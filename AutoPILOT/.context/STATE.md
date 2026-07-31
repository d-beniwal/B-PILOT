# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-07-28_

## Now working on

- **Slice 6 done (2026-07-28): richer plan-catalog lookup tools.** User's
  "AutoPILOT isn't smart enough about plans" complaint traced to the agent
  loop only ever knowing 2 hardcoded templates. New `autopilot/plan_catalog.py`
  reuses `gui_qt/plan_parser.py:find_plan_specs()` (AST-only, never imports)
  to expose a two-tier real plan catalog: "vetted" (the profile's
  `visible_plan_files`, fully documented) and "extended" (a hand-maintained
  allowlist + each beamline's own `<bl>_plans/*.py`, often undocumented).
  3 new read-only tools in `autopilot/tools.py` (`list_all_plans`,
  `describe_plan`, `list_scan_building_blocks`) wired into
  `pipeline.converse()`'s lookup-tool dispatch; system prompt now explicitly
  distinguishes draftable (`list_devices`/`list_plans`) from
  discussable-only (the 3 new tools). Does **not** change what AutoPILOT can
  draft — still only `step_scan`/`count`. Verified offline (dup-free across
  all 3 real profiles, `_NODEFAULT`-serialization regression test passes)
  and live against real Argo (tomography-scan question correctly names
  `tomoscan_sw` with real params; `mpe_count` parameter question answered
  correctly; building-blocks question answered correctly; existing
  `step_scan`/`count` draft generation unchanged). Full design rationale
  and two bugs caught during implementation (dedup-hashability, vetted-vs-
  extended name collisions) in DECISIONS.md.
- **Slice 5 (uncommitted, not yet verified): named theme system for the
  chat dock.** New `autopilot/gui/themes.py` — a `Theme` dataclass and 4
  presets (`cyberpunk_neon` default, `matrix_terminal`,
  `sleek_monochrome`, `classic` — the only one that falls back to
  B-PILOT's own light palette) each carrying bg/panel/accent/bubble colors
  plus a `font_family` (monospace for the 3 "AI-layer" themes, `"inherit"`
  sentinel for `classic`) and a `glow` flag (`QGraphicsDropShadowEffect` on
  the composer). `build_dock_stylesheet()` emits QSS scoped to
  `QDockWidget#AutoPILOTChatDock` so it overrides B-PILOT's app-wide style
  only for this dock. `settings.py` gained a `theme` key (defaults to
  `cyberpunk_neon`) plus a load-time migration: a settings file saved
  before `theme` existed has its old (light) bubble colors dropped so they
  don't pair with the new dark default chrome. `settings_dialog.py`'s
  Appearance card gained a theme picker that **previews** a preset into
  the existing color swatches (swatches stay hand-tweakable after) —
  `values()` always reads back whatever the swatches currently show, not
  the combo selection. **Not yet exercised**: no offscreen Qt smoke test
  or live B-PILOT run found for this feature (unlike Slices 1-4, which
  each record explicit verification below/in DECISIONS.md) — treat as
  unverified until confirmed.
- **Slice 4 done (2026-07-23): real multi-turn agent loop.** Replaced the
  single-forced-tool-call pipeline with `pipeline.converse()`
  (`tool_choice: "auto"`, up to `max_turns=4`), added read-only lookup
  tools (`autopilot/tools.py`: `list_devices`, `list_plans`) and two ways
  to avoid guessing (`ask_user`, `cannot_generate_plan`). Conversation
  memory lives in the caller (`chat_panel._ChatWorker._history`); a **New
  Chat** button resets it. Verified live against real Argo (self-
  correction, plain-text decline, two-turn memory) — see DECISIONS.md for
  the full reasoning including a same-session superseded first attempt.
- **Slice 3 done (2026-07-23): settings dialog + chat aesthetics.** New
  `autopilot/settings.py` (Qt-free JSON persistence, gitignored) +
  `autopilot/gui/settings_dialog.py`. Bubble-style transcript
  (`<table>`-based, Qt rich text has no `border-radius`); auto-growing
  composer built on `QTextEdit` not `QPlainTextEdit` (the latter's
  `document().size()` is unreliable before the widget is shown).
- **Fixed same session:** newer models (e.g. `claude-sonnet-5`) reject the
  `temperature` param outright (HTTP 400) — `llm_client` now retries once
  without it.
- **Slice 1+2 (done 2026-07-23):** NL plan-builder core (Argo-backed) and
  the chat dock embedded in B-PILOT via a guarded import — full history in
  DECISIONS.md.

## Next steps

- **Commit pending work.** As of 2026-07-28, `git status` shows Slices 3-6
  (settings dialog, multi-turn agent loop, theme system, plan-catalog lookup
  tools) all still uncommitted on `main` — last commit on disk is "Add slice
  2". Verify the theme system (below) before committing, or split into
  separate commits if that's cleaner.
- **Verify Slice 5 (theme system)** — no smoke test or live run found for
  it yet. At minimum: an offscreen Qt check that each of the 4 presets
  renders without error and that `settings.load()`'s pre-`theme` migration
  actually drops stale colors; ideally a live B-PILOT run to eyeball the
  glow effect and monospace font switch.
- **Two explicitly deferred, larger initiatives** (see `.context/
  DECISIONS.md` Slice 4 and `mpe_bluesky/.context/ARCHITECTURE.md`'s
  AutoPILOT section for the survey backing both):
  - **MCP server** (per the original 2026-07-22 architecture decision) —
    needs `pip install mcp` (absent from every environment on this machine
    today) plus a real MCP client to test against. `autopilot/tools.py`'s
    functions are already plain enough to wrap with minimal glue once built.
  - **Queue/dispatch tools** (enqueue, human-approval gate) — needs
    `build_command()` extracted from the Qt-coupled `gui_qt/plan_runner.py`
    (confirmed still absent) and a `queue_store.py` schema change (an
    `origin`/status field so `queue_runner.py`'s dispatch loop can skip
    agent-originated items until a human approves). Touches the literal
    `kc.execute()` hardware-dispatch boundary — treat as its own
    carefully-reviewed initiative, not bundled with chat-dock work.
- Add more templates as real usage demands (grid scans, relative scans,
  continuous acquisition) — same spec-first pattern: a new `Template` entry
  in `plan_context.py` plus a `_CALL_BODY` entry in `plan_renderer.py`.
  `list_plans`/`ask_user`/`cannot_generate_plan` are already
  template-count-agnostic, no loop changes needed.
- Wire promotion: today a human manually moves a reviewed file from
  `generated_plans/` into `instrument/plans/`. Could add a `--promote` CLI
  flag or a "Promote" button on the chat dock later, still human-triggered.
- Eventually: NL search over databroker (structured filter-dict, not
  embeddings — see `.context/DECISIONS.md`), deferred from the original
  scoping conversation, unchanged. `gui_qt/viewer.py` has the raw building
  blocks (`connect_catalog`, `list_runs`) but no filter-by-metadata logic
  yet.
- Create the GitHub repo (`d-beniwal/AutoPILOT` or similar) once `gh` is
  available or the user creates it via the web UI, then add the remote and
  push. **Still explicitly deferred by the user as of 2026-07-23** — AutoPILOT
  remains local-git-only; this workstation's disk is the only copy.

## Open questions / blockers

- `gh` CLI not installed/authenticated on this dev Mac — GitHub repo
  creation still deferred (user's explicit choice, re-confirmed 2026-07-23).
- `mpe_bluesky` (the parent project) has no git repo at all, and
  `instrument/iconfig.yml` has live plaintext MongoDB credentials — flagged
  to the user 2026-07-23; they chose to leave it as-is for now. AutoPILOT's
  own `.context/` is unaffected (it's inside AutoPILOT's own repo), but
  `mpe_bluesky/.context/*.md` (where B-PILOT-level facts also live) has zero
  version history/backup.
- NL→search-filter design for databroker queries not yet decided.

## Recent changes (last 3-5 sessions, dated; drop the oldest as it grows)

- 2026-07-28: Slice 6 — two-tier real plan catalog (`plan_catalog.py`) + 3
  new lookup tools (`list_all_plans`, `describe_plan`,
  `list_scan_building_blocks`) wired into `pipeline.converse()`. Verified
  offline and live against real Argo — see "Now working on" above.
- 2026-07-24: Slice 5 — named theme system for the chat dock
  (`gui/themes.py`, 4 presets) wired into `settings.py`/
  `settings_dialog.py`/`chat_panel.py`. Uncommitted; not yet verified (no
  smoke test found) — see "Now working on" above.
- 2026-07-23: Slice 4 — multi-turn agent loop (`converse()`), lookup tools,
  conversation memory, `classify()` removed. Verified live against real
  Argo (self-correction, plain-text replies, two-turn memory).
- 2026-07-23: Fixed `temperature` rejected outright by newer models
  (retry-without-temperature in `llm_client`); Slice 3 — settings dialog +
  chat bubble/composer aesthetics.
- 2026-07-23: Slice 2 (chat dock in B-PILOT) implemented and verified with
  two offscreen Qt smoke tests (presence + absence).
