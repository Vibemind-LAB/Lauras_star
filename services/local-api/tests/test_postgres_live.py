"""Live PostgreSQL backend tests. Run only when LAURA_TEST_PG_DSN points at a DB.

    LAURA_TEST_PG_DSN=postgresql://laura:laura@localhost:5433/laura uv run pytest \
        tests/test_postgres_live.py

Exercises the real PG path: portable migrations, ?->%s translation, FOR UPDATE
SKIP LOCKED job claim, and RBAC/audit repos against an actual server.
"""

from __future__ import annotations

import os

import pytest

DSN = os.environ.get("LAURA_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="set LAURA_TEST_PG_DSN for live Postgres tests")

pytest.importorskip("psycopg")

from laura.auth.keys import generate_api_key  # noqa: E402
from laura.db import repos  # noqa: E402
from laura.db.postgres import PostgresDatabase  # noqa: E402
from laura.jobs import JobRunner, default_registry, enqueue  # noqa: E402

_TABLES = [
    "audit_events", "api_keys", "memberships", "users", "organizations", "exports",
    "timeline_clips", "timelines", "transcript_words", "transcript_segments", "speakers",
    "shots", "analysis_runs", "asset_files", "media_assets", "projects", "jobs", "schema_meta",
]


@pytest.fixture
def pg() -> PostgresDatabase:
    assert DSN
    db = PostgresDatabase(DSN)
    with db.connection() as conn:
        for table in _TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    db.migrate()
    return db


def test_pg_migrations_apply(pg: PostgresDatabase) -> None:
    assert pg.schema_version() == 3


def test_pg_project_crud(pg: PostgresDatabase) -> None:
    project = repos.create_project(
        pg, name="PG", rate_num=30000, rate_den=1001, drop_frame=True, workspace_root="/tmp/x"
    )
    got = repos.get_project(pg, project["id"])
    assert got is not None and got["name"] == "PG"
    assert any(p["id"] == project["id"] for p in repos.list_projects(pg))


def test_pg_job_claim_and_run(pg: PostgresDatabase) -> None:
    job_id = enqueue(pg, queue="x", kind="echo", payload={"a": 1})
    runner = JobRunner(pg, default_registry())
    assert runner.run_once() is True
    job = repos.get_job(pg, job_id)
    assert job is not None and job["status"] == "succeeded"


def test_pg_idempotency(pg: PostgresDatabase) -> None:
    a = enqueue(pg, queue="x", kind="echo", idempotency_key="k1")
    b = enqueue(pg, queue="x", kind="echo", idempotency_key="k1")
    assert a == b


def test_pg_rbac_and_audit(pg: PostgresDatabase) -> None:
    org = repos.create_org(pg, name="Acme")
    user = repos.create_user(pg, email="a@example.com")
    repos.add_membership(pg, org_id=org["id"], user_id=user["id"], role="editor")
    _full, prefix, key_hash = generate_api_key()
    repos.create_api_key(
        pg, org_id=org["id"], user_id=user["id"], name="k", prefix=prefix,
        key_hash=key_hash, role="editor",
    )
    got = repos.get_api_key_by_hash(pg, key_hash)
    assert got is not None and got["role"] == "editor"

    repos.insert_audit_event(
        pg, org_id=org["id"], principal_kind="key", principal_id=user["id"],
        action="project.create", entity_type="project", entity_id="p1", payload={"x": 1},
    )
    assert any(e["action"] == "project.create" for e in repos.list_audit_events(pg))
