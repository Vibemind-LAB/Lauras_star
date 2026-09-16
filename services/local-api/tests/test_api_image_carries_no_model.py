"""Das API-Image darf kein lokales Modell mitbringen.

Nutzer-Auflage vom 11./12./16.09.2026 fuer den Umzug auf den Mini-PC (Geekom AS 6,
KEINE dedizierte GPU): dort laeuft kein lokales Modell. Modellarbeit gehoert in die
Sidecars -- `analysis-runtime`, MuseTalk, LivePortrait, voice -- und Laura spricht sie
ueber HTTP an (`LAURA_ANALYSIS_URL`, `ai_runtimes.base_url`).

Der Zustand ist HEUTE schon richtig: das Image baut mit `--extra scene --extra otel`,
und `scene` ist scenedetect+opencv, also klassische Bildverarbeitung ohne Gewichte.
Genau deshalb ist dieser Test billig -- er haelt fest, was gilt, statt etwas zu
erzwingen. Er schlaegt an, sobald jemand `--extra asr` ergaenzt, und nicht erst, wenn
der Mini-PC anfaengt, ein Modell zu laden.

GRENZE DIESES TESTS, damit niemand mehr hineinliest, als er zeigt: er liest das
BAUREZEPT, nicht ein gebautes Image. Er beweist, dass dieser Dockerfile kein Modell
hinzufuegt -- nicht, dass ein irgendwann anders gebautes Image keines enthaelt. Ein
Image, das gerade kein Modell GELADEN hat, beweist ohnehin gar nichts; deshalb wird
hier die Datei gelesen und nicht die Laufzeit befragt.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

PAKET = Path(__file__).resolve().parents[1]
DOCKERFILE = PAKET / "Dockerfile"
PYPROJECT = PAKET / "pyproject.toml"

MODELLTRAGENDE_EXTRAS = {
    "scene-ml",   # transnetv2-pytorch
    "asr",        # faster-whisper
    "align",      # whisperx + torch
    "diarize",    # pyannote.audio + torch
    "semantic",   # fastembed
    "autoshort",  # autogen-ext[openai,ollama]
}
"""Extras, die Modellgewichte oder eine Modell-Laufzeit mitbringen. Wer hier einen
Extra ergaenzt, muss ihn einordnen -- die Gegenprobe unten haelt die Liste ehrlich."""

# Zweite Tuer neben den Extras: ein Gewicht laesst sich auch direkt ins Image holen.
GEWICHTE_HOLEN = (
    re.compile(r"\bsnapshot_download\b"),
    re.compile(r"huggingface\.co", re.IGNORECASE),
    re.compile(r"\bhf\s+download\b"),
    re.compile(r"\bollama\s+pull\b"),
    re.compile(r"\.(?:onnx|safetensors|gguf|ckpt|pt|pth|bin)\b"),
)


def _anweisungen() -> str:
    """Nur echte Zeilen, keine Kommentare -- sonst zaehlt eine Erklaerung als Verstoss."""
    zeilen = [
        z for z in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if not z.lstrip().startswith("#")
    ]
    return "\n".join(zeilen)


def test_the_api_image_installs_no_model_bearing_extra() -> None:
    installiert = set(re.findall(r"--extra\s+([a-z0-9-]+)", _anweisungen()))
    verstoesse = installiert & MODELLTRAGENDE_EXTRAS

    assert not verstoesse, (
        f"Das API-Image wuerde ein lokales Modell mitbringen: {sorted(verstoesse)}. "
        "Modellarbeit gehoert in die Sidecars; Laura spricht sie ueber HTTP an "
        "(LAURA_ANALYSIS_URL, ai_runtimes.base_url). Nutzer-Auflage fuer den Mini-PC."
    )


def test_the_api_image_does_not_fetch_weights_directly() -> None:
    """Ein Gewicht kommt auch ohne Extra ins Image -- per COPY, curl oder Download."""
    text = _anweisungen()
    treffer = [m.pattern for m in GEWICHTE_HOLEN if m.search(text)]

    assert not treffer, (
        f"Der Dockerfile holt Modellgewichte ins Image: {treffer}. "
        "Auf dem Mini-PC laeuft kein lokales Modell."
    )


@pytest.mark.parametrize("extra", sorted(MODELLTRAGENDE_EXTRAS))
def test_every_listed_extra_really_exists(extra: str) -> None:
    """Gegenprobe: die Liste darf nicht an umbenannten Extras vorbeilaufen.

    Ein Test, der auf Namen prueft, die es nicht mehr gibt, ist gruen und wertlos --
    dieselbe Falle wie die festgenagelten Schemaversionen, die diese Suite schon
    einmal jahrelang mitgeschleppt hat.
    """
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert extra in pyproject["project"]["optional-dependencies"], (
        f"'{extra}' steht in MODELLTRAGENDE_EXTRAS, existiert aber nicht mehr in "
        "pyproject.toml -- Liste nachziehen, sonst prueft dieser Test ins Leere."
    )
