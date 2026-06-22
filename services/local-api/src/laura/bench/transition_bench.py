"""Eval harness: compare candidate VLMs on transition-smoothness labelling (Plan C / spec §7).

Runs each candidate model over the boundaries of a timeline whose cuts you've hand-labelled
(``gold``), and reports per-model **label agreement** vs the gold standard plus **latency per
cut** — so you can pick the default model your 12 GB VRAM runs well. The model runs are manual
(need a local Ollama with the models pulled + real media); the scoring math is pure and tested.

Run:  ``uv run python -m laura.bench.transition_bench <workspace.db> <timeline_id> <gold.json>``
where ``gold.json`` maps ``"<boundary_seq_frame>": "<label>"``.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..analysis.transition_review import enumerate_boundaries, extract_frames, frame_strip_plan
from ..analysis.vlm_ollama import OllamaVlmBackend
from ..db import repos
from ..db.database import SqliteDatabase

CANDIDATE_MODELS: tuple[str, ...] = ("qwen3-vl:8b", "qwen3-vl:4b", "smolvlm2:2.2b")


@dataclass(frozen=True)
class BenchResult:
    model: str
    agreement: float  # fraction of boundaries whose label matched gold
    mean_latency_ms: float
    n: int


def label_agreement(predicted: list[str], gold: list[str]) -> float:
    """Fraction of positions where ``predicted`` matches ``gold`` (0.0 when nothing to compare)."""
    pairs = list(zip(predicted, gold, strict=False))
    if not pairs:
        return 0.0
    return sum(1 for p, g in pairs if p == g) / len(pairs)


def bench_model(
    db: SqliteDatabase, timeline_id: str, gold: dict[int, str], model: str, *, k: int = 6
) -> BenchResult:
    """Run one model over the timeline's boundaries that have a gold label; score agreement+latency.

    Requires a local Ollama with ``model`` pulled and the timeline's proxies on disk."""
    backend = OllamaVlmBackend(model=model)
    boundaries = [b for b in enumerate_boundaries(db, timeline_id) if b.seq_out_a in gold]
    predicted: list[str] = []
    gold_labels: list[str] = []
    latencies: list[float] = []
    proxy_paths: dict[str, str] = {}
    for b in boundaries:
        for asset_id in (b.asset_a, b.asset_b):
            if asset_id not in proxy_paths:
                for f in repos.list_asset_files(db, asset_id):
                    if f.get("is_proxy"):
                        proxy_paths[asset_id] = str(f["path"])
                        break
        frames = extract_frames(proxy_paths, frame_strip_plan(b, k), rate_num=30, rate_den=1)
        meta: dict[str, object] = {
            "same_source": b.same_source,
            "removed_gap_frames": b.removed_gap_frames,
            "k": k,
        }
        t0 = time.perf_counter()
        verdict = backend.review(frames, meta)
        latencies.append((time.perf_counter() - t0) * 1000)
        predicted.append(verdict.label)
        gold_labels.append(gold[b.seq_out_a])
    mean_latency = sum(latencies) / len(latencies) if latencies else 0.0
    return BenchResult(
        model, label_agreement(predicted, gold_labels), mean_latency, len(boundaries)
    )


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write("usage: transition_bench <db_path> <timeline_id> <gold.json>\n")
        return 2
    db_path, timeline_id, gold_path = argv
    db = SqliteDatabase(Path(db_path))
    gold = {
        int(k): str(v) for k, v in json.loads(Path(gold_path).read_text(encoding="utf-8")).items()
    }
    results = [bench_model(db, timeline_id, gold, m) for m in CANDIDATE_MODELS]
    results.sort(key=lambda r: (-r.agreement, r.mean_latency_ms))
    sys.stdout.write(f"{'model':22} {'agreement':>10} {'ms/cut':>10} {'n':>4}\n")
    for r in results:
        sys.stdout.write(f"{r.model:22} {r.agreement:>10.2%} {r.mean_latency_ms:>10.0f} {r.n:>4}\n")
    if results:
        sys.stdout.write(f"\nrecommended default: {results[0].model}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
