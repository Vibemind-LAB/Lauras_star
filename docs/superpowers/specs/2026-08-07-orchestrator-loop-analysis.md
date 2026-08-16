# Orchestrator-Loop Analysis — Why the Team Keeps "Waiting" Instead of Stopping

Date: 2026-08-07 · Evidence: livetest sessions `617523e1` (Obsidian short) and `e982cb51`
(Idea Spaces), run logs under `workspace-livetest/agent-runs/<sid>/runs/*.ndjson`, plus the
implementation as merged in PR #15 (`960406f`).

## 1. Executive Diagnosis

The persisted workflow state is correct throughout — no LLM statement ever became board
state. The loop is purely an **orchestration-termination defect**: "waiting for the user"
exists on the board (`scene_gate.pending`) but has **no representation in the MagenticOne
run loop**. The team is constructed with `max_turns=30` and **no termination condition**
(production_agents.py:350: `MagenticOneGroupChat(participants=agents,
model_client=orchestrator, max_turns=MAX_TURNS)`; `MAX_TURNS = 30`, :35). After
`propose_scene_selection` succeeds, the orchestrator's task ledger still reads the overall
goal ("build a short") as unfinished, so it re-delegates; **no agent has a confirm tool
(server-only, by design)**, so agents can only answer in prose ("waiting"); the progress
ledger detects a stall (default `max_stalls=3`), triggers a re-plan that re-injects the
full ~11 KB task text, and delegates the same instruction again. The run is bounded only
by `max_turns` / stall heuristics and finally self-terminates as `weak` — after burning
104 post-proposal events in the observed run.

