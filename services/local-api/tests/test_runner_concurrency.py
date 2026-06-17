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
            done = conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE status='succeeded'"
            ).fetchone()["c"]
        if done == 6:
            break
        time.sleep(0.05)
    runner.stop()

    assert sorted(state["ran"]) == sorted(ids)  # each ran exactly once
    assert state["peak"] >= 2                    # genuine concurrency
