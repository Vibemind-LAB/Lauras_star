"""Die Objekt-Adresse muss EINLOESBAR sein, nicht nur vorhanden.

Am 22.09.2026 gemessen: ein Agent bekam die Objekt-Adresse eines fertigen Videos,
nannte sie "kein direkt herunterladbarer Link" und schlug vor, das Video NEU ZU
RENDERN, um an eine Datei zu kommen. Er hatte recht -- der Schluessel allein ist
eine Tuer ohne Klinke. Wer ihn einloesen wollte, braeuchte den Service-Key, und den
darf ein Agent nie sehen.

Geprueft wird deshalb beides: dass der Link entsteht, UND dass jeder Weg, auf dem
er nicht entstehen kann, eine klare Antwort gibt statt eines 500ers. Die
Fehlerarten sind absichtlich Marken und keine Texte -- eine Route, die auf
Wortlaut prueft, bricht beim ersten Umformulieren.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app
from laura.storage import SpeicherFehler, abruf_link


class _FakeStore:
    """Drei Zeilen, wie das Protocol es meint -- plus der neue Link."""

    bucket = "laura"

    def __init__(self, antwort: str = "http://speicher:5055/object/sign/laura/a.mp4?token=x"):
        self.antwort = antwort
        self.gefragt: list[tuple[str, int]] = []

    def signed_url(self, key: str, *, gueltig_sekunden: int = 3600) -> str:
        self.gefragt.append((key, gueltig_sekunden))
        return self.antwort


def _settings_mit_speicher(tmp_path: Path, an: bool = True) -> Settings:
    return Settings(
        workspace_root=tmp_path,
        start_runner=False,
        storage_url="http://speicher:5055" if an else None,
        storage_key="geheim" if an else None,
        storage_bucket="laura",
    )


# -- die Hilfe selbst ---------------------------------------------------------

def test_ohne_speicher_gibt_es_keinen_link(tmp_path: Path) -> None:
    with pytest.raises(SpeicherFehler) as e:
        abruf_link(_settings_mit_speicher(tmp_path, an=False), "laura/assets/a.mp4")
    assert e.value.art == "nicht_konfiguriert"


def test_ohne_objekt_adresse_gibt_es_keinen_link(tmp_path: Path) -> None:
    """Der haeufigste Fall: Material, das vor dem 16.09.2026 importiert wurde."""
    with pytest.raises(SpeicherFehler) as e:
        abruf_link(_settings_mit_speicher(tmp_path), None)
    assert e.value.art == "keine_kopie"
    assert "16.09.2026" in e.value.grund, "der Grund soll erklaeren, WARUM sie fehlt"


def test_eine_adresse_aus_einem_fremden_eimer_wird_abgelehnt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst signierte Laura Objekte, die ihr nicht gehoeren."""
    import laura.storage as st

    monkeypatch.setattr(st, "build_store", lambda s: _FakeStore())
    with pytest.raises(SpeicherFehler) as e:
        abruf_link(_settings_mit_speicher(tmp_path), "face_targets/heimlich.png")
    assert e.value.art == "fremder_eimer"


def test_ein_unerreichbarer_speicher_ist_ein_befund_kein_absturz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import laura.storage as st

    class _Kaputt(_FakeStore):
        def signed_url(self, key: str, *, gueltig_sekunden: int = 3600) -> str:
            raise OSError("bucket unreachable")

    monkeypatch.setattr(st, "build_store", lambda s: _Kaputt())
    with pytest.raises(SpeicherFehler) as e:
        abruf_link(_settings_mit_speicher(tmp_path), "laura/assets/a.mp4")
    assert e.value.art == "speicher_unerreichbar"


