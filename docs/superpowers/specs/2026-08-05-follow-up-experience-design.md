# Follow-up-Erlebnis: Weiter chatten & das Video wirklich anpassen

**Datum:** 2026-08-05 · **Status:** entworfen, vom User freigegeben (Chat: „mach das zu einem echten Vibecoding-Tool für Videos")

## Motivation

Live-Vorfall 2026-08-05, unmittelbar nach dem ersten deterministisch gerenderten Short:
Der User schreibt freie Kritik ins Chat-Feld („okay weidmann warum steht das im
transkript das macht kein sinn / und die szenen wahl sollte auch die sachen zeigen was
gesprochen wird") — und der Router fischt das Wort „Transkript" heraus und antwortet mit
einer dritten 100-Segmente-Transkript-Karte. Für den User fühlt sich das wie Hängen an.

Die Anpassungs-MECHANIK existiert seit heute vollständig (Text-Follow-up → Team ändert →
Gate bewaffnet sich bei Script-Änderung neu → Freigabe → schnelle deterministische
Kette → neues „▶ ansehen"). Was fehlt, ist die BEDIENBARKEIT: natürliche Sprache über
das Ergebnis muss zuverlässig als Gespräch oder Anpassung ankommen — das ist der Kern
eines Vibecoding-Tools. Dieser Arc liefert genau das; Bilder-im-Chat und
Sprach-Erkennung folgen als eigene Arcs danach.

## Entscheidungen (User, 2026-08-05)

1. **Freie Kritik/Fragen → Antwort + Vorschlag**: Laura antwortet inhaltlich (gegrundet
   auf Board/Script/Transkript/Session) und schlägt bei umsetzbarer Kritik direkt die
   Aktion vor („Soll ich …?"). Kein Direkt-Umsetzen ohne Rückfrage, kein Nur-Antworten.
2. **Dieser Arc zuerst** — vor Bild-Attachments und Sprach-Erkennung.
3. Bewusst KEINE neuen Schnitt-/Renderer-Fähigkeiten: Der Arc macht das Vorhandene per
   Chat erreichbar.

## Architektur

### 1. Neues Router-Tool `discuss`

- **Router-Vertrag:** `discuss: {"text": str}` — `text` ist die User-Nachricht wörtlich.
  Der Router wählt `discuss`, wenn die Nachricht Frage, Kritik oder Kommentar zum
  Ergebnis/Prozess ist und kein anderes Tool eindeutig passt. `discuss` ersetzt in
  diesen Fällen die bisherige Clarify-Antwort („formulier es bitte einmal anders") —
  die bleibt nur noch der Fallback bei doppeltem Validierungsfehler.
- **Prioritätsregel im Router-Prompt:** Existiert im Kontext eine aktive
  Produktions-Session und die Nachricht redet über das ERGEBNIS (Video, Szenen, Schnitt,
  Text im Video, Transkript-Qualität), gewinnen `discuss`/`follow_up` über die
  Asset-Tools. `review_transcript` wird NUR gewählt, wenn der User das Transkript
  explizit SEHEN will („zeig mir das Transkript"). Der Prompt bekommt Negativ-Beispiel:
  „warum steht das im transkript" → `discuss`, nicht `review_transcript`.
- **Anpassungs-Beispiele für `follow_up`** im Prompt: „mach Szene 2 kürzer", „anderes
  Intro", „zeig das volle Bild", „die Captions sind zu klein" → `follow_up` auf die
  aktive Session.

### 2. Discuss-Handler im Executor (gegrundete Antwort)

`_handle_discuss(db, conversation_id, messages, decision, now_utc)`:

1. **Session-Auflösung** wie `follow_up`: die letzte Action-Karte mit
   `refs.session_id` im Thread; ohne Session läuft `discuss` trotzdem (dann ohne
   Board-Kontext — z. B. Fragen vor dem ersten Auftrag).
2. **Kontext-Bausteine** (jeweils fehler-tolerant, fehlende Teile werden weggelassen):
   - Board-Status-Kompakt: resume_point, Gate-Zustand, Export vorhanden, QA-Verdict +
     Findings (aus `qa_report`), target_ratio.
   - Script-Zeilen (Kapitel · Szene · Text) falls vorhanden.
   - Transkript-Treffer: NUR wenn die Nachricht „Segment N" nennt oder ein
     Wort-n-Gramm der Nachricht (≥ 2 zusammenhängende Wörter, case-insensitive) in
     Segment-Texten vorkommt — dann die Top-3 passenden Segmente mit Index + Text.
   - Die letzten 6 Thread-Nachrichten kompakt (bestehende `_compact_message`-Form).
3. **Ein LLM-Call** über die bestehende Agent-Konfiguration (derselbe
   `build_model_client`-Weg wie der Router, eigener kurzer System-Prompt): Antworte in
   der Sprache des Users, kurz und konkret, erkläre aus dem MITGEGEBENEN Kontext (nie
   erfinden); wenn die Kritik umsetzbar ist, ende mit GENAU einer Zeile im Format
   `Vorschlag: <konkrete Follow-up-Anweisung>` plus dem Satz „Antworte ‚ja', dann setze
   ich das um — oder beschreib es anders."
4. **Fehlerbild:** Wirft der LLM-Call oder liefert er Leeres, antwortet der Handler
   ehrlich deterministisch: „Dazu kann ich gerade nichts Fundiertes sagen —
   beschreib konkret, was am Video anders sein soll." Der Thread bekommt IMMER eine
   Antwort, nie einen Fehler.
5. Die Antwort ist eine normale Text-Nachricht (kein neuer Karten-Typ).

### 3. „Ja" setzt den Vorschlag um

Der Vorschlag steht als Text im Thread und damit im Router-Kontext. Der Router-Prompt
bekommt die Regel + Beispiel: Stimmt der User einer unmittelbar vorausgehenden
`Vorschlag:`-Zeile zu („ja", „mach das", „genau"), wähle `follow_up` mit der aktiven
Session und ÜBERNIMM als `text` den Wortlaut hinter `Vorschlag:` — nicht das bloße
„ja". Kein neuer Zustand, kein neues Tool: Das Gedächtnis ist der Thread selbst.

### 4. Aktive-Session-Zeile im Router-Kontext

`compose_context` bekommt einen optionalen Parameter `active_session`
(`{"id": str, "state": str} | None`) und rendert ihn als eigene Zeile direkt unter der
Videos-Roster-Zeile: `Active production session: <id> (<state>)`. `state` ist eine der
kompakten Formen `done+export` / `awaiting-approval` / `running` / `failed` /
`in-progress`. `api/chat.py` ermittelt sie vor dem Router-Call: letzte Action-Karte mit
`refs.session_id` → Board lesen (`resume_point`, `script_gate.pending`, Job-Status) —
jeder Fehler dabei lässt die Zeile einfach weg (der Router-Call findet immer statt).
Damit muss der Router die Session nicht mehr aus komprimierten Karten erraten — dieselbe
Groundedness-Reparatur wie die Videos-Roster-Zeile von heute Vormittag.

### 5. Discoverability auf der Fertig-Karte

Die ProductionActionCard zeigt im Done-Zustand (Export vorhanden) unter „▶ ansehen"
eine Hinweiszeile: „Weiter anpassen: sag z. B. ‚mach den Hook kürzer' — oder frag
einfach." Rein präsentational, deutsch, ein Satz.

## Fehlerbehandlung

- Der Discuss-LLM-Call ist der einzige neue Fremdaufruf; er ist zeitbegrenzt (gleiches
  Timeout-Muster wie der Router) und fällt auf die deterministische Ehrlich-Antwort
  zurück. Ein Turn endet nie im 500 und nie ohne Antwort.
- Die Aktive-Session-Zeile ist best-effort: Board weg, Session gelöscht, Job-Row fehlt →
  Zeile fehlt, Router läuft wie heute.
- `discuss` ohne Session und ohne Kontext-Bausteine ist erlaubt (reines Gespräch).

## Bewusst NICHT in v1

- Bild-/Datei-Attachments im Chat (nächster Arc).
- Sprach-Erkennung für BoardMeta.language (Arc danach).
- Direkt-Umsetzen von Kritik ohne Bestätigung (User-Entscheidung 1).
- Neue Renderer-/Cutlist-Fähigkeiten; neue Karten-Typen; Persistenz des Vorschlags
  außerhalb des Threads.
- Mehrsprachige Hinweistexte (die neuen UI-/Antwort-Texte sind deutsch wie alles andere).

## Tests

1. **Router:** `discuss` im Toolset + Validierung (`text` nicht-leer); Single-Key-Form
   `{"discuss": {...}}` normalisiert; bestehende Tool-Tests unverändert grün.
2. **Executor discuss:** Session-Auflösung (mit/ohne Session); Kontext-Bausteine
   (Segment-Treffer per n-Gramm, „Segment 87"-Nennung, fehlende Artefakte weggelassen);
   LLM-Seam injiziert (wie `runner` beim Router): Antwort-Passthrough, Vorschlag-Zeile
   bleibt erhalten, Fehler/Leer → deterministischer Fallback-Text; Audit-Parität.
3. **compose_context:** Aktive-Session-Zeile mit jedem `state`; `None` → keine Zeile;
   Reihenfolge (nach Videos-Zeile).
4. **api/chat.py:** Session-Ermittlung best-effort (kaputtes Board → Zeile fehlt,
   Turn läuft).
5. **Frontend:** Done-Karte zeigt die Hinweiszeile; Pending-/Running-/Failed-Zustände
   zeigen sie nicht; vitest + typecheck.
6. **Manuell zu prüfen** (Live-App): der heutige Vorfall als Regressionsfall — freie
   Kritik mit „Transkript" im Wortlaut bei aktiver Session → `discuss`-Antwort statt
   Transkript-Karte; „ja" auf einen Vorschlag → Follow-up-Lauf startet.
