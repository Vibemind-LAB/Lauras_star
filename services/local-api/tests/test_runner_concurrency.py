import threading
import time
from typing import Any

from laura.db.database import Database
from laura.jobs.runner import JobContext, JobRunner, enqueue

# Uses the shared `db` fixture from tests/conftest.py (a migrated temp SqliteDatabase).


def test_long_job_is_not_reaped_thanks_to_auto_heartbeat(db: Database) -> None:
    ran = {"done": False}

    def slow(ctx: JobContext) -> dict[str, Any]:
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


def test_pool_runs_each_job_once_with_overlap(db: Database) -> None:
    lock = threading.Lock()
    state: dict[str, Any] = {"live": 0, "peak": 0, "ran": []}

    def work(ctx: JobContext) -> dict[str, Any]:
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
            done = conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE status='succeeded'"
            ).fetchone()["c"]
        if done == 6:
            break
        time.sleep(0.05)
    runner.stop()

    assert sorted(state["ran"]) == sorted(ids)  # each ran exactly once
    assert state["peak"] >= 2                    # genuine concurrency


def test_heartbeat_stops_after_max_runtime_so_wedged_job_is_reapable(db: Database) -> None:
    # A handler that blocks far longer than the max runtime: once the cap passes, the
    # auto-heartbeat must stop refreshing the lease so the reaper can reap it.
    def wedged(ctx: JobContext) -> dict[str, Any]:
        time.sleep(6)
        return {"ok": True}

    runner = JobRunner(db, {"wedged": wedged}, lease_seconds=2, max_runtime_seconds=2)
    enqueue(db, queue="ingest.io", kind="wedged")

    t = threading.Thread(target=runner.run_once, daemon=True)
    t.start()
    time.sleep(4.5)  # > max_runtime(2) + lease(2): heartbeat has stopped, lease expired
    reaped = runner.reap_expired()
    assert reaped >= 1  # the wedged job's lease was NOT kept fresh past the cap
    t.join(timeout=8)