def test_der_eimer_wird_vom_schluessel_abgeschnitten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`object_key` ist bucket-qualifiziert, der Speicher will den Pfad OHNE Eimer.

    Ohne diesen Schnitt entstuende `/object/sign/laura/laura/assets/...` -- ein
    Link auf ein Objekt, das es nicht gibt, und der Fehler faellt erst beim Abruf
    auf, weit weg von hier.
    """
    import laura.storage as st

    laden = _FakeStore()
    monkeypatch.setattr(st, "build_store", lambda s: laden)

    link, gueltig = abruf_link(_settings_mit_speicher(tmp_path),
                               "laura/assets/abc.mp4", gueltig_sekunden=60)

    assert laden.gefragt == [("assets/abc.mp4", 60)], "ohne Eimer-Praefix"
    assert link == laden.antwort
    assert gueltig == 60


def test_der_link_ist_absolut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """storage-api antwortet mit einem PFAD; wer ihn bekommt, kann damit nichts."""
    from laura.storage import SupabaseStorage

    laden = SupabaseStorage("http://speicher:5055", "geheim", "laura")

    class _Antwort:
        def read(self) -> bytes:
            return b'{"signedURL":"/object/sign/laura/assets/a.mp4?token=t"}'

        def __enter__(self) -> _Antwort:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr("laura.storage.urlopen", lambda *a, **k: _Antwort())

    assert laden.signed_url("assets/a.mp4") == (
        "http://speicher:5055/object/sign/laura/assets/a.mp4?token=t"
    )


# -- durch die API ------------------------------------------------------------

def _client_mit_asset(tmp_path: Path, object_key: str | None) -> tuple[TestClient, str]:
    settings = _settings_mit_speicher(tmp_path)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    projekt = repos.create_project(db, name="p", rate_num=30, rate_den=1,
                                   drop_frame=False,
                                   workspace_root=str(tmp_path / "p"))
    asset = repos.create_asset(db, project_id=projekt["id"], type="video",
                               display_name="a.mp4", source_path=str(tmp_path / "a.mp4"))
    if object_key:
        repos.set_asset_object(db, asset["id"], object_key)
    client = TestClient(create_app(settings))
    return client, asset["id"]


def test_die_api_gibt_einen_link_heraus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import laura.storage as st

    monkeypatch.setattr(st, "build_store", lambda s: _FakeStore())
    client, asset_id = _client_mit_asset(tmp_path, "laura/assets/abc.mp4")
    client.__enter__()
    try:
        antwort = client.get(f"/assets/{asset_id}/download-url")
        assert antwort.status_code == 200, antwort.text
        d = antwort.json()
        assert d["url"].startswith("http")
        assert d["object_key"] == "laura/assets/abc.mp4"
        assert d["expires_in_seconds"] > 0
    finally:
        client.__exit__(None, None, None)


def test_ohne_kopie_antwortet_die_api_mit_404_und_einem_grund(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein 500: wer fragt, soll erfahren, WARUM es keinen Link gibt."""
    import laura.storage as st

    monkeypatch.setattr(st, "build_store", lambda s: _FakeStore())
    client, asset_id = _client_mit_asset(tmp_path, None)
    client.__enter__()
    try:
        antwort = client.get(f"/assets/{asset_id}/download-url")
        assert antwort.status_code == 404
        assert "Objektspeicher" in antwort.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_ein_unbekanntes_asset_ist_ein_404(tmp_path: Path) -> None:
    client, _ = _client_mit_asset(tmp_path, None)
    client.__enter__()
    try:
        assert client.get("/assets/gibtesnicht/download-url").status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_ein_unerreichbarer_speicher_wird_zu_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """502, nicht 500: der Fehler liegt nachweislich nicht bei Laura."""
    import laura.storage as st

    class _Kaputt(_FakeStore):
        def signed_url(self, key: str, *, gueltig_sekunden: int = 3600) -> str:
            raise OSError("bucket unreachable")

    monkeypatch.setattr(st, "build_store", lambda s: _Kaputt())
    client, asset_id = _client_mit_asset(tmp_path, "laura/assets/abc.mp4")
    client.__enter__()
    try:
        assert client.get(f"/assets/{asset_id}/download-url").status_code == 502
    finally:
        client.__exit__(None, None, None)
