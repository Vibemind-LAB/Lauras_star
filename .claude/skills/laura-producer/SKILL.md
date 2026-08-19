---
name: laura-producer
description: Drive a Laura video production end-to-end over the laura MCP server — scene gate, storyline, grounded script, approve, deterministic render. Use when the user asks to produce/cut/export a video via Laura from this session.
---

# Laura Producer

Preconditions: the Laura desktop app is running (every tool fails with "Laura app is not
running" otherwise). All writes go through the MCP tools — never edit workspace files.

## Production contract (order is mandatory)
1. `list_assets` / `get_transcript` / `get_shots_and_scenes` — read before you write.
2. `start_production(asset_id, task, …)` — creates an author session (gates armed).
3. `propose_scenes` with candidates you justify from transcript + rough-cut scenes.
   THE USER picks: present the proposal, wait for their answer, then `confirm_scenes`
   with the `selection_version` from `production_status` (a changed proposal 409s — re-read).
4. `save_storyline` — chapters reference scene windows; only confirmed scenes.
5. `save_script_chapter` per chapter. Grounding rule: every claim comes from the
   transcript of the chosen material; never invent facts. The capacity guard measures
   speech rate from real timings — respect its rejections, shorten instead of arguing.
6. Show the user the script; on their explicit yes call `approve_script`. Everything
   after (voice → cutlist → contact sheet → render → QA) is deterministic and automatic —
   watch it via `production_status` / `job_status`, show `get_contact_sheet` when ready.

## Rules
- Language follows the user's instruction language (task + script in the same language).
- Look before you cut: `get_frame` around any boundary you are unsure about.
- Deletes (productions, assets, projects) only via `laura_api` and only after the user
  confirmed in this conversation. The input footage is never deleted by a production delete.
- If a tool answers 409 "team session", this production belongs to the in-app chat — do
  not write to it; offer to start a fresh author session instead.
