"""Per-shot quality metrics for video-driven rough-cut filtering.

Deterministic and CPU-only: sample a few small grayscale frames per shot via ffmpeg and
score them with numpy. No OpenCV, no GPU. Used to drop black / frozen / duplicate / blurry
shots when building a rough cut from scenes.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

SAMPLE_W, SAMPLE_H = 64, 36
SAMPLE_K = 5
BLACK_LUMA = 16.0
# A frame is only "black" when it is *uniformly* dark — its brightest pixel is also dark.
# A spotlight subject on a dark stage has mean~15 but max>>48, so it is NOT black.
BLACK_MAX = 48.0


def _shot_sample_indices(src_in: int, src_out: int, k: int = SAMPLE_K) -> list[int]:
    """The frame indices sampled for a shot's metrics — the single source of truth shared by
    the per-shot :func:`_sample_gray_frames` and the batched :func:`batch_shot_metrics`."""
    n = max(1, src_out - src_in)
    count = min(k, n)
    return sorted({src_in + (i * n) // count for i in range(count)})


def _sample_gray_frames(
    video: Path | str, src_in: int, src_out: int, *, k: int = SAMPLE_K,
    w: int = SAMPLE_W, h: int = SAMPLE_H,
) -> list[np.ndarray]:
    """Extract up to ``k`` evenly-spaced grayscale frames of [src_in, src_out) as HxW uint8."""
    idxs = _shot_sample_indices(src_in, src_out, k)
    expr = "+".join(f"eq(n\\,{i})" for i in idxs)
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video),
        "-vf", f"select='{expr}',scale={w}:{h},format=gray",
        "-vsync", "0", "-frames:v", str(len(idxs)), "-f", "rawvideo", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    frame_bytes = w * h
    frames: list[np.ndarray] = []
    for off in range(0, len(out) - frame_bytes + 1, frame_bytes):
        frames.append(np.frombuffer(out[off : off + frame_bytes], dtype=np.uint8).reshape(h, w))
    return frames


def static_score(frames: list[np.ndarray]) -> float:
    if len(frames) < 2:
        return 1.0
    diffs = [float(np.mean(np.abs(frames[i].astype(np.int16) - frames[i - 1])))
             for i in range(1, len(frames))]
    return max(0.0, min(1.0, 1.0 - (sum(diffs) / len(diffs)) / 255.0))


def dhash(frame: np.ndarray, *, hash_w: int = 9, hash_h: int = 8) -> str:
    """64-bit difference hash as 16 hex chars (resize then horizontal gradient sign)."""
    small = _resize_nearest(frame, hash_w, hash_h).astype(np.int16)
    bits = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _resize_nearest(frame: np.ndarray, w: int, h: int) -> np.ndarray:
    ys = (np.arange(h) * frame.shape[0] // h).clip(0, frame.shape[0] - 1)
    xs = (np.arange(w) * frame.shape[1] // w).clip(0, frame.shape[1] - 1)
    return frame[np.ix_(ys, xs)]


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _laplacian_var(frame: np.ndarray) -> float:
    f = frame.astype(np.float32)
    lap = (-4 * f
           + np.roll(f, 1, 0) + np.roll(f, -1, 0)
           + np.roll(f, 1, 1) + np.roll(f, -1, 1))
    return float(np.var(lap[1:-1, 1:-1]))


@dataclass(frozen=True)
class ShotMetrics:
    black_ratio: float
    static: float
    phash: str
    blur: float

    @classmethod
    def from_frames(cls, frames: list[np.ndarray]) -> ShotMetrics:
        if not frames:
            return cls(black_ratio=1.0, static=1.0, phash="0" * 16, blur=0.0)
        black = sum(
            1 for f in frames
            if float(np.mean(f)) < BLACK_LUMA and float(np.max(f)) < BLACK_MAX
        ) / len(frames)
        return cls(
            black_ratio=black,
            static=static_score(frames),
            phash=dhash(frames[len(frames) // 2]),
            blur=_laplacian_var(frames[len(frames) // 2]),
        )


def compute_shot_metrics(video: Path | str, src_in: int, src_out: int) -> ShotMetrics:
    return ShotMetrics.from_frames(_sample_gray_frames(video, src_in, src_out))


def _read_exact(stream: Any, n: int) -> bytes:
    """Read exactly ``n`` bytes from a pipe (which may return short reads), or fewer at EOF."""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def batch_shot_metrics(
    video: Path | str, shots: list[tuple[int, int]], *, k: int = SAMPLE_K,
    w: int = SAMPLE_W, h: int = SAMPLE_H,
) -> list[ShotMetrics]:
    """Compute :class:`ShotMetrics` for many shots in a SINGLE decode pass.

    Equivalent to calling :func:`compute_shot_metrics` per shot — it samples the *identical*
    frame indices, so metrics (and thus keep/drop decisions) are unchanged — but it streams the
    whole video's grayscale frames once and distributes them to shots. That turns the per-shot
    ``select='eq(n,..)'`` (which decodes from frame 0 on every call → O(N²) across a long video)
    into one O(N) pass. The result is in the same order as ``shots``."""
    per_shot = [_shot_sample_indices(src_in, src_out, k) for (src_in, src_out) in shots]
    needed: set[int] = set()
    for idxs in per_shot:
        needed.update(idxs)
    if not needed:
        return [ShotMetrics.from_frames([]) for _ in shots]
    last = max(needed)
    frame_bytes = w * h
    frames: dict[int, np.ndarray] = {}
    proc = subprocess.Popen(
        [
            "ffmpeg", "-v", "error", "-i", str(video),
            "-vf", f"scale={w}:{h},format=gray", "-vsync", "0", "-f", "rawvideo", "-",
        ],
        stdout=subprocess.PIPE,
    )
    assert proc.stdout is not None
    try:
        idx = 0
        while idx <= last:
            buf = _read_exact(proc.stdout, frame_bytes)
            if len(buf) < frame_bytes:
                break  # EOF before the last needed frame
            if idx in needed:
                frames[idx] = np.frombuffer(buf, dtype=np.uint8).reshape(h, w)
            idx += 1
    finally:
        proc.stdout.close()
        proc.terminate()
        proc.wait()
    return [
        ShotMetrics.from_frames([frames[i] for i in idxs if i in frames]) for idxs in per_shot
    ]


@dataclass(frozen=True)
class KeepThresholds:
    black_ratio: float = 0.8
    static: float = 0.985
    blur: float = 5.0  # below = too blurry; 0 disables
    # A near-frozen shot is only "dead footage" when it is BRIEF (a freeze/glitch). Held
    # shots at least this long are intentional content (title cards, talking heads, slow
    # b-roll) and are KEPT despite low motion — otherwise static footage loses ~everything.
    static_max_drop_frames: int = 24


_DEFAULT_THRESHOLDS = KeepThresholds()


def decide_keep(
    m: ShotMetrics,
    *,
    length_frames: int | None = None,
    thresholds: KeepThresholds = _DEFAULT_THRESHOLDS,
) -> tuple[bool, str | None]:
    if m.black_ratio >= thresholds.black_ratio:
        return False, "black"
    if m.static >= thresholds.static and (
        length_frames is None or length_frames < thresholds.static_max_drop_frames
    ):
        return False, "static"
    if thresholds.blur > 0 and m.blur < thresholds.blur:
        return False, "blur"
    return True, None


def mark_duplicates(
    rows: list[dict[str, Any]],
    *,
    dup_hamming: int = 6,
    dup_hamming_frozen: int = 2,
    static: float = _DEFAULT_THRESHOLDS.static,
) -> None:
    """Set drop_reason='duplicate' on later shots whose phash is near an earlier kept shot.

    The tolerance depends on motion, because the same hash distance means different things.
    On moving footage a repeat never matches exactly — sensor noise alone costs a few bits —
    so ``dup_hamming`` bits of slack are needed to catch it. On near-frozen footage the
    opposite holds: a real repeat is pixel-identical (distance 0), while two DIFFERENT screens
    of the same app land only 6-8 bits apart, sharing nothing but dark chrome and a header
    band — at 9x8 the text that tells them apart is long gone. The slack that rescues camera
    duplicates therefore deletes distinct screens.

    Live finding (a 10-min screen recording): a five-minute prompt screen was dropped as a
    duplicate of an unrelated server picker at distance 6, exactly the threshold. Hashing at
    higher resolution does not separate them (measured: 256- and 1024-bit variants put the
    same pair at 9.8-12.5%, no better than 64-bit), so the tolerance is what has to give.
    When BOTH shots are near-frozen, demand a near-exact match instead. An unknown motion
    level keeps the tolerant threshold — metrics can fail, and that must not silently change
    the rule.
    """
    kept: list[tuple[str, bool]] = []
    for row in rows:
        if not row.get("keep") or not row.get("phash"):
            continue
        frozen = float(row.get("static_score") or 0.0) >= static
        if any(
            hamming(row["phash"], h) <= (dup_hamming_frozen if frozen and h_frozen else dup_hamming)
            for h, h_frozen in kept
        ):
            row["keep"] = False
            row["drop_reason"] = "duplicate"
        else:
            kept.append((row["phash"], frozen))