The tool result's "STOP now" note is prose addressed at the model; prose does not bind
(this repo's I2 lesson, reproduced at the orchestration layer). The deterministic guards
(`save_storyline` / `save_script_chapter` refusals, server-only `confirmed_utc` writer,
the message-less-resume early-exit) all held — they made the loop *harmless to state* but
could not make it *stop*.

## 2. Evidence

Session A (`617523e1`), run `20260807T074859Z` — the start run:

- 165 events total: 65 agent messages, 49 tool calls, 49 tool results, 1 done.
- `propose_scene_selection` succeeds at event 15. **After it: 104 events** —
  story_architect 51, MagenticOneOrchestrator 30, scene_author 13, vision_reviewer 10.
- 20 of those messages are wait/confirm-flavored ("Wait for explicit user confirmation",
  "Please confirm the scene selection", "Confirm the scene selection so the scene author
  can proceed" — the screenshot the user captured shows this exchange verbatim).
- Refused tool calls in the same window (structural guards holding):
  `save_script_chapter · reason=no storyline on the board — call save_storyline first`
  (twice in the visible UI excerpt alone).
- **9 full task-ledger restatements** ("We are working to address the following user
  request… 1) GOAL: …") at events 2, 50, 61, 62, 63, 64, 65, 109, 152 — sizes 10 089 to
  12 146 chars, i.e. ~100 KB of task text re-injected across one run. The consecutive
  cluster 61–65 is a stall→re-plan churn: five re-plans in a row without any new fact.
- Run end: `{"type": "done", "ok": true, "weak": true, "resume_point":
  "scene_selection"}` — MagenticOne gave up on its own ("weak" = ended without the goal),
  not via any await-semantics.
- Gate-B shows the same pattern: run `20260807T075935Z` (post-approval team run) contains
  64 agent messages, mostly waiting/QA chatter around the voice refusal, before dying on
  the day's transient `Connection error.`

Counter-evidence that the *deterministic* layer behaves: once the run ended, the parked
board never spawned another team — the message-less resume early-exit returned
"awaiting user scene selection" without calling `execute`
(production_orchestrator.py:686-708; pinned by
`test_run_awaiting_selection_never_spawns_team`, verified live by the session watcher).
And the user's real confirmation (SceneSelectionCard click, 11:47) moved the board
deterministically: `confirmed_utc` stamped by `confirm_scene_selection`
(api/short_creator.py:1028 — the repo-wide **only** writer of that field), resume run,
storyline → script in ~90 s.

## 3. Repetition Analysis

| Repetition | Count (session A, run 1) | Initiator |
|---|---|---|
| Orchestrator→agent delegations after the proposal existed | ~30 orchestrator messages | MagenticOneOrchestrator |
| "Wait for user confirmation"-class instructions/replies | 20 | orchestrator instructs, agents echo |
| Full task re-injection (ledger restatement, ~11 KB each) | 9 (5 consecutive during one stall cluster) | MagenticOne re-plan step |
| Guard-refused write attempts (`save_script_chapter`/`save_storyline`) | ≥2 visible in UI excerpt; tool_result stream carries each refusal | scene_author / story_architect (delegated into the wall) |
| Proposal overwrites while unconfirmed (session B) | v1 → v3 (3 candidates → 3 → 33 after a follow-up nudge) | story_architect (legal pre-confirm retry path) |

Who initiates: **always the orchestrator.** Agents never self-invoke; every lap is a
fresh delegation from the ledger loop. The repetition mechanism is: unfinished goal in
ledger → delegate → agent cannot act (no tool / guard refuses) → prose reply → no
progress detected → stall counter → re-plan (full task re-injection) → same delegation.

## 4. Root Cause Tree

```
PRIMARY ROOT CAUSE
└─ AWAITING_USER_INPUT is not a terminal state of the orchestration run:
   MagenticOneGroupChat is constructed without any termination condition
   (production_agents.py:350); the proposal tool's "STOP now" is prose, not protocol.

SECONDARY CAUSES
├─ The orchestrator delegates "confirm the selection" as if it were an agent-performable
│  subtask — no participant has (or should have) a confirm tool; the ledger has no
│  concept of "external actor required".
├─ Stall→re-plan re-injects the complete task text (charter + SOURCE MATERIAL + facts,
│  ~11 KB) each cycle — completed stages are re-presented as text, not as
│  machine-readable done-state, inviting re-consideration.
└─ Start runs use require_tool_call=False (only message runs enforce a tool call), so a
   turn made of pure waiting prose still counts as a "successful" turn.

AMPLIFYING FACTORS
├─ max_turns=30 and max_stalls=3 (library default) are the only bounds — generous enough
│  for many laps; each lap costs one or more LLM calls.
├─ The charter itself narrates the gate lifecycle in prose ("Then STOP … until the user
│  confirmed") — the model reasons ABOUT confirmation instead of terminating.
└─ Growing context per re-plan raises the odds of contradictory assumptions (the model
   paraphrasing the charter's "After confirmation, use ONLY the selected scenes" into
   near-claims that confirmation happened).

SYMPTOMS
├─ Repeated wait instructions / "waiting" replies (20×).
├─ Guard-refused writes (correct, but wasted).
├─ Run ends "weak" instead of "awaiting user".
└─ Wasted tokens and minutes per gate encounter (~2/3 of run 1's events were post-gate).
```

Classification of the "user has confirmed" appearances (deliverable "False State
Transition"): **prompt-induced assumption / stale ledger paraphrase** — the phrases occur
inside the orchestrator's ledger restatements which quote and paraphrase the charter's
confirmation clauses. No hallucinated state ever reached the board: `scene_gate.confirmed`
stayed `false` until the real HTTP confirm (board versions confirm this — the archived
`scene_selection` v1/v2 carry `confirmed_utc: null`). The correct hardening is therefore
not "validate LLM claims" (the write path already ignores them) but "stop asking the LLM
to reason across the gate at all".

## 5. Actual vs Intended State Machine

Intended (and — on the BOARD — actually implemented):

```
INIT → SCENE_REVIEW → SCENE_SELECTION_PROPOSED → AWAITING_SCENE_CONFIRMATION ⏸
  ⏸ —SceneSelectionConfirmed(user)→ SCENE_SELECTION_CONFIRMED
  → STORYLINE → SCRIPT → AWAITING_SCRIPT_CONFIRMATION ⏸
  ⏸ —ScriptApproved(user)→ VOICE → CUTLIST → CONTACT_SHEET → RENDER → QA → DONE
```

Actual, at the ORCHESTRATION layer during a single team run:

```
… SCENE_SELECTION_PROPOSED
   ↓ (ledger: goal unfinished)
DELEGATE("wait/confirm") → agent prose "waiting" → stall++ → RE-PLAN(full task) ─┐
   ↑ ───────────────────────────────────────────────────────────────────────────┘
until max_turns/stall gives up → run ends "weak"        (bounded loop, no ⏸ state)
```

Between runs the machine is correct: `Board.resume_point` treats a present-but-unconfirmed
proposal as `scene_selection` (board.py:541-547) and `run_production`'s early-exit parks
without a team (only for `message is None` runs). The defect lives exclusively *inside*
the team run that made the proposal (and, symmetrically, any message run that reaches an
open gate).

Per-state table (board-owned states; the two ⏸ states are the interesting rows):

| State | Entry | Persisted as | Exit | Who transitions | LLM may transition? |
|---|---|---|---|---|---|
| AWAITING_SCENE_CONFIRMATION | `propose_scene_selection` ok | `scene_selection.json` with `confirmed_utc=null` | HTTP/chat confirm | `confirm_scene_selection` (server) | **No** (agents have no tool; guards refuse writes) |
| AWAITING_SCRIPT_CONFIRMATION | script saved on gated board | `meta.script_gate` + no `script_approved_utc` | approve_script | `Board.set_script_approved` (server) | **No** (`synthesize_script_voice` refuses) |
| all others | artifact presence | chain artifacts + parents | next tool | deterministic tail / team tools | Only via validated tool writes |

Transitions that exist **only in prompts** today: "stop after proposing" and "stop after
saving the script" (the run-termination edges of both ⏸ states).

## 6. State Ownership

| State | Owns it today | Should own it |
|---|---|---|
| Gate pending/confirmed, selection, approval | Board (server-only writers) ✅ | unchanged |
| Chain progress / resume point | Board presence + gate fields ✅ | unchanged |
| "Is this run finished?" | MagenticOne ledger (LLM text) ❌ | deterministic termination condition in the team wrapper |
| "What happens next after a pause?" | run_production early-exit + deterministic_eligible ✅ | unchanged |
| Run outcome label | result status ("ok/weak/hard_fail") — "weak" conflates gave-up with awaiting | explicit `awaiting_user` status derived from board state |

## 7. Human-Gate Analysis

Confirmation **cannot** progress from inside the team — by design and correctly so:
`confirmed_utc` has exactly one writer (`confirm_scene_selection`,
api/short_creator.py; chat `select_scenes` and the SceneSelectionCard both land on it),
agents have no such tool, and `propose_scene_selection` refuses to touch a confirmed
selection (post-final-review guard). Binding: confirm validates `picked ⊆ candidates`
against the artifact it then updates under the board lock; a *new* proposal is a new
artifact version with `confirmed_utc=null`, so an old confirmation can never apply to a
new proposal (idempotent re-confirm with the same set is a no-op that re-enqueues the
resume — the heal-forward path). The only residual nuance is the accepted
read-validate-write window (no proposal-id in the confirm request); a
`selection_version` field in the request would close it cheaply (P2).

What *fails* is purely that the producing run does not terminate at the gate — and that
its result is labeled `weak` instead of `awaiting user`.

## 8. Concrete Fix (minimal)

**P0-A — a real termination signal.** The installed autogen provides what we need
(`autogen_agentchat.conditions`: `TextMentionTermination`, `FunctionalTermination`,
`StopMessageTermination`, …). Smallest viable change:

1. `production_tools.py`: add a module constant
   `AWAITING_USER_SENTINEL = "AWAITING_USER_INPUT"` and append it to the RETURN note of
   (a) `propose_scene_selection` on success while the gate is unconfirmed, and
   (b) `synthesize_script_voice`'s two Gate-B refusal reasons.
2. `production_agents.py:350`: construct the team with
   `termination_condition=TextMentionTermination(AWAITING_USER_SENTINEL)` (OR-combined
   with nothing else needed — `max_turns` stays as backstop):
   `MagenticOneGroupChat(participants=agents, model_client=orchestrator,
   max_turns=MAX_TURNS, termination_condition=TextMentionTermination(...))`.
   Termination conditions see tool-result content as message text, so the sentinel in the
   tool result ends the run on the very turn the proposal lands. (If the installed
   version's condition checks only chat messages and not tool events, use
   `FunctionalTermination` over the message sequence instead — same two files.)
3. `production_orchestrator.py`: when the run ended via the sentinel (or, more robustly,
   when `meta.scene_gate` and `Board.resume_point(...) == "scene_selection"` with a
   proposal present / `script_gate` pending after the run), set the result `status` to a
   new value `"awaiting_user"` instead of relying on `weak`, and skip
   `board.set_status("failed")` paths. UI maps it to the existing pause cards.

**P0-B — don't even offer the impossible delegation.** In `build_production_task`'s
Gate-S block, replace "Then STOP" with "Then call no further tools; the run ends
automatically" (aligns the prose with the now-real mechanism; prose stays advisory, the
condition does the stopping).

**P1 — deterministic outcome plumbing:** result `status="awaiting_user"` (above) +
`resume_point` already carried; chat done-cards render "wartet auf dich" instead of
"⚠ schwach".

**P2 — idempotency polish:** `selection_version` in the confirm request (reject
version-mismatch with 409, closing the TOCTOU window); everything else already holds (see
§10).

**P3 — context diet:** on a gate-phase run (`resume_point == "scene_selection"` at task
build time), omit the SOURCE MATERIAL / SCENE FACTS blocks from the task — phase 1 needs
review+propose context only. Cuts the ~11 KB re-injection roughly in half and removes the
post-gate lifecycle prose the model keeps paraphrasing.

## 9. Proposed State Model (delta only)

States and persistence stay as they are (board-owned, above). Newly explicit:

- Event `SceneSelectionConfirmed {session_id, selection_version, scene_numbers,
  confirmed_utc}` — already materialized as the confirm write; add `selection_version`
  to the request/validation (P2).
- Run-level status enum gains `awaiting_user` (alongside ok/weak/hard_fail), derived
  deterministically from board state at run end — never from LLM text.
- Termination events: tool-result sentinel `AWAITING_USER_INPUT` ends any team run.

## 10. Idempotency (current truth per operation)

| Operation | Called twice → | Mechanism |
|---|---|---|
| review_scene | 1 re-review per healthy review per run | re-review budget (production_tools) |
| propose_scene_selection | identical → board no-op; different → new version (resets selection); confirmed → **refused** | `_same_content` short-circuit + final-review guard |
| confirm_scene_selection | same set → `already_current` no-op **+ resume re-enqueued** (heal); different set → new version, cascade | GS4 + heal-forward |
| save_storyline | identical → no-op; structure-equal → carry-over re-stamp | `_same_content`, carried_over |
| save_script_chapter | replace-per-chapter (documented); identical merge → no-op | merge semantics |
| synthesize_script_voice | whole-track hash cache + per-line disk cache | script_hash + line_hash(text+voice) |
| build_cutlist | recomputed deterministically; identical output → board no-op | pure derivation |
| save_contact_sheet / render_production | render cycles capped (`_MAX_RENDER_CYCLES`, +1 per approval) | caps |
| save_qa_report | new version, harmless | chain tail |

Recommended keys already in force: session_id (board root), artifact content-hash
(identity), line_hash (voice), approval bound to script content_hash. Add:
selection_version on confirm (P2).

## 11. Orchestrator Termination Contract

A MagenticOne production run MUST stop when any of:
1. A tool result carries `AWAITING_USER_INPUT` (gate reached) → result
   `status="awaiting_user"`, board stays `active`.
2. The goal is complete for this phase (existing final-answer path).
3. `max_turns` (30) or stall-exhaustion → `weak` (now meaning only "gave up", never
   "waiting").
And a run MUST NOT start when `message is None` and the board is parked at a gate
(exists: Gate-S early-exit; extend the same check to a pending Gate B before team
construction — today only `deterministic_eligible` covers the approved case).

## 12. Tests (mapping + gaps)

Already existing (pinning today's correct behavior):
- `test_run_awaiting_selection_never_spawns_team` ≙ resume-side of
  *test_scene_selection_stops_orchestrator*.
- `test_run_confirmed_selection_runs_the_team` — confirmed board proceeds.
- Gate-S guard tests ≙ *test_storyline_requires_confirmed_selection*.
- `test_confirm_happy_path_enqueues_resume`, `test_reconfirm_same_set_is_noop_but_heals_a_resume`
  ≙ *test_user_confirmation_updates_board* (+ heal).
- propose-refusal-when-confirmed test ≙ *test_old_confirmation_cannot_confirm_new_proposal*
  (inverse direction) — the direct direction (confirm carries stale version) needs P2.
- Board/idempotency suites ≙ *test_duplicate_tool_calls_are_idempotent* (partial),
  `Board.resume_point` tests ≙ *test_resume_uses_persisted_state*.

New tests required by the P0 fix:
- `test_awaiting_sentinel_terminates_the_team_run` — fake execute that yields a propose
  tool-result with the sentinel; assert the group chat stops on that turn (no further
  delegation) — the *test_waiting_agent_is_not_reinvoked* of this architecture.
- `test_run_result_status_is_awaiting_user_at_open_gate` — run ends at gate → result
  status `awaiting_user`, board `active`, resume_point `scene_selection`.
- `test_proposal_is_not_recreated_while_waiting` — after the sentinel ends the run, no
  second propose happened within the run (assert single proposal version).
- `test_confirmation_requires_current_proposal` (P2) — confirm with stale
  `selection_version` → 409, selection untouched.
- `test_llm_cannot_invent_state_transition` — already structurally true; pin it: a team
  turn whose messages CLAIM confirmation but call no tool leaves
  `scene_gate.confirmed == False` and the guards still refuse storyline writes.

## 13. Patch Plan

- **P0** (stops the loops): sentinel constant + `TextMentionTermination` wiring +
  `awaiting_user` result status; Gate-B refusals carry the sentinel too. (~3 files:
  production_tools.py, production_agents.py, production_orchestrator.py + tests.)
- **P1** (deterministic outcome): UI/done-card mapping for `awaiting_user`; job result
  no longer says `weak` for gate pauses.
- **P2** (resilience): `selection_version` bound confirm; sentinel documented in
  docs/09; contract test for tool-note wording.
- **P3** (context diet): phase-scoped task text (omit SOURCE MATERIAL pre-gate);
  optionally lower `max_turns` for phase-1 runs.

**Non-goal:** prompt tuning as the fix. The prose can stay as advisory color; every load-
bearing behavior above is deterministic code.
