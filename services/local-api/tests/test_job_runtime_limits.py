"""Long-running job kinds need their own runtime cap.

Live finding: three ``production.run`` jobs (an agent team that legitimately works for
over an hour) were cut off by the single 3600s cap that also guards quick jobs, where an
hour would mean a hang. Board checkpoints made each death survivable, but every one cost
a manual nudge. The cap must be per-kind, not one number for everything.
"""

from __future__ import annotations

from pathlib import Path

from laura.db.database import SqliteDatabase
from laura.jobs.runner import JobRunner


def _runner(tmp_path: Path, **kwargs: object) -> JobRunner:
    db = SqliteDatabase(tmp_path / "t.db")
    return JobRunner(db, **kwargs)  # type: ignore[arg-type]


def test_unlisted_kind_uses_the_global_cap(tmp_path: Path) -> None:
    runner = _runner(tmp_path, max_runtime_seconds=3600)
    assert runner.runtime_limit_for("ingest.probe") == 3600


def test_override_wins_for_its_kind(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        max_runtime_seconds=3600,
        runtime_overrides={"production.run": 14400},
    )
    assert runner.runtime_limit_for("production.run") == 14400
    # everything else keeps the strict global cap — an hour-long probe IS a hang
    assert runner.runtime_limit_for("shorts.render") == 3600


def test_no_overrides_is_the_old_behaviour(tmp_path: Path) -> None:
    runner = _runner(tmp_path, max_runtime_seconds=120)
    assert runner.runtime_limit_for("anything") == 120
    assert runner.runtime_overrides == {}
