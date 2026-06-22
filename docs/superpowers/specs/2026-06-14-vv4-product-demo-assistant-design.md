# VV4 Product-Demo Assistant Design

## Ziel

VV4 baut aus einem Screenrecording-Asset einen editierbaren Demo-Draft. Laura rendert nicht
automatisch ein fertiges Video und importiert keinen MoviePy-/LLM-Workflow in den Kern. Stattdessen
entsteht ein normaler Laura-Workflow: Vorschlaege pruefen, Text anpassen, dann als Szenen/Sequenz
uebernehmen.

## V1-Schnitt

- Input: ein vorhandenes Video-Asset im Projekt.
- Output: `demo_drafts`-Datensatz mit JSON-Items.
- Draft-Item:
  - `src_in_frame`
  - `src_out_frame_exclusive`
  - `label`
  - `voiceover_text`
  - `thumb_frame`
  - `confidence`
  - `enabled`
- Apply: erzeugt einen neuen Rough-Cut fuer das Asset, Szenen in den Draft-Ranges,
  materialisierte Scene-Timelines und setzt die Projekt-Sequenz auf die aktivierten Items.

## Analyse-Strategie

Der Job `demo.analyze` nutzt vorhandene Daten:

1. Wenn Shots aus der neuesten Analyse existieren, werden sie als Item-Ranges genutzt.
2. Sonst wird das Asset in 6-Sekunden-Blöcke geteilt.
3. Labels kommen aus ueberlappenden Transcript-Segmenten, sonst `Schritt N`.
4. Voiceovertext kommt aus den ueberlappenden Segmenttexten, sonst ein neutraler Satz.

Alle Ranges bleiben Ganzzahl-Frames und end-exclusive. Es gibt keine Float-Sekunden im
Projektzustand.

## API

- `POST /assets/{asset_id}/demo-drafts` startet den Job und erzeugt einen Draft im Status
  `analyzing`.
- `GET /demo-drafts/{draft_id}` liest Draft + Items.
- `PATCH /demo-drafts/{draft_id}` speichert editierte Items/Text/Enabled-State.
- `POST /demo-drafts/{draft_id}/apply` uebernimmt aktivierte Items in die Sequenz.

## Frontend

Im Assemble-Tools-Tab ergaenzt `DemoAssistantPanel`:

- Asset-Auswahl aus Projektmedien.
- Button `Demo-Draft erzeugen`.
- Draft laden/status anzeigen.
- Items mit Label, Voiceovertext und aktiv/inaktiv editieren.
- Button `In Sequenz uebernehmen`.

V1 erzeugt noch keine TTS-Clips automatisch. Nach Apply kann der vorhandene Voiceover-Button im
Transcript-Panel genutzt werden.

## Fehlerfaelle

- Unbekanntes Asset: 404.
- Nicht-Video-Asset: 422.
- Draft ohne aktivierte Items: 422 beim Apply.
- Draft aus anderem Projekt/fehlende Szenen werden nicht stumm uebergangen.
- Jobfehler bleiben im Job-Center sichtbar.

## Tests

- Pure Draft-Builder-Tests fuer Shots/Transcript/Fallback.
- API-Tests fuer create/get/update/apply.
- Job-Test fuer `demo.analyze`.
- Frontend-Client-Tests fuer die vier Endpoints.
- Component-Test fuer `DemoAssistantPanel`.

## Exit

VV4 ist fertig, wenn ein Video-Asset per Button zu einem editierbaren Demo-Draft wird und dieser
Draft als normale Laura-Sequenz uebernommen werden kann.
