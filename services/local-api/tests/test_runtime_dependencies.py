"""Was der Code beim LADEN importiert, muss eine Laufzeit-Abhaengigkeit sein.

Zweimal hat dieselbe Luecke zugeschlagen, in zwei getrennten Sitzungen am selben
Tag: `numpy` war ueberhaupt nicht deklariert, `httpx` stand in
``[dependency-groups].dev``. Beide werden auf Modulebene importiert, und
``laura.main`` zieht beide Pfade beim Start. Ein Image ohne dev-Gruppe startete
damit gar nicht -- `ModuleNotFoundError` noch vor der ersten Zeile Anwendungscode.

Der Fehler ist im Betrieb unauffindbar teuer: lokal laeuft alles, weil das
Entwickler-venv die dev-Gruppe hat. Sichtbar wird er erst, wenn jemand das Image
neu baut -- unter Umstaenden Monate spaeter, und dann faellt der Dienst aus.

Der Test liest die Deklaration, nicht die Umgebung: eine installierte Bibliothek
beweist nicht, dass sie auch deklariert IST.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

# Module, die `laura.main` beim Import ungeschuetzt erreicht. Wer hier etwas
# hinzufuegt, muss es auch in [project].dependencies eintragen -- genau darum
# geht es. Geschuetzte Importe (try/except) gehoeren NICHT in diese Liste:
# `opentelemetry` (telemetry.py) und `scenedetect` (shots.py) duerfen fehlen.
BEIM_START_GEBRAUCHT = ["numpy", "httpx"]


def _deklarierte_namen() -> set[str]:
    pyproject_path = Path(__file__).parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return {
        dependency.split("[", 1)[0].split(">", 1)[0]
        .split("=", 1)[0].split("<", 1)[0].strip().lower()
        for dependency in pyproject["project"]["dependencies"]
    }


@pytest.mark.parametrize("modul", BEIM_START_GEBRAUCHT)
def test_is_declared_as_a_runtime_dependency(modul: str) -> None:
    assert modul in _deklarierte_namen(), (
        f"{modul} wird beim Start importiert, steht aber nicht in "
        "[project].dependencies -- ein Image ohne dev-Gruppe startet damit nicht"
    )


def test_none_of_them_hides_in_the_dev_group() -> None:
    """Doppelt deklariert ist kein Fehler, aber NUR in dev war genau der Fehler.

    Der Test prueft die Richtung, die weh tut: ein Modul, das beim Start gebraucht
    wird und ausschliesslich in der dev-Gruppe steht.
    """
    pyproject_path = Path(__file__).parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    dev = {
        eintrag.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0].split("<", 1)[0].strip().lower()
        for eintrag in pyproject.get("dependency-groups", {}).get("dev", [])
    }
    laufzeit = _deklarierte_namen()

    nur_dev = [m for m in BEIM_START_GEBRAUCHT if m in dev and m not in laufzeit]

    assert not nur_dev, f"nur in der dev-Gruppe deklariert: {nur_dev}"
