# ADR-0001 — OTIO als internes Source-of-Truth-Modell

- **Status:** akzeptiert
- **Kontext:** Wir brauchen ein kanonisches Timeline-/Interchange-Modell. Optionen: EDL/XML als
  Projektzustand führen, oder ein eigenes Modell an OTIO anlehnen.

## Entscheidung

Intern **OpenTimelineIO als Source of Truth** führen. EDL, FCP7-XML, FCPXML, SRT, VTT sind
**ausschließlich Exporte** (Adapter-Schicht), niemals Projektzustand.

## Begründung

- OTIO ist explizit für Editorial-Cut-Informationen gedacht (kein Media-Container) und hat ein
  Adapter-System für Interchange.
- EDL ist strukturell limitiert; XML-Dialekte (v. a. FCPXML) haben Adapter-/Maintainer-Risiken.
  Sich früh an einen fremden Dialekt zu ketten, wäre fragil.
- Premiere-Interop läuft laut OTIO-Doku am robustesten über **FCP7-XML** — also gezielt
  exportieren und validieren, statt intern XML zu führen.

## Konsequenzen

- `timelines.otio_json` ist der maßgebliche Zustand; `timeline_clips` ist nur materialisierte Sicht.
- Jeder Export durchläuft Preflight (Capability/Degradation) und Golden-Round-trip-Tests.
- Adapter erhalten **kanonische, end-exclusive, rationale** Ranges — nie UI-Zustände.
