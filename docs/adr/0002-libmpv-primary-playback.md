# ADR-0002 — libmpv als primärer Playback-Layer

- **Status:** akzeptiert
- **Kontext:** Pro-Playback/Scrubbing in einer Electron-App. Optionen: Browser-`<video>`/WebCodecs
  vs. eingebettetes natives Backend.

## Entscheidung

**libmpv** ist der primäre Playback-/Scrub-Layer. **WebCodecs** nur **ergänzend** (z. B. schnelle
Thumbnails/Spezialfälle), nicht als alleiniger Pro-Player.

## Begründung

- libmpv ist offiziell als Einbettungs-Backend vorgesehen, robust für Video/Audio.
- WebCodecs ist mächtig, aber browserseitig noch nicht überall Baseline → allein zu riskant für
  frame-genaues Pro-Playback.
- Scrubbing/Playback ist ein **Top-Risiko** des Produkts; ein bewährter nativer Player senkt es.

## Konsequenzen

- Native Integration in Electron kostet Arbeit (Build/Bindings je OS) — bewusst eingeplant (Phase „Härtung").
- Frame-genaues Seeking gegen CFR-Proxies; VFR-Quellen vorher zu CFR proxien.
- Playback-Engine lebt im Main-Prozess/nativ, gesteuert per IPC aus dem Renderer.
