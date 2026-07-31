# Auto-Overview: ein Thema, mehrere Videos, ein ansehbarer Überblick

**Datum:** 2026-07-31 · **Status:** vom User freigegeben · **Scope:** Phase 2 aus
[`2026-07-21-auto-short-design.md`](2026-07-21-auto-short-design.md) (Entscheidung 2).
Backend-only, wie Phase 1.

## Ausgangslage

Phase 1 beantwortet „gib mir einen Short zum Thema X" — sie schneidet aus **einem** Video,
über das Production Board mit seiner Provenienz-Kette. Phase 2 beantwortet die andere Frage:
„zeig mir, was in meinen Videos zu Thema X steckt". Das ist kein Short aus einer Quelle,
sondern eine Montage über mehrere — und dafür gibt es in Laura bereits ein Gefäß, das genau
das kann: die **Sequenz**. `PUT /sequences/{id}/scenes` nimmt eine geordnete Szenenliste quer
über alle Assets des Projekts, `flatten_sequence` löst sie zur Laufzeit auf,
`POST /timelines/{id}/render` rendert sie inklusive Übergängen.

Der Board-Weg wurde in Phase 1 ausdrücklich verworfen: ein Multi-Asset-Umbau des Boards wäre
ein Eingriff in die Kette, die gerade erst kohärent gemacht wurde. Die Sequenz-Maschinerie
kann Multi-Asset schon heute — sie muss nur bedient werden.

## Entscheidungen (User)

1. **Sequenz UND Render.** Der Lauf hinterlässt eine bearbeitbare Montage *und* einen
   ansehbaren Film. Nicht entweder-oder.
2. **Eigene, zusätzliche Sequenz.** Die Projekt-Sequenz aus „Zusammenfügen" wird nie
   überschrieben. Ehrlicher Preis: die UI zeigt derzeit nur die älteste Sequenz — der
   Überblick ist zunächst über den gerenderten Film und die Sequenz-ID greifbar. Ein
   UI-Umschalter ist ein eigener, kleiner Folgezyklus.
3. **Originalton.** Die Ausschnitte wurden ausgewählt, WEIL ihre Transkripte zum Thema
   passen — sie sagen die Sache selbst. Eine Erzählstimme über fremdes Material zöge eine
   Skript- und Mischschicht ein, die es für Sequenzen nicht gibt; sie ist ein eigener Zyklus.
4. **Der Scout wählt und ordnet, mit Begründung.** Konsistent zu Phase 1 (Ansatz B): nicht
   blanke Cosine-Scores entscheiden über Dramaturgie, sondern ein Agent, der sagt, warum.
   Der deterministische Notnagel bleibt.
5. **Kurze Ausschnitte statt ganzer Szenen.** Gemessen im Livetest-Projekt: Szenen sind im
   Schnitt 24–360s lang, die längste 677s. Ganze Szenen aneinanderzuhängen ergäbe einen
   20-Minuten-Zusammenschnitt, keinen Überblick.
6. **Mindestens zwei Quellen, wenn welche taugen.** Sonst ist es kein Überblick, sondern ein
   längerer Short. Trägt nur ein Video etwas bei, wird das gemeldet, nicht erzwungen.
7. **Ein Endpunkt, synchron bauen, Render anstoßen** (Ansatz A). Kein neuer Job-Typ:
   Discovery und Scout dauern Sekunden, der Render ist längst ein Job.

## Design

### 1. Endpunkt

```
POST /projects/{project_id}/auto-overview
     {topic: str, target_seconds?: int = 180, language?: str}
  -> 202 {sequence_id, source_timeline_id, clips: [...], rationale, fallback,
          ranking, warnings, job_id, export_id}
```

Reihenfolge der Prüfungen, spiegelt Phase 1:

1. Projekt unbekannt → **404**.
2. `autoshort`-Extra fehlt → **503** (der Scout braucht AutoGen).
3. `config_problems(config)` → **503**; `config_warnings(config)` → `warnings` im Response.
4. Discovery liefert nichts Verwertbares → **422 „no material"** — **vor dem ersten
   Schreibvorgang**. Im Projekt bleibt danach nichts liegen.

### 2. Discovery — additive Erweiterung

`short_creator/discovery.py::search_material` bleibt in Form und Verhalten, was es ist
(semantisch wenn der Index antwortet, sonst lexikalisch; Treffer read-only auf Rohschnitt-
Szenen gemappt). Ein Eintrag in `scene_hits` trägt zusätzlich die **Quell-Frames** des
getroffenen Segments:

