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
