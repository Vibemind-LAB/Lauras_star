# 07 — Interchange & Exportstrategie

Export ist ein **eigener Produktbereich**, kein Anhängsel. Grundsatz: **intern niemals EDL/XML
als Projektzustand** — alles in OTIO/kanonischem Modell halten, Exporte nur generieren.
→ [`ADR-0001`](adr/0001-canonical-otio-internal.md).

## Formate

| Format | Rolle | Status | Hinweise |
|---|---|---|---|
| **OTIO** | kanonisch (intern + extern) | **Pflicht** | Editorial-Cut-Daten, **kein** Media-Container |
| **EDL / CMX3600** | minimal robuster Universalaustausch | **Pflicht** | linear, strukturell limitiert; Adapter `cmx_3600` |
| **FCP 7 XML** | Premiere-/Legacy-Interop | **Pflicht** | laut OTIO **empfohlener** Adobe-Interchange-Pfad; `fcp_xml` |
| **FCPXML** | Final Cut Pro Interop | **wichtig, vorsichtig** | `fcpx_xml` existiert, Maintainer-/Qualitätsrisiko → Warnstatus + Golden Tests |
| **SRT** | universelle Subtitles | **Pflicht** | Plain-Text, extrem breit unterstützt |
| **VTT** | Web-/Review-Subtitles | **Pflicht** | W3C Timed Text fürs Web |

## Capability-/Degradation-Modell (Preflight)

Vor jedem Schreiben prüft `POST /interop/validate`, ob der Zielpfad Features verliert:

- **EDL:** keine mehreren Video-Lanes, begrenzte Transitions/Effekte, keine Sprecher-Metadaten →
  `lossy: true`, `drops: [...]` melden.
- **FCP7-XML:** breit kompatibel, aber Sonderfälle (Speed, Subframe) markieren.
- **FCPXML:** Adapter-Risiken → immer Warnstatus, gegen Fixture-Bank validieren, nie blind vertrauen.
- Exportdialog zeigt **was verloren geht**, bevor geschrieben wird.

## Determinismus & Round-trip

- Exporte sind **deterministisch**: gleiche Timeline + gleiche Optionen → byte-stabiler Output
  (sortierte Attribute, feste Zeitformatierung, keine Zeitstempel im Body außer explizit).
- **Round-trip-Tests** OTIO↔EDL↔XML gegen **Golden Fixtures** in `fixtures/` (→ [`10-testing`](10-testing-observability.md)).
- Adapter bekommt **kanonische Ranges** (end-exclusive, rational), nie UI-Zustände.

## Subtitle-Erzeugung

- SRT/VTT aus `transcript_segments` (Sample→Frame→Timecode), Sprecher-Präfix optional.
- Zeilenumbruch/CPS-Limits konfigurierbar; VTT mit Cue-Settings; SRT plain.

## Implementierung

`services/local-api/src/laura/interchange/` — Module: `otio_io.py`, `edl.py`, `fcp7_xml.py`,
`fcpx_xml.py` (guarded), `captions.py`, `validate.py`. OTIO-Plugins per Export-Matrix absichern.