```python
{"scene_number": int, "snippet": str, "score": float,
 "start_frame": int, "end_frame_exclusive": int}   # neu, Quell-Frame-Raum
```

Die Frames stammen aus `transcript_segments` und liegen im selben Raum wie die
Szenen-Quellbereiche aus `_scene_ranges` — dieselbe Größe, mit der die bestehende Zuordnung
`lo <= start < hi` bereits arbeitet. Phase 1 liest die neuen Schlüssel nicht und bleibt
unverändert; ihre Tests laufen unverändert weiter.

Lexikalische Treffer tragen die Segment-Frames bereits (`repos.search_transcript` selektiert
`s.start_frame`, `s.end_frame`), semantische kommen aus der Qdrant-Payload (`start_frame`/
`end_frame`, so vom Analyse-Handler indiziert) — beide Pfade liefern dasselbe Paar.

**Achtung, Namensfalle:** die Spalte heißt `end_frame`, trägt aber bereits eine
**end-exklusive** Grenze (`mapping.map_segment` snappt sie mit `snap_out_to_frame`, CEIL,
Docstring „Out-point (exclusive)"). Der neue Schlüssel heißt darum `end_frame_exclusive` —
gleicher Wert, ehrlicher Name, konform zu Invariante 2. Es wird **nichts** umgerechnet;
ein `+1` oder `-1` an dieser Stelle wäre ein Off-by-one-Bug.

### 3. Fensterbildung — reine Funktion

`short_creator/overview_windows.py`, ohne DB-Zugriff, damit sie vollständig testbar ist:

```python
def build_windows(hits: list[Hit], *, scene_bounds: dict[int, tuple[int, int]],
                  fps_num: int, fps_den: int) -> list[Window]
```

Regeln, in dieser Reihenfolge:

1. **Basis** = `[start_frame, end_frame_exclusive)` des Segments.
2. **Polster** von 1,0s auf beiden Seiten, in Frames über die Bildrate des Assets.
3. **Klemmung** auf die Quell-Grenzen der Szene, der der Treffer zugeordnet ist. Ein Fenster
   verlässt nie seine Szene.
4. **Verschmelzen** innerhalb desselben Assets: Fenster, die sich überlappen oder deren Lücke
   kleiner als 1,5s ist, werden zu einem.
5. **Grenzen**: kürzer als 4,0s → verworfen; länger als 20,0s → am Ende gekappt.

Alles in **Ganzzahl-Frames, end-exklusiv**; Sekunden existieren nur als Konstanten, die über
`fps_num/fps_den` des jeweiligen Assets umgerechnet werden. Die Zeitkern-Invarianten des
Projekts (`CLAUDE.md` Punkte 1 und 2) gelten unverändert.

`target_seconds` deckelt die Summe: der Scout bekommt die Zielzeit im Auftrag, und die
Auswahl wird nach der Fensterbildung hart auf die Zielzeit plus 20% Toleranz gekürzt
(Ausschnitte am Ende fallen zuerst).

### 4. Scout

`short_creator/overview_scout.py`, gebaut wie `scout.py`, weil sich dessen Härtung live
bewährt hat:

```python
def run_overview_scout(db, config, *, project_id, topic, material, target_seconds,
                       runner=None) -> OverviewDecision
# OverviewDecision: {clips: [{asset_id, scene_number, start_frame,
#                             end_frame_exclusive, why}],
#                    rationale: str, fallback: bool}
```

Ein `AssistantAgent` mit den Discovery-Treffern im Auftragstext, Antwort als letzter
JSON-Block, 60s-Deckel. Der Ablauf ist **validieren → genau ein Retry mit den konkreten
Fehlern → deterministischer Fallback**. Nichts entkommt: auch die Lesevorgänge der
Validierung sind gekapselt (der Fehler, den Phase 1 im Review gefunden hat).

Validierung:

- `asset_id` kommt in `material.ranking` vor,
- `scene_number` existiert für dieses Asset,
- `[start_frame, end_frame_exclusive)` liegt innerhalb der Quell-Grenzen dieser Szene,
- mindestens ein Ausschnitt,
- **mindestens zwei verschiedene Assets**, sofern `material.ranking` mehr als eines enthält.

Der deterministische Fallback: stärkste Treffer, höchstens 3 je Asset, Assets nach Score
sortiert, innerhalb eines Assets chronologisch; zwei Quellen, wenn das Material sie hergibt.
`fallback: true` und die Begründung `"automatic fallback: top search scores"` — dieselbe
Ehrlichkeit wie in Phase 1.

Trägt nur ein Asset Material bei, ist das **kein** Fehler: die Zwei-Quellen-Regel greift
nicht, und `warnings` enthält `"overview covers a single source: only <name> matched the
topic"`.

### 5. Bauen — drei Ebenen, keine Migration

Erst wird **vollständig aufgelöst** (Szene → Quell-Grenzen → Fenster), dann wird geschrieben.
Ein Ausschnitt, der sich nicht auflösen lässt, fällt mit einer Warnung heraus; fallen alle
heraus, ist es ein 422 — und es wurde noch nichts angelegt.

1. **Quell-Timeline**, `kind="overview"`, `name="Überblick: <topic>"`: ein
   `timeline_clips`-Eintrag je Ausschnitt (`asset_id`, `src_in_frame`,
   `src_out_frame_exclusive`), `seq_*` fortlaufend aneinandergesetzt, `lane=0`.
   Das eigene `kind` ist der Schutz: `repos.get_asset_rough_cut` sucht nach
   `kind='rough_cut' AND created_from=<asset_id>` — eine Überblicks-Timeline kann dort nie
   fälschlich als Rohschnitt eines Videos erscheinen.
2. **Eine `scenes`-Zeile je Clip** über dieser Timeline (`order_index` = Position,
   `seq_in/out` = Clip-Grenzen), dann `materialize_scene`. Weil Szenen- und Clip-Grenzen
   exakt zusammenfallen, greift `materialize_scene` **unverändert**: es kopiert genau den
   einen Clip und rechnet ihn auf 0 zurück. Kein neuer Materialisierungs-Code.
3. **Eine eigene Sequenz**, `kind="sequence"`, `name="Überblick: <topic>"`, mit den Szenen in
   Scout-Reihenfolge (`repos.replace_sequence_items`), danach `rebuild_otio`.

Szenen werden über `source_timeline_id` gelistet (`repos.list_scenes`). Da die
Ausschnitts-Szenen an der Überblicks-Timeline hängen, bleiben Szenenlisten **und
Szenennummern** der Quellvideos unberührt — und damit Auto-Shorts Szenen-Referenzen, die auf
`order_index + 1` beruhen.

`get_or_create_project_sequence` liefert weiterhin die **älteste** Sequenz: die Montage in
„Zusammenfügen" bleibt, wie sie ist.

### 6. Render

Der bestehende Pfad wird angestoßen (`export.render`-Job auf die neue Sequenz), nicht neu
gebaut — `_sequence_video_transitions` liest die Übergänge bereits aus `sequence_items`.
Ein fehlgeschlagener Render ist ein normaler Job-Fehler; die Sequenz bleibt bestehen und
bearbeitbar, was der eigentliche Ertrag des Laufs ist.

## Tests

- **Discovery:** Segment-Frames werden durchgereicht (semantisch und lexikalisch); die
  Phase-1-Form bleibt unverändert (bestehende Tests laufen ungeändert).
- **Fensterbildung:** Polster, Klemmung an der Szenengrenze, Verschmelzen überlappender und
  benachbarter Fenster, Mindest- und Höchstlänge, Bildraten-Umrechnung bei
  Nicht-Ganzzahl-Raten (z.B. 30000/1001).
- **Scout:** gültige Antwort; ungültige → Retry; kaputter Runner → Fallback ohne Ausnahme;
  Zwei-Quellen-Regel greift bzw. greift begründet nicht; fremdes Projekt bleibt unsichtbar.
- **Endpunkt:** 404 / 503 (Extra, Config) / 422 / 202; **keine Schreibvorgänge vor dem 422**
  (Timeline- und Szenenzahl vor und nach dem Aufruf identisch); Sequenz, Szenen und
  Quell-Timeline korrekt verdrahtet; `kind="overview"` gesetzt; Render-Job angestoßen;
  die Projekt-Sequenz unverändert.

## Nicht in diesem Scope

- **Erzählstimme** über die Montage (Entscheidung 3) — eigener Zyklus.
- **Board und Provenienz-Kette:** Sequenzen sind keine Kettenglieder. Der Überblick ist
  bearbeitbares Material, kein Artefakt mit `content_hash`/`parents`.
- **UI-Einstieg** und ein Umschalter zwischen mehreren Sequenzen — eigener Zyklus
  (Entscheidung 2).
- **Übergänge** zwischen den Ausschnitten: v1 schneidet hart. `sequence_items` tragen bereits
  `transition_after_kind`; Blenden lassen sich später setzen, ohne dass sich hier etwas ändert.
- Jede Änderung an Phase 1, am Produktions-Team, am Board oder an der semantischen
  Indizierung.
- Kein neuer Job-Typ, keine Schema-Migration.
