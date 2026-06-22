# Async-Pipeline + GPU-Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the ingest/analysis pipeline fast by running multiple job workers concurrently and using the GPU (NVENC proxy, CUDA Whisper, CUDA TransNet) — without lowering proxy resolution.

**Architecture:** A new `gpu.py` util detects NVENC/CUDA (cached, tolerant). `proxy.py`/`asr.py`/`transnet.py` pick GPU paths when available with clean CPU fallback. `JobRunner` gains a runner-managed auto-heartbeat (keeps long jobs' leases fresh) and a `concurrency` parameter that starts N worker threads; the desktop runs 3 by default. `claim_job` is already atomic and SQLite is already WAL, so concurrent workers are safe.

**Tech Stack:** Python 3.11/uv/pytest · faster-whisper (ctranslate2) · torch (TransNetV2) · ffmpeg (NVENC) · SQLite (WAL) · FastAPI.

## Global Constraints

- `PROXY_MAX_HEIGHT = 1080` stays — **never downscale** below the existing cap.
- Heavy models/GPU stay optional: every GPU probe returns `False` on any error → CPU/libx264 fallback; backend must start and run without GPU/extras.
- No `print` in committed code → `logging.getLogger(__name__)`. Typing strict (mypy), `ruff` clean.
- Verification: `uv run pytest` green; proxy verified with a real `ffprobe`.
- Collision rules: never touch `api/timelines.py`, `RoughCutView.tsx`, `Player.tsx`, `analysis/refine.py`, `shots.py`. Pathspec commits only; never stage `uv.lock`/`.claude`/build artifacts.
- Paths below are relative to `services/local-api/`. Run pytest from `services/local-api/` via `uv run pytest`.

---

## File Structure

- **Create:** `src/laura/gpu.py` (detection util) · `tests/test_gpu.py` · `tests/test_runner_concurrency.py`
- **Modify:** `src/laura/ingest/proxy.py` (NVENC) · `src/laura/analysis/asr.py` (CUDA device) · `src/laura/analysis/transnet.py` (CUDA load) · `src/laura/jobs/runner.py` (auto-heartbeat + concurrency) · `src/laura/config.py` (`worker_concurrency`) · `src/laura/main.py` (pass concurrency)
- **Extend tests:** `tests/test_proxy.py` (or create if absent) · `tests/test_asr.py` (or create if absent)

---

## Task 1 — GPU detection util

**Files:** Create `src/laura/gpu.py`, `tests/test_gpu.py`

**Interfaces:**
- Produces: `nvenc_available() -> bool`, `asr_cuda_available() -> bool`, `torch_cuda_available() -> bool` (each cached, never raises).

- [ ] **Step 1 — Failing test** (`tests/test_gpu.py`):

```python
import subprocess
import laura.gpu as gpu


def _clear():
    gpu.nvenc_available.cache_clear()
    gpu.asr_cuda_available.cache_clear()
    gpu.torch_cuda_available.cache_clear()


def test_nvenc_available_true_when_encoder_listed(monkeypatch):
    _clear()
    monkeypatch.setattr(
        gpu.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=" V..... h264_nvenc NVIDIA", stderr=""),
    )
    assert gpu.nvenc_available() is True


def test_nvenc_available_false_when_absent(monkeypatch):
    _clear()
    monkeypatch.setattr(
        gpu.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=" V..... libx264 only", stderr=""),
    )
    assert gpu.nvenc_available() is False


def test_nvenc_available_false_on_error(monkeypatch):
    _clear()
    def boom(*a, **k):
        raise FileNotFoundError("no ffmpeg")
    monkeypatch.setattr(gpu.subprocess, "run", boom)
    assert gpu.nvenc_available() is False
```

- [ ] **Step 2 — Run, expect fail.** `uv run pytest tests/test_gpu.py -v` → ImportError / no module `laura.gpu`.

- [ ] **Step 3 — Implement** (`src/laura/gpu.py`):

```python
"""GPU capability detection (cached, tolerant). Every probe returns False on any
error so the backend runs unchanged without a GPU or the heavy extras."""
from __future__ import annotations

import logging
import subprocess
from functools import lru_cache

from .ingest.ffmpeg import ffmpeg_bin

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def nvenc_available() -> bool:
    """True if this ffmpeg build exposes the h264_nvenc encoder."""
    try:
        proc = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=15,
        )  # noqa: S603
    except Exception:  # noqa: BLE001 - ffmpeg missing / timeout -> no NVENC
        return False
    ok = "h264_nvenc" in (proc.stdout or "")
    logger.info("nvenc_available=%s", ok)
    return ok


@lru_cache(maxsize=1)
def asr_cuda_available() -> bool:
    """True if ctranslate2 (faster-whisper backend) sees a CUDA device."""
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:  # noqa: BLE001
        return False


@lru_cache(maxsize=1)
def torch_cuda_available() -> bool:
    """True if torch reports an available CUDA device (TransNet scene path)."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False
```

- [ ] **Step 4 — Run, expect pass.** `uv run pytest tests/test_gpu.py -v`
- [ ] **Step 5 — Commit.** `git add src/laura/gpu.py tests/test_gpu.py && git commit -m "feat(perf): GPU capability detection util (nvenc/cuda)"`

---

## Task 2 — Proxy via NVENC (no downscale)

**Files:** Modify `src/laura/ingest/proxy.py`; Create/extend `tests/test_proxy.py`

**Interfaces:**
- Consumes: `laura.gpu.nvenc_available`
- Produces: `build_proxy(...)` unchanged signature; internally selects encoder.

- [ ] **Step 1 — Failing test** (`tests/test_proxy.py`):

```python
import laura.ingest.proxy as proxy


def _capture(monkeypatch):
    calls = {}
    monkeypatch.setattr(proxy, "run_ffmpeg", lambda args, **k: calls.setdefault("args", args))
    return calls


def test_build_proxy_uses_nvenc_when_available(monkeypatch, tmp_path):
    monkeypatch.delenv("LAURA_PROXY_ENCODER", raising=False)
    monkeypatch.setattr(proxy, "nvenc_available", lambda: True)
    calls = _capture(monkeypatch)
    proxy.build_proxy("in.mp4", tmp_path / "p.mp4", src_height=1080, rate_num=30, rate_den=1)
    assert "h264_nvenc" in calls["args"]
    assert "scale=-2:1080" in calls["args"]  # no downscale of a 1080p source


def test_build_proxy_falls_back_to_libx264(monkeypatch, tmp_path):
    monkeypatch.delenv("LAURA_PROXY_ENCODER", raising=False)
    monkeypatch.setattr(proxy, "nvenc_available", lambda: False)
    calls = _capture(monkeypatch)
    proxy.build_proxy("in.mp4", tmp_path / "p.mp4", src_height=1080)
    assert "libx264" in calls["args"]


def test_build_proxy_env_override_forces_libx264(monkeypatch, tmp_path):
    monkeypatch.setenv("LAURA_PROXY_ENCODER", "libx264")
    monkeypatch.setattr(proxy, "nvenc_available", lambda: True)
    calls = _capture(monkeypatch)
    proxy.build_proxy("in.mp4", tmp_path / "p.mp4", src_height=1080)
    assert "libx264" in calls["args"]
    assert "h264_nvenc" not in calls["args"]
```

- [ ] **Step 2 — Run, expect fail.** `uv run pytest tests/test_proxy.py -v` → `AttributeError: module 'laura.ingest.proxy' has no attribute 'nvenc_available'`.

- [ ] **Step 3 — Implement.** In `src/laura/ingest/proxy.py`, add imports + encoder helper and use it in `build_proxy`:

```python
import logging
import os

from .ffmpeg import run_ffmpeg
from ..gpu import nvenc_available

logger = logging.getLogger(__name__)
```

Add helper (after `proxy_target_height`):

```python
def _video_encoder_args() -> tuple[str, list[str]]:
    """(name, ffmpeg video-codec args). NVENC keeps full resolution but encodes on the
    GPU (the slow CPU step otherwise). Override via LAURA_PROXY_ENCODER=auto|nvenc|libx264."""
    choice = (os.environ.get("LAURA_PROXY_ENCODER") or "auto").strip().lower()
    use_nvenc = choice == "nvenc" or (choice == "auto" and nvenc_available())
    if use_nvenc:
        return "h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23"]
    return "libx264", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
```

Replace the codec block inside `build_proxy` (the `args += ["-c:v", "libx264", ...]` part) with:

```python
    enc_name, venc = _video_encoder_args()
    logger.info("proxy encoder=%s target_h=%d", enc_name, target_h)
    args = ["-i", str(src), "-vf", f"scale=-2:{target_h}"]
    if rate_num and rate_den:
        args += ["-r", f"{rate_num}/{rate_den}"]  # force CFR
    args += [
        *venc,
        "-g", "1",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(dest),
    ]
    run_ffmpeg(args)
```

- [ ] **Step 4 — Run, expect pass.** `uv run pytest tests/test_proxy.py -v`
- [ ] **Step 5 — Commit.** `git add src/laura/ingest/proxy.py tests/test_proxy.py && git commit -m "feat(perf): NVENC proxy encode when available, full-res, libx264 fallback"`

---

## Task 3 — Whisper ASR on CUDA when available

**Files:** Modify `src/laura/analysis/asr.py`; Create/extend `tests/test_asr.py`

**Interfaces:**
- Consumes: `laura.gpu.asr_cuda_available`
- Produces: `resolve_asr_device(device: str | None = None) -> str`; `transcribe(...)` unchanged signature.

- [ ] **Step 1 — Failing test** (`tests/test_asr.py`):

```python
import sys
import types

import laura.analysis.asr as asr


def test_resolve_device_prefers_cuda_when_available(monkeypatch):
    monkeypatch.delenv("LAURA_ASR_DEVICE", raising=False)
    monkeypatch.setattr(asr, "asr_cuda_available", lambda: True)
    assert asr.resolve_asr_device() == "cuda"


def test_resolve_device_cpu_when_no_cuda(monkeypatch):
    monkeypatch.delenv("LAURA_ASR_DEVICE", raising=False)
    monkeypatch.setattr(asr, "asr_cuda_available", lambda: False)
    assert asr.resolve_asr_device() == "cpu"


def test_resolve_device_env_override(monkeypatch):
    monkeypatch.setenv("LAURA_ASR_DEVICE", "cpu")
    monkeypatch.setattr(asr, "asr_cuda_available", lambda: True)
    assert asr.resolve_asr_device() == "cpu"


def test_run_uses_float16_on_cuda(monkeypatch):
    captured = {}

    class FakeModel:
        def __init__(self, model_size, device, compute_type):
            captured["device"] = device
            captured["compute_type"] = compute_type

        def transcribe(self, *a, **k):
            return ([], object())

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)

    asr._run("a.wav", "base", None, "cuda")
    assert captured == {"device": "cuda", "compute_type": "float16"}
    asr._run("a.wav", "base", None, "cpu")
    assert captured == {"device": "cpu", "compute_type": "int8"}
```

- [ ] **Step 2 — Run, expect fail.** `uv run pytest tests/test_asr.py -v` → `AttributeError: ... has no attribute 'resolve_asr_device'`.

- [ ] **Step 3 — Implement.** In `src/laura/analysis/asr.py`:

Add imports + logger near the top (after `import os`):

```python
import logging

from ..gpu import asr_cuda_available

logger = logging.getLogger(__name__)
```

Add the resolver:

```python
def resolve_asr_device(device: str | None = None) -> str:
    """Pick the ASR device: explicit arg > LAURA_ASR_DEVICE > CUDA-if-available > cpu."""
    return device or os.environ.get("LAURA_ASR_DEVICE") or ("cuda" if asr_cuda_available() else "cpu")
```

Change `_run` to pick compute_type by device + log:

```python
def _run(
    audio_path: Path | str, model_size: str, language: str | None, device: str
) -> list[SegmentResult]:
    from faster_whisper import WhisperModel

    compute_type = "float16" if device == "cuda" else "int8"
    logger.info("ASR device=%s compute_type=%s model=%s", device, compute_type, model_size)
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(str(audio_path), word_timestamps=True, language=language)
    # ... (rest unchanged)
```

Change `transcribe`'s device line from `chosen = device or os.environ.get("LAURA_ASR_DEVICE") or "auto"` to:

```python
    chosen = resolve_asr_device(device)
```

(The existing `try: _run(... chosen) except: _run(... "cpu")` fallback stays — if `chosen=="cuda"` errors at runtime, it retries on CPU.)

- [ ] **Step 4 — Run, expect pass.** `uv run pytest tests/test_asr.py -v`
- [ ] **Step 5 — Commit.** `git add src/laura/analysis/asr.py tests/test_asr.py && git commit -m "feat(perf): Whisper ASR on CUDA (float16) when available, logged, CPU fallback"`

---

## Task 4 — TransNet scene model on CUDA (defensive)

**Files:** Modify `src/laura/analysis/transnet.py`; extend `tests/` (add `tests/test_transnet_device.py`)

**Interfaces:**
- Consumes: `laura.gpu.torch_cuda_available`

- [ ] **Step 1 — Failing test** (`tests/test_transnet_device.py`):

```python
import laura.analysis.transnet as tn


def test_load_model_moves_to_cuda_when_available(monkeypatch):
    moved = {}

    class FakeModel:
        def eval(self):
            return self
        def to(self, dev):
            moved["dev"] = dev
            return self

    fake_pkg = type("P", (), {"TransNetV2": FakeModel})
    import sys, types
    mod = types.ModuleType("transnetv2_pytorch")
    mod.TransNetV2 = FakeModel
    monkeypatch.setitem(sys.modules, "transnetv2_pytorch", mod)
    monkeypatch.setattr(tn, "torch_cuda_available", lambda: True)

    tn._load_model()
    assert moved.get("dev") == "cuda"


def test_load_model_stays_cpu_without_cuda(monkeypatch):
    moved = {}

    class FakeModel:
        def eval(self):
            return self
        def to(self, dev):
            moved["dev"] = dev
            return self

    import sys, types
    mod = types.ModuleType("transnetv2_pytorch")
    mod.TransNetV2 = FakeModel
    monkeypatch.setitem(sys.modules, "transnetv2_pytorch", mod)
    monkeypatch.setattr(tn, "torch_cuda_available", lambda: False)

    tn._load_model()
    assert "dev" not in moved
```

- [ ] **Step 2 — Run, expect fail.** `uv run pytest tests/test_transnet_device.py -v` → `AttributeError: ... has no attribute 'torch_cuda_available'`.

- [ ] **Step 3 — Implement.** In `src/laura/analysis/transnet.py`, add import near the top:

```python
from ..gpu import torch_cuda_available
```

In `_load_model`, after the `eval()` block and before `return model`, add a defensive CUDA move:

```python
    if torch_cuda_available():
        to_fn = getattr(model, "to", None)
        if callable(to_fn):
            try:
                model = to_fn("cuda") or model
            except Exception:  # noqa: BLE001 - stay on CPU if the move fails
                pass
    return model
```

- [ ] **Step 4 — Run, expect pass.** `uv run pytest tests/test_transnet_device.py -v`
- [ ] **Step 5 — Commit.** `git add src/laura/analysis/transnet.py tests/test_transnet_device.py && git commit -m "feat(perf): load TransNet scene model on CUDA when available (defensive)"`

---

## Task 5 — Runner-managed auto-heartbeat

**Files:** Modify `src/laura/jobs/runner.py`; Create `tests/test_runner_concurrency.py` (heartbeat test here; concurrency test added in Task 6)

**Interfaces:**
- Produces: `JobRunner._execute` keeps a job's lease fresh for its whole duration via a daemon thread (no handler changes needed).

- [ ] **Step 1 — Failing test** (`tests/test_runner_concurrency.py`):

```python
import threading
import time

from laura.jobs.runner import JobRunner, enqueue
# Uses the shared `db` fixture from tests/conftest.py (a migrated temp SqliteDatabase).


def test_long_job_is_not_reaped_thanks_to_auto_heartbeat(db):
    ran = {"done": False}

    def slow(ctx):
        time.sleep(2.5)  # longer than lease_seconds below
        ran["done"] = True
        return {"ok": True}

    runner = JobRunner(db, {"slow": slow}, lease_seconds=2)
    job_id = enqueue(db, queue="ingest.io", kind="slow")

    # Run the job in a background thread; meanwhile reap from the "main" worker.
    t = threading.Thread(target=runner.run_once, daemon=True)
    t.start()
    time.sleep(2.2)  # past the 2s lease — without heartbeat the reaper would grab it
    reaped = runner.reap_expired()
    t.join(timeout=5)

    assert ran["done"] is True
    with db.connection() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "succeeded"
    assert reaped == 0  # the running job's lease was kept fresh
```

- [ ] **Step 2 — Run, expect fail.** `uv run pytest tests/test_runner_concurrency.py -v` → the job is reaped (`reaped >= 1`) / status not succeeded, because no auto-heartbeat exists yet.

- [ ] **Step 3 — Implement.** In `src/laura/jobs/runner.py`, add `import threading` (already imported) and rewrite the handler-execution part of `_execute` to wrap it with an auto-heartbeat:

```python
    def _execute(self, job: dict[str, Any]) -> None:
        kind = str(job["kind"])
        with span("job.execute", **{"job.kind": kind, "job.queue": str(job["queue"])}) as sp:
            handler = self.registry.get(kind)
            if handler is None:
                sp.set_attribute("job.status", "failed")
                self._finish_fail(job, f"no handler registered for kind={kind!r}")
                JOBS.labels(kind, "failed").inc()
                return
            ctx = JobContext(
                job_id=str(job["id"]),
                kind=kind,
                queue=str(job["queue"]),
                payload=json.loads(job["payload_json"] or "{}"),
                db=self.db,
                lease_seconds=self.lease_seconds,
            )
            stop_hb = threading.Event()

            def _heartbeat_loop() -> None:
                interval = max(1.0, self.lease_seconds / 2)
                while not stop_hb.wait(interval):
                    try:
                        ctx.heartbeat()
                    except Exception:  # noqa: BLE001 - heartbeat is best-effort
                        pass

            hb = threading.Thread(target=_heartbeat_loop, name=f"hb-{str(job['id'])[:8]}", daemon=True)
            hb.start()
            try:
                result = handler(ctx)
            except Exception as exc:  # noqa: BLE001 - we record any handler failure
                sp.set_attribute("job.status", "failed")
                self._finish_fail(job, f"{type(exc).__name__}: {exc}")
                JOBS.labels(kind, "failed").inc()
                return
            finally:
                stop_hb.set()
                hb.join(timeout=2.0)
            sp.set_attribute("job.status", "succeeded")
            self._finish_ok(str(job["id"]), result)
            JOBS.labels(kind, "succeeded").inc()
```

- [ ] **Step 4 — Run, expect pass.** `uv run pytest tests/test_runner_concurrency.py -v`
- [ ] **Step 5 — Commit.** `git add src/laura/jobs/runner.py tests/test_runner_concurrency.py && git commit -m "feat(perf): runner-managed auto-heartbeat keeps long jobs' leases fresh"`

---

## Task 6 — Multi-worker concurrency

**Files:** Modify `src/laura/jobs/runner.py`, `src/laura/config.py`, `src/laura/main.py`; extend `tests/test_runner_concurrency.py`

**Interfaces:**
- Consumes: auto-heartbeat from Task 5 (so concurrent reapers don't kill in-flight jobs).
- Produces: `JobRunner(db, registry, *, concurrency: int = 1, ...)`; `Settings.worker_concurrency: int`.

- [ ] **Step 1 — Failing test** (append to `tests/test_runner_concurrency.py`):

```python
def test_pool_runs_each_job_once_with_overlap(db):
    lock = threading.Lock()
    state = {"live": 0, "peak": 0, "ran": []}

    def work(ctx):
        with lock:
            state["live"] += 1
            state["peak"] = max(state["peak"], state["live"])
        time.sleep(0.3)
        with lock:
            state["live"] -= 1
            state["ran"].append(ctx.job_id)
        return {"ok": True}

    runner = JobRunner(db, {"work": work}, lease_seconds=30, concurrency=4, poll_interval=0.02)
    ids = [enqueue(db, queue="ingest.io", kind="work") for _ in range(6)]
    runner.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        with db.connection() as conn:
            done = conn.execute("SELECT COUNT(*) c FROM jobs WHERE status='succeeded'").fetchone()["c"]
        if done == 6:
            break
        time.sleep(0.05)
    runner.stop()

    assert sorted(state["ran"]) == sorted(ids)  # each ran exactly once
    assert state["peak"] >= 2                    # genuine concurrency
```

- [ ] **Step 2 — Run, expect fail.** `uv run pytest tests/test_runner_concurrency.py::test_pool_runs_each_job_once_with_overlap -v` → `TypeError: __init__() got an unexpected keyword argument 'concurrency'`.

- [ ] **Step 3 — Implement.**

(a) `src/laura/jobs/runner.py` — add `concurrency` param, multi-thread start/stop. Change the constructor signature and the thread fields:

```python
    def __init__(
        self,
        db: Database,
        registry: dict[str, JobHandler] | None = None,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 60,
        poll_interval: float = 0.5,
        queues: tuple[str, ...] | None = None,
        concurrency: int = 1,
    ) -> None:
        self.db = db
        self.registry: dict[str, JobHandler] = registry or {}
        self.worker_id = worker_id or f"worker-{new_id()[:8]}"
        self.lease_seconds = lease_seconds
        self.poll_interval = poll_interval
        self.queues = queues
        self.concurrency = max(1, concurrency)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
```

Replace `start()` and `stop()`:

```python
    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, name=f"laura-job-runner-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads = []
```

(b) `src/laura/config.py` — add the field (after `lease_seconds`):

```python
    worker_concurrency: int = 3       # background job-runner threads (desktop)
```

and in `load()` (inside the `return cls(...)`), add:

```python
            worker_concurrency=int(os.environ.get("LAURA_WORKERS", "3")),
```

(c) `src/laura/main.py` — pass it (line ~60):

```python
    runner = JobRunner(
        db, registry,
        lease_seconds=settings.lease_seconds,
        concurrency=settings.worker_concurrency,
    )
```

- [ ] **Step 4 — Run, expect pass.** `uv run pytest tests/test_runner_concurrency.py -v`
- [ ] **Step 5 — Commit.** `git add src/laura/jobs/runner.py src/laura/config.py src/laura/main.py tests/test_runner_concurrency.py && git commit -m "feat(perf): multi-worker job pool (LAURA_WORKERS, default 3)"`

---

## Task 7 — Full verification + adversarial review

**Files:** none (verification only)

- [ ] **Step 1 — Full suite + lint + types.**

Run: `cd services/local-api && uv run pytest && uv run ruff check . && uv run mypy src`
Expected: all green (pre-existing unrelated failures, if any, noted but not introduced by this plan).

- [ ] **Step 2 — Real proxy ffprobe.** Build a proxy from a real ≥1080p fixture (or the imported test asset) and confirm it stays full height and is valid video:

Run an ad-hoc check that `build_proxy(...)` output via `ffprobe` has `height == src_height` (no downscale) and one h264 video stream.

- [ ] **Step 3 — Adversarial code review.** Dispatch a code-reviewer subagent over the diff (`git diff main...HEAD` for the runner/config/main + gpu/proxy/asr/transnet changes). Focus: runner concurrency (double-claim, thread join, stop() correctness), auto-heartbeat thread lifecycle (no leak on handler exception), GPU-probe tolerance, NVENC arg correctness, CPU/libx264 fallbacks. Fix any Critical/Important findings, re-run pytest.

- [ ] **Step 4 — Live re-test.** Launch the app (`LAURA_REMOTE_DEBUG=9222 npm --prefix apps/desktop start`), import the 29-min test video (`https://www.youtube.com/watch?v=8D49WRH9hMg`), run analysis, and measure end-to-end time. Confirm via logs that `proxy encoder=h264_nvenc` and `ASR device=cuda` were used, and that total time dropped from "many minutes" to roughly 1–2 min. Clean up app processes by PID/port afterwards (never the user's Vibemind python processes).
