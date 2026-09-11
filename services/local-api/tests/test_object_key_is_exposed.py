"""Die Objekt-Adresse muss Laura verlassen koennen.

`exports.object_key` und `media_assets.object_key` werden beim Render und beim
Import gefuellt (siehe `laura/storage.py`), aber ein Wert in der Datenbank nuetzt
niemandem: marketing und sales-claw sehen Laura ausschliesslich ueber ihre HTTP-API.
Stand vor diesen Tests war die Spalte in KEINEM Antwortmodell — der Objektspeicher
war fuer jeden Konsumenten unsichtbar, und der Steckbrief sagte weiterhin nur, DASS
ein Video existiert.

`None` ist der Normalfall und muss es bleiben: eine Desktop-Installation ohne
Objektspeicher darf nicht anders aussehen als vorher, nur eben mit einem leeren Feld.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database


def _projekt(client: TestClient) -> str:
    antwort = client.post(
        "/projects",
        json={"name": "p", "rate_num": 30, "rate_den": 1, "drop_frame": False},
    )
    assert antwort.status_code in (200, 201), antwort.text
    return str(antwort.json()["id"])


def test_asset_carries_its_object_key(client: TestClient, db: Database) -> None:
    projekt = _projekt(client)
    asset = repos.create_asset(
        db, project_id=projekt, type="video", display_name="clip.mp4",
        source_path="/tmp/clip.mp4",
    )
    repos.set_asset_object(db, str(asset["id"]), "laura/assets/clip.mp4")

    einzeln = client.get(f"/assets/{asset['id']}")
    liste = client.get(f"/projects/{projekt}/assets")

    assert einzeln.status_code == 200, einzeln.text
    assert einzeln.json()["object_key"] == "laura/assets/clip.mp4"
    assert liste.status_code == 200
    assert [a["object_key"] for a in liste.json()] == ["laura/assets/clip.mp4"]


def test_asset_without_storage_reports_none(client: TestClient, db: Database) -> None:
    """Der Normalfall: kein Objektspeicher, Feld vorhanden und leer."""
    projekt = _projekt(client)
    asset = repos.create_asset(
        db, project_id=projekt, type="video", display_name="clip.mp4",
        source_path="/tmp/clip.mp4",
    )

    antwort = client.get(f"/assets/{asset['id']}")

    assert antwort.status_code == 200
    assert antwort.json()["object_key"] is None


def test_export_carries_its_object_key(
    client: TestClient, db: Database, tmp_path: Path
) -> None:
    projekt = _projekt(client)
    tl = repos.create_timeline(db, project_id=projekt, name="schnitt", kind="rough_cut")
    export = repos.create_export(db, project_id=projekt, timeline_id=tl["id"], format="mp4")
    datei = tmp_path / "fertig.mp4"
    datei.write_bytes(b"x" * 17)
    repos.set_export_done(db, str(export["id"]), path=str(datei), size_bytes=17)
    repos.set_export_object(db, str(export["id"]), f"laura/exports/{export['id']}.mp4")

    einzeln = client.get(f"/exports/{export['id']}")
    liste = client.get(f"/projects/{projekt}/exports")

    assert einzeln.status_code == 200, einzeln.text
    koerper = einzeln.json()
    assert koerper["object_key"] == f"laura/exports/{export['id']}.mp4"
    # Die lokale Datei bleibt daneben stehen -- die Kopie ersetzt sie nie.
    assert koerper["path"] == str(datei)
    assert liste.status_code == 200
    assert [e["object_key"] for e in liste.json()] == [f"laura/exports/{export['id']}.mp4"]


def test_export_without_storage_reports_none(
    client: TestClient, db: Database, tmp_path: Path
) -> None:
    projekt = _projekt(client)
    tl = repos.create_timeline(db, project_id=projekt, name="schnitt", kind="rough_cut")
    export = repos.create_export(db, project_id=projekt, timeline_id=tl["id"], format="mp4")
    datei = tmp_path / "fertig.mp4"
    datei.write_bytes(b"x")
    repos.set_export_done(db, str(export["id"]), path=str(datei), size_bytes=1)

    antwort = client.get(f"/exports/{export['id']}")

    assert antwort.status_code == 200
    assert antwort.json()["object_key"] is None


def test_the_field_survives_the_whole_chain(
    client: TestClient, db: Database, tmp_path: Path, monkeypatch: Any
) -> None:
    """Vom Upload bis zur API-Antwort, ohne die Datenbank von Hand anzufassen.

    Die anderen Tests setzen `object_key` direkt; dieser laesst `publish` ihn
    erzeugen und prueft, dass genau DIESER Wert am anderen Ende herauskommt --
    sonst koennte die Kette an jeder Naht anders heissen.
    """
    from laura.storage import publish

    class _Attrappe:
        bucket = "laura"

        def put(self, key: str, path: Path, *, content_type: str) -> str:
            return f"{self.bucket}/{key}"

    projekt = _projekt(client)
    tl = repos.create_timeline(db, project_id=projekt, name="schnitt", kind="rough_cut")
    export = repos.create_export(db, project_id=projekt, timeline_id=tl["id"], format="mp4")
    datei = tmp_path / "fertig.mp4"
    datei.write_bytes(b"x" * 5)
    repos.set_export_done(db, str(export["id"]), path=str(datei), size_bytes=5)

    schluessel = publish(_Attrappe(), f"exports/{export['id']}.mp4", datei)
    assert schluessel is not None
    repos.set_export_object(db, str(export["id"]), schluessel)

    antwort = client.get(f"/exports/{export['id']}")

    assert antwort.status_code == 200
    assert antwort.json()["object_key"] == schluessel
