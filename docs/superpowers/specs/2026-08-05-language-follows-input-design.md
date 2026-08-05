# Sprache folgt dem Input: Erkennung beim Start + Wechsel per Follow-up

**Datum:** 2026-08-05 · **Status:** entworfen, vom User freigegeben (Chat)

## Motivation

Heute ist die Video-Sprache de-facto fest „German": `BoardMeta.language` steuert
Script-Erzeugung, Voice und Captions bereits vollständig, aber kein Chat-Pfad setzt das
Feld je — der Default gewinnt immer. Live 2026-08-05 bat der User mitten im Vibecoding
„kannst du das video auch auf 70 sek machen und in english?" — der Sprachwunsch hatte
keinerlei Mechanik hinter sich.

## Entscheidungen (User, 2026-08-05)

1. **Sprachregel:** Die Video-Sprache folgt der Sprache der User-Anweisung. Nennt die
   Anweisung explizit eine Zielsprache („auf Englisch", „in english"), gewinnt die
   Nennung über die erkannte Input-Sprache.
2. **Beides:** Erkennung beim Start UND nachträglicher Wechsel per Follow-up auf ein
   bestehendes Video.

## Architektur

### 1. Start-Erkennung im Router

- `start_short` und `start_overview` bekommen ein optionales Argument
  `"language"?: str` — ein englischer Sprachname („German", „English", „Spanish", …).
- Router-Prompt-Regel: Setze `language` auf die Sprache, in der die Anweisung
  geschrieben ist; nennt die Anweisung explizit eine Zielsprache, nimm DIE. Beispiele:
  „bau mir einen Short über X" (deutsch geschrieben) → `"language": "German"`;
  „build me a short about X" → `"language": "English"`;
  „bau mir einen Short über X auf Englisch" → `"language": "English"`.
- Validierung im Router (`_validate_args`): falls vorhanden, nicht-leerer String,
  nur Buchstaben/Leerzeichen, ≤ 32 Zeichen. Fehlt das Argument, bleibt der bisherige
  Default („German") — kein Verhaltensbruch für alte Threads.
- Der Executor (`_handle_start_short`/`_handle_start_overview`) reicht `language` an
  die Session-Erzeugung durch; `BoardMeta.language` trägt ab dann den Wert. Keine
  weitere Neuerung nötig — Script/Voice/Captions lesen das Feld schon heute.
  (`start_overview` nur, sofern dessen Service-Pfad `language` bereits akzeptiert;
  sonst ist der Overview-Pfad in v1 explizit ausgenommen und im Plan als solcher
  markiert.)

### 2. Follow-up-Wechsel: neues Produktions-Tool `set_board_language`

- Neues board-gebundenes Tool in `build_production_tool_specs`:
  `set_board_language(language: str) -> {"ok": bool, ...}` — validiert wie oben,
  schreibt `meta.language` atomar unter dem Board-Lock (gleiches Muster wie
  `set_script_approved`), gibt alt→neu zurück.
- Exponiert an die Kreativ-Agenten, die Script/Storyline besitzen (der Plan pinnt die
  exakte Tool-Zuordnung als Exakt-Tupel-Test) — NICHT an die QA-Stufe und nicht in der
  deterministischen Kette (die Kette bleibt schreibfrei auf Kreativ-Zustand; ein
  Sprachwechsel ist Kreativarbeit und läuft immer über das Team).
- Team-Charter-Zeile (Task-Text): Bittet der User um eine andere Sprache, rufe ZUERST
  `set_board_language`, und schreibe DANACH jedes Kapitel in dieser Sprache neu —
  `save_script_chapter` übernimmt die Board-Sprache automatisch (es liest
  `board.meta().language` seit dem Grounding-Fix).
- Kein neues Gate-Verhalten nötig: Die Script-Neufassung ändert den content_hash, das
  Gate bewaffnet sich neu, die Freigabe läuft wie immer, danach rendert die
  deterministische Kette Voice/Captions in der neuen Sprache.
- Der Router braucht nichts Neues: „mach das in english" ist ein normales
  Text-Follow-up auf die aktive Session (die Anpassungs-Regel aus dem
  Follow-up-Erlebnis-Arc routet es bereits).

### 3. Voice/Captions

- Die konfigurierte Stimme (ElevenLabs, multilingual) spricht die Script-Sprache; es
  gibt KEINE Sprach-Whitelist im Backend. Eine exotische Sprache, die das Modell/die
  Stimme schlecht trifft, ist ein Qualitätsthema für die QA-Stufe, kein Validierungsfall.
- Captions entstehen aus dem Script-Text und sind damit automatisch in der neuen Sprache.

## Fehlerbehandlung

- Ungültiges `language`-Argument beim Start → normale Router-Validierungsrunde
  (ein Retry, dann Fallback) — wie jedes andere Argument.
- `set_board_language` mit ungültigem Wert → `{"ok": False, "reason": ...}` an den
  Agenten, kein Board-Schreiben.
- Fehlt die Erkennung (Router lässt `language` weg), bleibt „German" — nie ein Fehler.

## Bewusst NICHT in v1

- Keine Sprach-Whitelist / kein Locale-Mapping (BCP-47 etc.) — englische Sprachnamen
  als freie Strings genügen der bestehenden Prompt-Maschinerie.
- Keine Mehrsprachigkeit INNERHALB eines Videos.
- Keine Übersetzung bestehender Voice-Artefakte — der Wechsel erzeugt neue.
- UI-Texte/Chat-Antworten bleiben deutsch (discuss antwortet bereits in der
  User-Sprache über seinen eigenen Prompt).

## Tests

1. **Router:** `language`-Validierung (fehlend ok, leer/Sonderzeichen/über-lang →
   Validierungsrunde); Prompt-Pin der Sprachregel-Strings (Beispiele wörtlich).
2. **Executor:** `start_short` mit `language` → Session-Erzeugung erhält den Wert →
   `BoardMeta.language` trägt ihn; ohne Argument → „German" (Bestand).
3. **Tool:** `set_board_language` atomar (alt→neu im Ergebnis, meta.json aktualisiert),
   Validierungsfehler ohne Schreiben; Exakt-Tupel-Pin der Agenten-Zuordnung; NICHT im
   Tail-Toolset (bestehender Vier-Tool-Pin bleibt) und nicht bei qa_reviewer.
4. **Charter:** Task-Text-Pin der Sprachwechsel-Zeile.
5. **Manuell zu prüfen** (Live): „build me a 45s short about X" → englisches Script am
   Gate; Follow-up „mach das in english" auf ein deutsches Video → Team übersetzt,
   Gate re-armed, nach Freigabe englische Voice.
