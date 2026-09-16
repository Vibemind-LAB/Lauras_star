"""Ein importiertes Quellvideo erreicht den Objektspeicher -- auf JEDEM Importweg.

`test_render_publishes_to_storage.py` haelt die andere Haelfte der Kette fest: den
fertigen Render. Diese Datei gibt es, weil die Import-Haelfte in der Praxis tot war
und niemand es gemerkt hat.

Am 16.09.2026 an der laufenden Instanz gemessen: jedes vorhandene Asset trug
`object_key = NULL`. Der Upload stand in `_finalize_media_asset` und im httpx-Zweig
-- beides Wege, die etwas HERUNTERLADEN. Der Import per lokalem Pfad
(`POST /projects/{id}/assets/import` mit `source_path`) beruehrt keinen von beiden,
und genau so war jedes echte Asset importiert worden. Die Testsuite blieb gruen,
weil sie den Importzweig nie gegen einen Speicher gefahren hat.

Deshalb pruefen die Tests hier den SEAM, nicht den Client:

* der lokale Import -- der Weg, den alle echten Assets genommen haben -- laedt hoch;
* der Download-Zweig laedt danach GENAU EINMAL hoch, nicht zweimal;
* ein kaputter Bucket kostet den Import nichts;
* ohne Konfiguration wird nichts versucht.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.ingest.handlers import _finalize_media_asset, register_ingest_handlers
from laura.jobs import JobRunner, default_registry, enqueue
from laura.jobs.runner import JobContext

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="ffmpeg not available on PATH",
)


class _FakeStore:
    """Haelt fest, was hochgeladen werden sollte. Drei Zeilen, wie das Protocol es meint."""

    bucket = "laura"

    def __init__(self) -> None:
        self.aufrufe: list[tuple[str, Path, str]] = []

    def put(self, key: str, path: Path, *, content_type: str) -> str:
        self.aufrufe.append((key, path, content_type))
        return f"{self.bucket}/{key}"


class _BrokenStore(_FakeStore):
    def put(self, key: str, path: Path, *, content_type: str) -> str:
        raise OSError("bucket unreachable")


def _drain(runner: JobRunner, limit: int = 60) -> int:
    ran = 0
    while runner.run_once():
        ran += 1
        if ran >= limit:
            break
    return ran


def _aufbau(tmp_path: Path) -> tuple[SqliteDatabase, JobRunner, dict[str, Any], Path]:
    media = tmp_path / "a.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media),
    ])
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    proot = settings.workspace_root / "project"
    proot.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False, workspace_root=str(proot)
    )
    registry = default_registry()
    register_ingest_handlers(registry)
    return db, JobRunner(db, registry), project, media


def _lokaler_import(db: SqliteDatabase, runner: JobRunner, project: dict[str, Any],
                    media: Path) -> dict[str, Any]:
    """Genau das, was `POST /projects/{id}/assets/import` mit `source_path` tut."""
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name=media.name,
        source_path=str(media),
    )
    enqueue(
        db, queue="ingest.io", kind="ingest.probe",
        payload={"asset_id": asset["id"]}, idempotency_key=f"probe:{asset['id']}",
    )
    _drain(runner)
    fertig = repos.get_asset(db, asset["id"])
    assert fertig is not None
    return fertig


def test_a_local_path_import_is_published_and_recorded(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Der Weg, auf dem jedes echte Asset importiert wurde -- und der nie hochlud."""
    store = _FakeStore()
    monkeypatch.setattr("laura.ingest.handlers.store_from_env", lambda: store)

    db, runner, project, media = _aufbau(tmp_path)
    asset = _lokaler_import(db, runner, project, media)

    assert asset["object_key"] == f"laura/assets/{asset['id']}.mp4"
    assert Path(asset["source_path"]).exists(), "die lokale Datei bleibt liegen"
    hochgeladen = [a for a in store.aufrufe if a[0].startswith("assets/")]
    assert len(hochgeladen) == 1, f"genau ein Upload erwartet, war: {store.aufrufe}"
    key, pfad, content_type = hochgeladen[0]
    assert key == f"assets/{asset['id']}.mp4"
    assert pfad == media, "die Datei auf der Platte ist, was hochgeladen wird"
    assert content_type == "video/mp4"


def test_the_download_path_publishes_exactly_once(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Der Upload ist aus den Download-Zweigen in den Probe gewandert.

    Der Download-Zweig darf dadurch weder aufhoeren hochzuladen noch es doppelt tun:
    er reicht ueber seinen eigenen `enqueue` in denselben Probe hinein.
    """
    store = _FakeStore()
    monkeypatch.setattr("laura.ingest.handlers.store_from_env", lambda: store)

    db, runner, project, media = _aufbau(tmp_path)
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name=media.name,
        source_path=f"url:https://example.invalid/{media.name}",
    )
    ctx = JobContext(job_id="j", kind="ingest.fetch", queue="ingest.io",
                     payload={"asset_id": asset["id"]}, db=db)
    assert _finalize_media_asset(ctx, asset, media, full_scan=False) is True
    _drain(runner)

    fertig = repos.get_asset(db, asset["id"])
    assert fertig is not None
    assert fertig["object_key"] == f"laura/assets/{asset['id']}.mp4"
    assert len(store.aufrufe) == 1, f"genau ein Upload erwartet, war: {store.aufrufe}"


def test_a_failing_upload_leaves_the_import_intact(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Dieselbe Abwaegung wie beim Render: die Datei ist wertvoll, die Kopie bequem."""
    monkeypatch.setattr("laura.ingest.handlers.store_from_env", lambda: _BrokenStore())

    db, runner, project, media = _aufbau(tmp_path)
    asset = _lokaler_import(db, runner, project, media)

    assert asset["online"], "ein unerreichbarer Bucket darf den Import nicht kippen"
    assert asset["sha256"], "der Probe ist trotzdem durchgelaufen"
    assert asset["object_key"] is None, "fuer einen gescheiterten Upload kein Schluessel"


def test_storage_off_touches_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    """Die Vorgabe einer Desktop-Installation: kein Speicher, kein Schluessel, kein Versuch."""
    monkeypatch.delenv("LAURA_STORAGE_URL", raising=False)
    monkeypatch.delenv("LAURA_STORAGE_KEY", raising=False)

    db, runner, project, media = _aufbau(tmp_path)
    asset = _lokaler_import(db, runner, project, media)

    assert asset["online"]
    assert asset["object_key"] is None
