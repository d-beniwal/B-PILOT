# DECISIONS — append-only, newest first

Each entry: what was decided and *why* (the reasoning that would be expensive
to reconstruct later). Never rewrite history; add a new entry to supersede.

> **2026-08-01 merge note:** this file merges two previously-separate decision
> logs into one, now that AutoPILOT lives inside B-PILOT's own repo (see the
> 2026-07-31 entry below) and B-PILOT has finally been given its own
> `.context/` folder (previously all of B-PILOT's history lived in the parent
> project's `mpe_bluesky/.context/DECISIONS.md`, and AutoPILOT kept its own
> separate `B-PILOT/AutoPILOT/.context/DECISIONS.md`). Entries below are
> unedited except for this note — if an old entry references
> `AutoPILOT/.context/...` or `mpe_bluesky/.context/ARCHITECTURE.md`'s
> AutoPILOT/B-PILOT sections, that content now lives in *this* file and in
> `B-PILOT/.context/ARCHITECTURE.md`. mpe_bluesky's `.context/DECISIONS.md`
> keeps only its one genuinely instrument/QS-scoped entry (2026-07-15,
> adopting the two-layer `.context` system for that project).

## 2026-08-01 — GUI layout cleanup: single launch mode, menu-ized viewer/AutoPILOT, shutdown clears the session log

Four toolbar/menu changes requested to declutter the main window and fix a
transcript carry-over bug, all confined to `gui_qt/`:

- **"Launch script" mode removed entirely, not just hidden.** It never
  worked with B-PILOT (the user's words) and was fully self-contained — a
  repo-wide grep confirmed only `main_window.py`/`config.py`/
  `config_dialog.py` referenced `launch_mode`/`launch_script`/
  `script_run_mode`, so removal was safe with no other module affected.
  Deleted the mode combo, `_launch_via_script()`, the "Run as" control, and
  the config keys/dialog field outright (user's explicit choice over
  leaving dead config behind) rather than just hardcoding embedded mode in
  the UI. `dm_experiment`/`setup_file` were kept — they're read by the
  embedded launcher too, not script-mode-only despite living in what used
  to be called the "script params row".
- **AutoPILOT gets a second toggle (Python menu, checkable action) instead
  of replacing the Configuration → Appearance checkbox.** User's explicit
  choice — both write the same `autopilot_enabled` config key and
  `_sync_autopilot_dock()` call; `_open_config()` now also resyncs the menu
  action's checked state after a Configuration save (via `blockSignals` to
  avoid a redundant toggle round-trip) so the two controls can't drift out
  of sync.
- **Session log cleared on shutdown, not on attach — hooked at
  `kernel_session.stop()`, not in the GUI layer.** The transcript file path
  is a fixed per-beamline `kernel.log` (not per-launch), so previously a
  shutdown followed by a fresh launch would show the old and new sessions
  concatenated. `stop()` is only ever called for a kernel this GUI itself
  started and is now killing (confirmed via `console_panel.py::shutdown()`,
  which calls `shutdown_kernel(cf)` instead for an arbitrary/attached
  kernel) — so adding `p["log"]` to `stop()`'s existing cf/sidecar cleanup
  loop cleanly means attach-then-view-history is never affected, only an
  intentional shutdown of our own kernel. `main_window.py`'s
  `_shutdown_kernel()`/`_restart_kernel()` also blank the visible panel
  immediately (`session_log.load(None)`) rather than waiting for the next
  launch to overwrite stale text.
- Verified via offscreen Qt smoke tests only (`MainWindow`/`ConfigDialog`
  construct cleanly, expected widgets present/absent) — not yet click-tested
  on a real desktop; flagged in STATE.md as the next verification step,
  same caution as the 2026-07-24 usability round's dropdown-timing class of
  bugs that offscreen testing can't catch.

## 2026-08-01 — Slice 7: docstring-drafting assistance, read-only and chat-only

User wanted AutoPILOT to read a real plan file with non-compliant docstrings
and draft replacements matching B-PILOT's grammar, tested against
`instrument/plans/s20idd_plans/user_plans/nfdev_jul26.py` (5 plan functions:
3 undocumented, 2 with a `PARAMETERS`-not-`Parameters` docstring documenting
`det`/`sms` args that don't even exist in the real signature).

- **New grammar functions added to `gui_qt/plan_parser.py` itself, not
  reimplemented in AutoPILOT.** `find_plan_functions_raw()` and
  `validate_docstring_text()` reuse the existing private helpers
  (`_module_all`, `_is_generator`, `_signature`, `_parse_parameters`,
  `_parse_typespec`) rather than re-deriving the same regex/AST logic in
  `autopilot/`. Both are purely additive — no existing function's behavior
  changed. Keeps `plan_parser.py` the single authoritative source of the
  grammar, same principle `plan_catalog.py`/`plan_context.py` already
  follow.
- **`read_plan_file` is hard-scoped to `instrument/plans/`, not the whole
  project root.** `instrument/iconfig.yml` (a sibling of `instrument/plans/`
  under the project root) holds live plaintext MongoDB credentials
  (flagged, left as-is, 2026-07-23) — restricting the resolved path to
  `PLANS_DIR` specifically means this new file-reading tool can never reach
  it, even by accident or a bad relative path (`../` traversal tested and
  refused).
- **`validate_docstring` takes a batch of drafts, not one call per
  function.** A 5-function file would otherwise cost 5 validate turns on
  top of the read + reply turns; batching keeps a typical file's fix-up
  conversation to ~3 tool turns regardless of function count, comfortably
  inside `max_turns` (bumped 4→6 for a fix-and-recheck margin, not because
  the common case needs it).
- **Deliberately chat-only — no file write, ever.** The user was explicit
  that AutoPILOT must not modify the target file; this is a genuinely new
  capability, not a variant of the existing `propose_*` → `GENERATED_DIR`
  write path, so it was built as a pure lookup-tool pair (`read_plan_file`,
  `validate_docstring`) with the model's final plain-text reply as the only
  "output" — same shape as `list_all_plans`/`describe_plan`, just pointed at
  a real file's raw signature instead of the curated plan catalog.
- Verified live against real Argo: the model self-corrected across 3
  `validate_docstring` rounds before its final reply, produced all 5
  docstrings with correct dtypes/units and no hallucinated parameters, and
  the source file was confirmed byte-for-byte unchanged afterward (checksum
  before/after). The pre-existing `count`/`step_scan` draft flow was rerun
  and confirmed unaffected by the new tools/turn-budget change.

## 2026-07-31 — B-PILOT: folded AutoPILOT in as a tracked subdirectory instead of a separate nested repo

Reversed the 2026-07-22 decision below ("AutoPILOT is a separate nested git
repo") at the user's request, now that AutoPILOT has proven itself stable
enough to stop needing independent versioning friction. Flattened AutoPILOT's
3-commit standalone repo history into a single commit inside B-PILOT's own
repo (`1c62ebe`):

- **Old standalone history preserved, not discarded.** Bundled AutoPILOT's
  3 commits to `B-PILOT/AutoPILOT/.context/autopilot_standalone_history.bundle`
  before deleting `AutoPILOT/.git` — recoverable via `git clone` if ever
  needed, recorded in `~/.claude/skills/github/projects/b-pilot.md` and
  `mpe_bluesky.md`.
- **Checked for secrets first.** Before folding a second repo's history in,
  confirmed AutoPILOT's tracked files (its own `.gitignore` already excludes
  `autopilot_settings.json`, `generated_plans/*`) carry no credentials —
  same caution as the standing `instrument/iconfig.yml` plaintext-credentials
  flag elsewhere in this project.
- **Commits authored as the human identity, not Claude** — a standing git
  convention for this project, not new to this decision.
- Net effect: `B-PILOT/AutoPILOT/` is now a normal tracked subdirectory of
  B-PILOT's git repo (confirmed via `git status` — `AutoPILOT/.context/*`
  shows as ordinary tracked/modified files, not gitignored). This is the
  decision that eventually made today's docs-merge task possible/necessary:
  AutoPILOT's `CLAUDE.md`/`.context/` no longer need to describe a separate
  project, and B-PILOT finally warrants its own `.context/` folder instead of
  borrowing `mpe_bluesky/.context/` (see the 2026-08-01 merge note at the top
  of this file).

## 2026-07-28 — Slice 6: richer plan-catalog lookup tools (two-tier)

User's complaint: "AutoPILOT is not smart enough" about plans. Root cause
wasn't reasoning quality — the agent loop's only plan knowledge was
`plan_context.TEMPLATES` (2 hardcoded templates: `step_scan`, `count`), so it
had zero visibility into the ~150 real, exported plans in
`instrument/plans/` (tomography, alignment, grid scans, per-beamline plans).
It could neither describe them nor decline accurately.

- **Two-tier catalog, not one** (user confirmed via `AskUserQuestion`):
  `autopilot/plan_catalog.py` (new) exposes a "vetted" tier (the profile's
  existing `visible_plan_files`, fully parameter-documented) and an
  "extended" tier (a hand-maintained allowlist — `_EXTENDED_SHARED_FILES` —
  plus each beamline's own `<bl>_plans/*.py`, mostly `documented=False`).
  Same "explicit list, not recursive discovery" philosophy as
  `plan_context.TEMPLATES` / `plan_parser.SKELETON_SHAPES` — a stray one-off
  script never silently appears in what the model tells users.
- **Does not expand what AutoPILOT can draft.** Only `list_devices`/
  `list_plans` (unchanged) feed the `propose_*_plan` path. The 3 new tools
  (`list_all_plans`, `describe_plan`, `list_scan_building_blocks`) are
  read-only/informational only — verified live that draft-generation for
  `step_scan`/`count` is byte-for-byte unchanged after this change.
  `_build_system_prompt()` now explicitly tells the model these two tool
  families cover different things, so it stops declining/guessing about
  plans it can discuss but not draft.
- **Vetted always wins on name collision.** Some `_EXTENDED_SHARED_FILES`
  entries (`scans_standard.py`, `scans_stationary.py`, `scan_skeletons.py`)
  are the un-reformatted originals of files already in `visible_plan_files`
  via their `bpilot/*.py` copies — same plan, strictly worse (undocumented)
  info. First implementation surfaced both as separate `describe_plan`
  matches for the same name (confusing); fixed by computing the vetted tier
  first and dropping any extended-tier entry whose name is already vetted,
  rather than trying to merge/dedupe them as if genuinely different plans.
- **`_NODEFAULT` serialization bug caught before it shipped.** `ParamSpec.default`
  is `plan_parser._NODEFAULT` (a private, non-JSON-serializable sentinel) for
  every required parameter. `PlanParam` construction normalizes this to
  `default=None if spec.required else spec.default` — without it,
  `describe_plan` would raise `TypeError` inside `json.dumps()` the first
  time the model asked about almost any real plan. Regression-tested offline
  and confirmed live (`mpe_count`/`tomoscan_sw` describe calls both work).
- **Dedup comparison uses `==`, not a `set`.** `PlanParam.choices` is a
  `list`, so a frozen-dataclass tuple containing it isn't hashable — an
  initial `{...}` set-based identical-check raised `TypeError: unhashable
  type: 'list'` on the very first real profile. Fixed by comparing tuples
  pairwise with `==` instead of hashing them into a set.
- Verified live against real Argo (haiku model): a tomography-scan question
  now names `tomoscan_sw` specifically with correct params (not a generic
  decline); an `mpe_count` parameter question gets a correct, specific
  answer; a building-blocks question correctly lists all 12 `per_step`
  names. Offline: `plan_catalog.load()` is dup-free across all 3 real
  profiles (`s1id`/`s20idd`/`s20ide`).

## 2026-07-24 — B-PILOT: added `gui_qt/scan_building_discovery.py` for scan-skeleton building blocks

Static, never-imports discovery of `plan_opener`/`per_step`/`plan_closer`
(from `scan_hw_triggering.py`/`scan_sw_triggering.py`) and
`suspender`/`pseudo_suspender` (from `suspenders.py`/`suspenders_pseudo.py` +
per-beamline `<bl>_suspenders.py`), mirroring `device_discovery.py`'s
approach. Parses each `__all__`'s section comments via `tokenize` (ast alone
discards comments) — the comment grouping is the only reliable signal
distinguishing e.g. true suspenders from pseudo-suspenders (see
`.context/DOMAIN.md`). New profile config keys
`plan_building_search_paths`/`suspender_search_paths`/`plan_building_blocks`
(`gui_qt/config.py` DEFAULTS) — the discovered catalog is **persisted**, not
live-rescanned like devices, since these change rarely. New "Scan blocks"
Configuration-dialog tab (after Devices) with a Discover button, read-only
(no shown/hidden concept needed, unlike Devices). Deliberately does NOT wire
the catalog into `plan_runner.py`'s actual skeleton form
(`acquisition_modes` combo, suspenders free-text fallback) — user chose
"discovery + storage only" scope for this piece; see the scope-decision
entry immediately below for why the boundary is drawn where it is. Verified:
py_compile + an offscreen Qt smoke test across all 3 profiles (tab renders,
Discover is idempotent, `.values()` round-trips).

## 2026-07-24 — B-PILOT: scope decision for direct `scan_skeletons.py` handling — wrapper plans are responsible for exposing their own args

Scoped the "B-PILOT should handle `scan_skeletons.py` plans directly, not
just via docstring-reformatted wrapper copies" goal. Decision: **if a plan
wraps a skeleton call but doesn't expose one of its args at the wrapper's own
function signature, B-PILOT will not reach through to expose it** — that's
the wrapping plan author's responsibility, not something the GUI
compensates for. Reasoning: reaching through a wrapper to synthesize form
fields for args the wrapper itself chose to hide would require guessing
intent (is a fixed arg fixed on purpose, or just forgotten?) and risks
producing a form that lets a user set something the plan author deliberately
locked down. Referenced by memory `[[scan_skeletons_scope]]`. See
`.context/DOMAIN.md`'s "`scan_skeletons.py` building blocks" section for the
domain knowledge behind `plan_opener`/`per_step`/`plan_closer`/suspenders
this scoping decision applies to.

## 2026-07-24 — B-PILOT: category dropdown fix above was incomplete

The first fix (deferring `_rebuild_device_list()` via
`QTimer.singleShot(0, ...)` so a combo's own signal handler finishes before
the list rebuilds) addressed "adding a 2nd category dropdown and picking a
value closes the dropdown" but a worse variant then appeared: **every**
dropdown collapsed the instant it opened, before picking anything. Real root
cause: `editingFinished` fires on any focus-out, not just real edits, so
clicking a different dropdown blurred the previous one and scheduled a
rebuild that landed right as the next popup opened. Fixed by having
`_CategoryDropdowns._emit()` only fire `on_change` when the category list
actually changed (tracks `self._last_emitted`). Verified offscreen (spurious
focus-out is now a no-op; genuine selection still fires once) — flagged as
still needing a real desktop click-test since this bug class is about live
mouse/focus timing offscreen Qt can't fully reproduce.

## 2026-07-24 — B-PILOT: fixed category dropdown "closes on select" bug

Adding a 2nd category dropdown and picking a value in it closed the dropdown
instead of registering the pick. Root cause: `_apply_device_categories`
(`config_dialog.py`) called `self._rebuild_device_list()` synchronously, but
it's invoked from *inside* the just-picked combo box's own
`activated`/`editingFinished` signal handler — the rebuild tore down and
recreated the whole device list (including that very combo) mid-signal.
Fixed by deferring the rebuild with
`QtCore.QTimer.singleShot(0, self._rebuild_device_list)` so the combo's own
signal handling finishes first; the override dict update itself still
happens synchronously. Verified offscreen against the real `s20ide` profile.
**Turned out incomplete** — see the entry above for the follow-up fix.

## 2026-07-24 — B-PILOT: device categories are dropdowns again, not free text

Reverted the free-text "comma-separated categories" `QLineEdit` (from the
2026-07-23 device_category_overrides change below) back to actual dropdowns
per the user's explicit ask. New `_CategoryDropdowns` composite widget
(editable `QComboBox` per assigned category + "+"/"×" to add/remove, always
keeps ≥1 row) replaces `_make_category_field`'s old `QLineEdit`;
`_apply_device_categories` now takes a `list[str]` straight from the widget
instead of parsing free text. Combo dropdowns are populated from every
category currently in use in the profile but stay editable so a brand-new
category can still be typed. Verified with offscreen-Qt smoke tests (card
order, style toggling, row widget order, and `_CategoryDropdowns`
add/select/remove against the real `20ide` profile — 84 devices, 5
categories, all got a working widget).

## 2026-07-24 — B-PILOT: BEAMMODE/TESTMODE share Stop-run's row; other usability polish

Four refinements after user feedback: (1) removed the now wholly-redundant
"Build/Update" button — the command preview already updates live, so the
button and its dead `_update_command` handler were deleted outright. (2)
reordered the plan-runner's resizable stack so **Run notes sits above the
Command box** (was the reverse). (3) the "✎ Edit" toggle now visibly
highlights (accent-orange background) while checked, and the command box
border switches to a 2px accent border (was a 1px green border that read as
"just colored," not "editing") — both reset to blank stylesheets on
toggle-off. (4) BEAMMODE/TESTMODE (`ModeButtonBar`) no longer sit in their
own row below Stop run/Shut down kernel — `RunControlBar` gained
`add_trailing_widget()`, which inserts a widget into its own top button row
just before Shutdown; `main_window.py` calls it once instead of adding
`mode_buttons` as a second row. All verified with offscreen-Qt smoke tests
— no redwood exercise yet as of this entry.

## 2026-07-24 — Slice 5: named theme presets instead of only ad-hoc colors

Slice 3 gave the chat dock 5 independently-pickable colors but no notion of a
coherent "look" — a user had to hand-match 5 swatches to get anything other
than the shipped default. Added `autopilot/gui/themes.py`: a `Theme`
dataclass and a small curated set of presets (`cyberpunk_neon` — new default,
`matrix_terminal`, `sleek_monochrome`, `classic`).

- **Themes deliberately do NOT reuse `gui_qt.style` colors**, except
  `classic`. The point of the dock having its own look is to visually read
  as a distinct AI layer rather than blending into B-PILOT's light theme;
  `classic` exists as the explicit opt-out for anyone who wants the old
  blend-in behavior back.
- **`build_dock_stylesheet()` is scoped to `QDockWidget#AutoPILOTChatDock`**
  so it overrides B-PILOT's app-wide stylesheet only for this dock and its
  children, never touching `gui_qt/style.py` or global Qt state — same
  non-invasive spirit as the rest of AutoPILOT's guarded-import relationship
  with B-PILOT.
- **A theme carries `font_family` and `glow`, not just colors** — the 3
  "AI-layer" presets use a monospace font and an optional
  `QGraphicsDropShadowEffect` glow on the composer for a HUD feel;
  `font_family="inherit"` is a sentinel `classic` uses to keep B-PILOT's own
  non-monospace app font instead of forcing monospace everywhere.
- **Settings dialog previews, not locks.** Picking a theme in
  `settings_dialog.py` writes its preset colors into the existing 5 color
  swatches rather than hiding them — `values()` always reads back whatever
  the swatches currently show, so a user can pick a theme as a starting
  point and then hand-tweak one swatch without losing the rest.
- **`settings.py` migration for pre-theme settings files**: a settings file
  saved before the `theme` key existed carried colors chosen for the old
  single light appearance; `load()` now drops that stale color set (but
  keeps model/temperature/Argo overrides) when `theme` is absent, so the new
  dark default doesn't end up paired with leftover light bubbles.
- **Not yet verified**: unlike Slices 1-4, no offscreen Qt smoke test or live
  B-PILOT run was found for this feature when this entry was written — flag
  it for verification before relying on it.

## 2026-07-23 — B-PILOT: silent status polling via `user_expressions`

Added a way to poll RunEngine/queue status from the kernel without it
appearing as a visible echoed cell in the console/transcript (distinct from
the deliberate "queue-run cells show in the console" decision below, which
is about *user-initiated* plan runs). Uses Jupyter's `user_expressions`
mechanism on a silent `execute_request` — evaluates expressions server-side
and returns their reprs in the reply metadata without an `In [N]:` echo or
IOPub stream output, so periodic polling (e.g. "is RE busy") doesn't spam the
transcript.

## 2026-07-23 — B-PILOT: `device_category_overrides` becomes list-valued (multi-category devices)

A device can now belong to more than one category (e.g. a combined
motor+detector fixture), motivating a schema change from a single string
category override to a list. UI became a free-text "comma-separated
categories" field at this point (later reverted to dropdowns — see the
2026-07-24 entry above once the user pushed back on free text).

## 2026-07-23 — B-PILOT backend survey for future agent/queue tooling (driven by AutoPILOT's Slice 4)

Direct code survey of B-PILOT's backend, prompted by AutoPILOT's Slice 4 work
needing to know what's actually safe to wrap as agent tools. Findings (see
`.context/ARCHITECTURE.md`'s "AutoPILOT" section for the merged, up-to-date
version of this survey):

- **Qt-free, JSON-serializable-data modules ready to wrap as tools:**
  `gui_qt/config.py` (`list_profiles()`, `active_profile()`,
  `profile_values(name)`, `as_dict()`, `get(key)`); `gui_qt/device_source.py:
  get_catalog()` (config-filtered device catalog — prefer this over raw
  `device_discovery.scan()` for a "list devices" tool, since it already
  applies the human-curated `device_selection` visibility filter);
  `gui_qt/plan_parser.py:find_plan_specs(filepath)` (AST-only, never imports
  the plan module); `gui_qt/queue_store.py` (`load()`, `add()`,
  `set_item_status()`, `remove()`, `move()`, `set_state()` — all plain dict
  I/O, `flock`-guarded).
- **`build_command()` does not exist.** The kwargs→`RE(plan(...))` string
  logic is still spread across Qt-widget-coupled `PlanRunnerPanel` methods in
  `gui_qt/plan_runner.py`: `_parse_params()`, `_make_import_line()`/
  `_make_re_line()`, `_compose_lines()`. Extracting a standalone, Qt-free
  `build_command(plan_name, values, params, *, module, notes="") ->
  tuple[str, str]` is the one prerequisite before any agent can build
  runnable commands from structured kwargs instead of writing draft files.
- **The dispatch-to-kernel boundary is exactly two call sites in the whole
  codebase**: `gui_qt/queue_runner.py`'s dispatch loop (`kc.execute(nxt
  ["command"], ...)`, only picks up items whose status is `WAITING`) and
  `gui_qt/console_panel.py`'s interactive "Run" button. Nothing else ever
  reaches a live kernel's `execute()`.
- **`queue_store.py`'s schema has no pending-approval state** — item status
  is only `waiting|running|done|error`, queue state is only
  `idle|running|paused`. A human-approval gate for agent-originated queue
  items needs an additive schema change (e.g. an `origin: "agent"|"human"`
  field per item + a `queue_store.approve()` function) and a filter change in
  `queue_runner.py`'s dispatch loop — `queue_panel.py`'s status/state
  dictionaries already tolerate unknown values via `.get(..., default)`, so
  this wouldn't crash the GUI on its own, just render with no distinct color
  until that dict is updated too.
- **`gui_qt/viewer.py` has the building blocks for a "search past runs"
  tool but no filtering yet** — `list_catalogs()`, `connect_catalog(name) ->
  (catalog_obj, status)`, `list_runs(cat, offset, limit) -> (rows, total,
  uids)` give plain `(uid, start_dict, stop_dict)` tuples, but `catalog_obj`
  itself is a live, non-JSON-serializable databroker object (must be cached
  server-side, only derived data handed to an LLM), and there's no
  filter-by-metadata logic today (`list_runs` only paginates newest-first).

## 2026-07-23 — Slice 4: multi-turn agent loop replaces single forced tool call

The single-shot "force exactly one tool call" design from Slice 1 had a real
failure mode, discovered live: a request that wasn't actually a scan
description (*"Create a copy of this generate plan and save it as
test.py"*) still produced a confident "Wrote draft plan" response with
fabricated generic defaults, because `tool_choice: {"type": "tool", "name":
...}` gives the model no way to decline. Separately, a device name not valid
on the active profile (asked for `samE`, active profile was `s20idd` which
only has `samD`) was silently substituted with zero indication to the user.

- **First fix attempt (superseded same session): two-tool forced choice.**
  Added `plan_spec.DECLINE_TOOL_NAME`/`build_decline_tool_schema()` and
  changed `tool_choice` to `{"type": "any"}` (forces *some* tool call, model
  picks which of two) — fixed the "confident fabrication" failure mode and
  was verified live against Argo. Superseded within the same session by the
  fuller redesign below once it became clear the same mechanism
  (`tool_choice: "auto"` + more tools) could also fix classification and add
  real lookup capability, not just a binary propose/decline choice.
- **Final design: drop `plan_context.classify()` entirely.** The keyword
  heuristic (`"count" in text and "scan" not in text -> count, else
  step_scan`) was a workaround for being forced into exactly one tool call —
  with `tool_choice: {"type": "auto", "disable_parallel_tool_use": True}`,
  every template's proposal tool is offered every turn and the model picks
  (or asks, or declines) based on full understanding, strictly better than a
  2-keyword heuristic. `disable_parallel_tool_use` keeps the loop simple: a
  turn is either plain text, one lookup call, or one terminal call — never
  several things at once, so ending the loop on a terminal call never leaves
  an unanswered sibling `tool_use` block.
- **New read-only lookup tools** (`autopilot/tools.py`, NEW file):
  `list_devices`/`list_plans`, both pure wrappers over data AutoPILOT
  already loads (`device_catalog.DeviceCatalog`, `plan_context.TEMPLATES`) —
  no new `gui_qt` imports needed. These let the model double-check a device
  name or discover what's actually buildable instead of guessing, directly
  addressing both original failure modes at the source.
- **New `ask_user` terminal tool** (`plan_spec.ASK_USER_TOOL_NAME`),
  alongside `cannot_generate_plan` — distinct semantics: "one resolvable
  detail is missing, ask" vs. "this isn't buildable at all, don't ask."
- **`llm_client.call_with_tool()` -> `call()`**: generalized from "force one
  of N tools, return its input dict" to "one turn of a conversation,
  `tool_choice: auto`, return the raw response" — the loop itself now lives
  in `pipeline.py`, not the client. Same `temperature`-deprecated retry
  logic, unchanged.
- **`pipeline.generate_plan()` -> `pipeline.converse()`**: the real loop
  (`history` in, `(PlanResult, history)` out, `max_turns=4` default). Every
  `tool_use` gets an immediate matching `tool_result` appended (even a
  placeholder `"Acknowledged."` for terminal tools) specifically so a
  returned `history` is always valid to resume from later — Anthropic's API
  rejects a next call whose most recent assistant turn has an unanswered
  `tool_use`. `generate_plan()` still exists as a thin single-shot wrapper
  (`converse(request, history=None, ...)[0]`) so the CLI's call site is
  unchanged.
- **Conversation memory lives in the caller, not the pipeline.**
  `pipeline.py` stays stateless; `chat_panel._ChatWorker` holds
  `self._history` across turns and passes it back in. A new **New Chat**
  button (`reset_conversation()`) is the deliberate escape valve against
  unbounded context/cost growth — token minimization was an explicit Slice-1
  goal and memory works directly against that without a way to reset.
- **`PlanResult.tool_calls: list[str]`** (new field, alongside the existing
  `tool_name`) records the *whole* trail for a turn (e.g. `["list_devices",
  "propose_step_scan_plan"]`), not just the terminal decision — surfaced in
  the chat bubble header and the CLI's `Tools used:` line.
- **Verified live against real Argo**, not just mocks: a bogus device name
  now gets `cannot_generate_plan` with a specific correction ("did you mean
  samE?") instead of a silent wrong substitution; a genuinely non-scan
  request now gets a helpful plain-text reply (no tool call at all) instead
  of a fabricated plan; a two-turn exchange (turn 1 asks which motor, turn 2
  supplies just `"samE, with 1 second exposure on pimega"`) correctly
  completed the plan using turn 1's range/step-count from memory alone.
- **Deferred, not built this session** (see `.context/ARCHITECTURE.md`'s
  AutoPILOT section for the merged backend survey): an MCP server (the `mcp`
  SDK is installed nowhere on this machine — needs a new dependency and its
  own testable pass) and queue/dispatch tools (needs `build_command()`
  extracted from the Qt-coupled `plan_runner.py`, confirmed still absent,
  plus a `queue_store.py` schema change for human-gated dispatch — this
  touches the literal `kc.execute()` hardware-dispatch boundary and deserves
  its own carefully-reviewed initiative, not bundling into a "make the chat
  smarter" pass).

## 2026-07-23 — Fix: `temperature` rejected outright by newer models

Adding a user-facing temperature setting (see Slice 3 below) surfaced a real
runtime failure live: Argo returned an HTTP 400 ("`temperature` is
deprecated for this model") for `claude-sonnet-5`, but not for the default
`claude-haiku-4-5-20251001`. Rather than silently dropping the setting for
all models (loses it for models that *do* support it) or hard-failing every
request for models that don't, `llm_client`'s Argo call now catches that
specific `BadRequestError` and retries once with `temperature` popped from
the kwargs — verified with a mock reproducing the exact error message, and
confirmed the retry is skipped (no wasted second call) for unrelated 400s.

## 2026-07-23 — Slice 3: chat dock aesthetics, auto-growing composer, settings dialog

Three related UI/UX improvements to the chat dock, requested together:

- **Bubble-style transcript.** `QTextEdit`'s rich-text engine is a CSS-2.1
  subset (no `border-radius`, no flexbox) so "bubbles" are faked with
  `<table align="left|right" width="78%">` blocks carrying background-color
  + border + padding — real rounded corners weren't worth chasing.
- **Auto-growing composer built on `QTextEdit`, not `QPlainTextEdit`.**
  `QPlainTextEdit.document().size()` reports stale/wrong values before the
  widget has been shown/painted (`QPlainTextDocumentLayout` lazily lays out
  only visible blocks) — confirmed by direct comparison in an offscreen Qt
  test. `QTextEdit` + `setAcceptRichText(False)` + explicit
  `document().setTextWidth(viewport().width())` gives a reliable height even
  offscreen, at the cost of being a "plain-text-behaving" rich-text widget
  rather than a true plain-text one. Enter sends; Shift+Enter inserts a
  newline; height is clamped 1–6 lines with a scrollbar past the cap.
- **Settings dialog + persistence** (`autopilot/settings.py`,
  `autopilot/gui/settings_dialog.py`, NEW files) — model, temperature, font
  size, 5 bubble/text/panel colors, a "show raw model output" debug toggle,
  and advanced Argo base_url/api_key overrides. Deliberately its own tiny
  JSON file (`AutoPILOT/autopilot_settings.json`, gitignored) rather than
  hooking into B-PILOT's `config.py` profile system — that system is
  per-beamline *instrument* config with profile-switching semantics that
  have nothing to do with one user's chat-widget preferences. Mirrors
  B-PILOT's own `ConfigDialog` house style (cards, Restore
  Defaults/Cancel/Save, values previewed and only written on Save) without
  reusing its code. `_ChatWorker` now builds one `ArgoClient` up front
  (cheap/side-effect-free) and exposes `reconfigure()` so Settings → Save
  rebuilds it live, no B-PILOT restart needed.
- This directly executes on the earlier-recorded deferral (see the Slice-2
  entry below: *"No settings UI added... revisit if that need actually
  appears"*) — the need appeared once the user wanted to change model/
  behavior without editing env vars.

## 2026-07-22 — Viewer: fixed backwards pagination order; defensive socket timeout

User was on s20id/redwood testing the viewer against the live `hexm` catalog
(6000+ runs) and hit two real bugs: page 1 showed oldest runs instead of
newest, and page 2 hung forever.

- **Root cause #1 (backwards offset math)**: fixed and verified with a fake
  catalog — `list_runs()`'s offset/limit windowing was computed from the
  wrong end of the run list.
- **Root cause #2 (page 2 hang)**: likely a pymongo hang with no read
  timeout. Added a defensive `socket.setdefaulttimeout(30)` fix, but this
  could **not** be verified against real hardware from the dev machine — if
  page 2 still hangs after this fix on a future redwood session, that's the
  first thing to re-check (the timeout may need to be set at the pymongo
  client level instead of the socket-module default, which some drivers
  bypass).

## 2026-07-22 — Profile refinements: nested `device_selection`, self-documenting saves, Data Viewer settings, s1id/s20idd profiles

Follow-up to the 2026-07-21 profiles + device discovery work below, driven by
user testing:

- **`device_selection` became nested by category** (`{category: {name:
  bool}}`), matching the Devices tab's grouped-by-category display, instead
  of a flat `{name: bool}` dict.
- **Saved profiles are self-documenting** — every key is written out in full
  except the workstation-derived ones (`plans_dir`, `import_root`,
  `launch_script`, `embedded_starter_script`, `session_dir`,
  `last_kernel_connection_file`), which stay diff-only against
  `config.DEFAULTS` since baking one workstation's absolute path into a
  profile you commit to git would break the next one.
- **New Data Viewer settings tab**: `databroker_catalog`/`databroker_uri`/
  `databroker_nexus_dir`, taking priority over `viewer.py`'s original
  account-based auto-detect from `iconfig.yml` when set.
- **Shipped `s1id.json`/`s20idd.json` profiles** alongside `20ide.json` — the
  device dirs `instrument/devices/{s1id,s20idd}_devices/` already existed
  for both.
- Verified offscreen: nested `device_selection` preserves per-device edits
  like the real `gh2: false` in `profiles/20ide.json`; all 6 tabs render;
  profile switch/create/delete/save round-trips.

## 2026-07-22 — AutoPILOT is a separate nested git repo, not a B-PILOT subfolder-in-repo

Chose to give AutoPILOT its own git history (nested inside `B-PILOT/AutoPILOT/`
on disk, gitignored from B-PILOT's repo) rather than adding it as tracked
folder inside B-PILOT's existing repo. Reasoning: independent versioning and
release lifecycle (AutoPILOT will iterate much faster and more
experimentally than the GUI), and — more importantly — a structural
guarantee that B-PILOT can never accidentally grow a hard dependency on
AutoPILOT, since B-PILOT's own git history simply has no knowledge the
folder exists. This mirrors the existing precedent one level up: B-PILOT
itself is a separate git repo nested inside the non-git `mpe_bluesky` tree.

> **Superseded 2026-07-31** (see that entry above): once AutoPILOT had
> proven stable, the user asked to fold it back into B-PILOT's own repo as a
> tracked subdirectory, trading the structural git-level guarantee above for
> simpler single-repo maintenance. The *code-level* guardrail (guarded
> `try/except ImportError`, B-PILOT must run standalone with `AutoPILOT/`
> absent) is unaffected and remains the real enforcement mechanism today.

## 2026-07-22 — Architecture: MCP server over B-PILOT's Qt-free backend, human-gated dispatch

Scoped during a planning conversation that surveyed B-PILOT's codebase for
agent-callable surfaces. Key findings and resulting decisions:

- Most of B-PILOT's backend (`config.py`, `device_discovery.py`,
  `plan_parser.py`, `queue_store.py`, `viewer.py`) is already Qt-free and
  returns plain, JSON-serializable data — ready to wrap as LLM tool calls
  with minimal glue.
- Chose **tool-use via an MCP server** over embedding an agent loop directly
  in the Qt process, and over a heavier orchestration framework (e.g.
  LangGraph). Reasoning: the tool set here is small and well-bounded (no
  need for graph-based orchestration), and exposing it as an MCP server
  decouples "agent brain" from "GUI process" — any MCP client (Claude
  Desktop, Claude Code, or a future in-app chat dock) can drive the same
  tools, and B-PILOT keeps running with zero changes if no agent is
  connected at all.
- **Hard safety rule:** the agent must never get a tool that reaches
  `kernel_session`'s raw `execute()` primitive (unrestricted Python, full
  EPICS/hardware access, no sandboxing). The only path from the agent to the
  kernel is: validated kwargs (checked against `plan_parser` schemas) →
  `build_command()` → the exact same command-string shape `queue_store`
  already accepts today. LLM-authored free text never becomes executable
  code.
- **Dispatch requires human approval.** The agent may freely search data,
  list/describe plans, and enqueue items, but newly-enqueued
  agent-originated items start in a `pending`-style state requiring one
  human action before the queue runner will actually send them to the
  kernel. This reuses the queue's existing state machine rather than adding
  new plumbing.
- Left open: whether NL→data-search should be structured filter-dict
  translation (recommended — databroker start-doc fields are structured) or
  embeddings/RAG (deferred to a possible later "find similar runs" feature).
- **Status as of this merge (2026-08-01): still deferred, not built** — see
  Slice 4's entry above and `.context/ARCHITECTURE.md`'s AutoPILOT section
  for what's still missing (`mcp` SDK not installed anywhere, no test
  client available; `build_command()` extraction and the queue schema
  change are the concrete prerequisites for the dispatch half).

## 2026-07-22 — Adopt two-layer `.context` system (AutoPILOT's own bootstrap)

Adopted the STATE (disposable-but-current) + DECISIONS (permanent) split for
AutoPILOT's own context, so returning to this subsystem after a long gap is
cheap: only STATE.md auto-loads; detail is read on demand. At the time this
was AutoPILOT's own separate `.context/`, git-tracked in its own repo; as of
the 2026-08-01 merge (see the note at the top of this file) it lives here
instead, alongside B-PILOT's own history.

## 2026-07-21 — B-PILOT: beamline profiles + static device discovery (retired `device_manifest.yml`)

Made B-PILOT portable across *beamlines*, not just workstations. Every
beamline-specific setting (plan scope, launch/session commands, device
search paths, appearance) now lives in `B-PILOT/profiles/<name>.json` instead
of a single flat `gui_config.json` — the latter is now just a pointer,
`{"active_profile": "<name>"}`.

- **`gui_qt/config.py` rewritten** to manage profiles; `config_dialog.py`
  rewritten with a profile bar (New…/Save As…/Delete) above 5 tabs
  (Paths/Plans/Launch Session/Devices/Appearance — later grew a 6th, Data
  Viewer, see the 2026-07-22 refinement above).
- **New `gui_qt/device_discovery.py` replaces `device_manifest.yml`**
  (deleted): statically `ast.parse`s `__all__` names under a profile's
  `device_search_paths`, categorizing by **source filename**
  (`_motors`→motor, `_scalers`→scaler, etc.) — chosen after tracing every
  real device in `instrument/devices/s20ide_devices/` showed **class-name
  matching doesn't work on this codebase** (shared factory functions,
  generic class names). Verified filename-based inference exactly
  reproduces the old manifest's 20ide entry (0 missing, 0 extra, all 5
  categories) via a headless `device_discovery.scan()` check.
- Shipped `profiles/20ide.json` with `device_search_paths` pointing at
  `instrument/devices/s20ide_devices`.
- **Fixed a latent bug**: `device_source.get_catalog()` now keys off
  `config.get("beamline")` — previously switching beamlines never actually
  changed the device list (the old beamline picker was dead code as a
  result; superseded entirely by "switch profile" in the Configuration
  dialog).

## 2026-07-20 — GUI extracted as its own package: B-PILOT (Bluesky-PILOT)

Renamed `gui/` → `B-PILOT/` and turned it into its own git repo (nested
inside the `mpe_bluesky` tree, pushed to `github.com/d-beniwal/B-PILOT`).
Bundle contents: `gui_qt/`, `test_plans/` (later retired — see the
2026-07-20 "Real instrument/plans/ scope" entry below), `device_manifest.yml`
(later retired by the 2026-07-21 device-discovery work above),
`embedded_kernel_starter.sh`, `gui_config.json` (gitignored, per-install
active-profile pointer), `launch.py`, `README.md`,
`environments/mpe_bluesky_dev.yml` (copied from the workspace
`environments/` folder so B-PILOT is self-contained). `PORTING_NOTES.md`
moved to `.context/PORTING_NOTES.md`. All paths still resolve from the GUI's
own location via `gui_qt/paths.py` — the rename needed zero path-logic
changes, only doc-comment updates (verified: an offscreen Qt smoke test
after the rename still resolves `PROJECT_ROOT` to `mpe_bluesky` and the plan
dropdown still populates correctly).

## 2026-07-20 — Real `instrument/plans/` scope + Plan visibility setting

Plan scope switched from the scratch `gui/test_plans/` to the real
`instrument/plans/`, with a new **Plan visibility** setting (Configuration
dialog) controlling which of its ~30 files even show up as rows in the main
panel's file browser — defaults to just
`instrument/plans/scans_stationary_gui_testing.py` (a full docstring-
reformatted copy of `scans_stationary.py`, all 6 `__all__` plans covered).
`IMPORT_ROOT` repointed from `BUNDLE_DIR` to `PROJECT_ROOT` — necessary
because plans now live inside the `instrument` package rather than the GUI
bundle itself; the generated command now reads
`from instrument.plans.scans_stationary_gui_testing import <plan>`.

- **New `visible_plan_files` config key** (explicit whitelist of
  `plans_dir`-relative paths, default = just the new gui-testing file) plus a
  **Plan visibility** card in the Configuration dialog (checkbox per file
  found under the plans directory, Select-all/Deselect-all/Refresh-list).
  This controls which rows even *appear* in the main panel's file browser —
  distinct from that panel's existing per-row checkbox, which still controls
  whether a *visible* file's plans merge into the plan dropdown. Chose an
  explicit whitelist over an "empty = show all" sentinel: with
  Select-all/Deselect-all one click away, there's no need for special-cased
  empty-list semantics, and behavior stays predictable as files get added to
  `instrument/plans/` later.
- **Default visibility = only the new gui-testing file** (user's explicit
  choice) — preserves today's narrow dev scope; other plan files opt in via
  Settings as they get docstring-reformatted for the grammar.

## 2026-07-18 — First redwood (hardware host) session: viewer→databroker, run-as `s20iduser`, deploy layout, log-clobber guard

Moved onto the beamline workstation **redwood** — which is now also the QS +
redis host (`_run_qs.sh` sets `QS_SERVER_HOST=redwood.xray.aps.anl.gov`, was
kurtag). Standing safety rule reaffirmed and observed: read-only investigation
of the live infra; **no** hardware/EPICS/QS/kernel actions; source edits only.

Investigated `/home/beams/S20IDUSER/bluesky` and decided/changed:

- **Deploy layout = `bluesky/gui/gui_qt`** (copy the whole `gui/` bundle into
  `bluesky/`). Why: keeps the bundle files in `BUNDLE_DIR=bluesky/gui`, matches
  `paths.py`'s `<root>/gui/gui_qt` fallback, and adds **no** top-level `*.py` to
  the QS startup root. Simulated the layout (symlink farm) → `PROJECT_ROOT=
  bluesky`, `ICONFIG`/`BLUESKY_STARTER`/bundle paths all resolve. (Note: the
  deploy layout target moved to `bluesky/B-PILOT/gui_qt` after the 2026-07-20
  `gui/` → `B-PILOT/` rename above — see `.context/DEPLOY.md`.)
- **Runs as `s20iduser`.** `collection.py` gates devices/plans on
  `getpass.getuser()` (only s1iduser/s1idtest/s20hedm/s20iduser) and
  `session_logs.py` opens `~/bluesky/user/user_defaults/dm_experiment.txt` at
  import — so any other account (e.g. dev `dbeniwal`) breaks the collection
  import. GUI + kernel must run as s20iduser. Runtime state stays under
  `~/.bluesky_gui/<beamline>/` (per-user, writable) — later renamed
  `~/.bluesky_pilot/` (see 2026-07-20).
- **Viewer → databroker, not Tiled.** MPE catalogs are databroker/MongoDB
  (`hexm` for s20iduser, defined in `~/.local/share/intake/MPE_mongodb.yml`;
  account→catalog mapping in `iconfig.yml`). Rewrote `viewer.py`: connect via
  `databroker.catalog[name]`, name auto-selected from iconfig by account
  (default `hexm`); kept an **optional** Tiled URI override. Removed the 3-ID-C
  `http://sn.xray.aps.anl.gov:8000` default + `TILED_PROFILE_NAME/TILED_PATH_NAME`.
  Connection is still user-initiated (no auto-connect).
- **Safety — never clobber the live experiment.** `embedded_kernel_starter.sh`
  wrote `dm_experiment.txt`/`setup_file.txt` unconditionally; an empty/stale GUI
  config would overwrite the beamline's live experiment (which drives
  `~/new_data/<dm_exp>/.logs`). Now writes only when the passed value is
  non-empty, else preserves. Cleared `gui_config.json` (was
  `dm_experiment=test/setup_file=temp.yml/script_run_mode=lab`) to `{}` so
  defaults apply (`beamline=20ide`, embedded, screen).
- **Removed remaining 3-ID-C bits:** `plan_runner.py` fallback module
  `id3c.user.db_bps` → `instrument.collection` (re-exports every plan in the
  loaded session, so the import resolves for any real plan); reworded 3-ID-C
  docstrings/comments in `plan_runner.py` / `plan_parser.py`.
- Kernel launch (Q1) + attach/detach (Q2): **no code change** — correct as-is
  when run as s20iduser (embedded starter mirrors `blueskyStarter.sh`; env
  `bluesky_2024_2` at `~/.conda/envs`; IPython `bluesky` profile present).
- **Write access:** the dev tree is owned by s20iduser but this session runs as
  dbeniwal → user `chmod -R o+rwX`'d the dev copy so edits could be made in
  place (reversible with `o-w`).

Verified (no hardware): `py_compile` all modules, `bash -n` on the starter,
JSON valid, and the path-resolution simulation of the target layout.

## 2026-07-18 — GUI relocated to `<mpe_bluesky>/gui/`; config persists overrides-only

The GUI is now kept inside the project (not in `scratch/`), in a dedicated
subfolder so it stays self-contained and away from the project root (where QS
executes top-level `*.py` — e.g. `qstarter.py`).

- **Moved** the GUI *bundle* to `<mpe_bluesky>/gui/`: `gui_qt/`, `test_plans/`,
  `device_manifest.yml`, `embedded_kernel_starter.sh`, `gui_config.json`, plus
  `PORTING_NOTES.md`. The bundle files must sit in the *parent of `gui_qt`*
  (that's `paths.BUNDLE_DIR`), so they moved together. `scratch/` and the
  obsolete `scratch/tmp_session/` were removed. No code changes were needed for
  the move itself — `paths.py` finds `PROJECT_ROOT` by marker walk-up and derives
  bundle files from `BUNDLE_DIR`, so it "just worked" at the new depth.
- **Chose a subfolder (`gui/`) over the project root** so the move adds no
  top-level `*.py` and keeps root clean next to `instrument/`, `user/`,
  `blueskyStarter.sh` (user picked this).
- **Config now persists overrides-only** (`config.save()` writes only keys whose
  value differs from the computed default). Why: the old `gui_config.json` had
  stored **absolute** `scratch/...` paths for `plans_dir`/`import_root`/
  `embedded_starter_script`; after the move those would have overridden the
  correct new defaults *and* broken cross-machine portability. With
  overrides-only, derived paths (which equal their default on a clean install)
  are never written, so the saved config is free of machine-specific paths. The
  file was regenerated to the only real overrides: `dm_experiment=test`,
  `setup_file=temp.yml`, `script_run_mode=lab`.
- Updated run instructions/comments (`app.py`, `__init__.py`, `__main__.py`,
  `config_dialog`, `plan_parser`, `paths`, `embedded_kernel_starter.sh`,
  `environments/mpe_bluesky_dev.yml`, STATE, memory): `cd scratch` → `cd gui`.
- Verified in `mpe_bluesky_dev` (offscreen): `BUNDLE_DIR=<root>/gui`,
  `PROJECT_ROOT=<root>`, `ICONFIG` exists, all 17 modules import, overrides-only
  save produces a 3-key file.

## 2026-07-17 — GUI: central `paths.py`, all paths anchored to GUI location

To make the GUI portable across machines (it'll be dropped into a `mpe_bluesky`
folder at an unknown location on the beamline), centralized every filesystem
path into one module derived from the GUI's own `__file__`.

- **New `scratch/gui_qt/paths.py`** — single source of truth. Two anchors:
  (1) **GUI bundle**: `GUI_DIR` (gui_qt) + `BUNDLE_DIR` (its parent, = scratch)
  for files that ship next to the GUI (`gui_config.json`, `device_manifest.yml`,
  `test_plans/`, `embedded_kernel_starter.sh`, `session_recorder.py`); these move
  *with* the GUI. (2) **`PROJECT_ROOT`**: found by **walking up** for markers
  (`instrument/` dir + `blueskyStarter.sh`/`qserver.sh`), so it's correct even if
  the GUI is relocated to a different depth; falls back to two levels up. Project
  paths (`INSTRUMENT_DIR`, `ICONFIG`, `BLUESKY_STARTER`, `PROJECT_USER_DIR`) hang
  off it.
- **Routed 7 modules through it** (removed their duplicated `dirname(...)`
  chains): `config`, `plan_parser`, `device_source`, `viewer`, `main_window`,
  `console_panel`, `kernel_session`.
- **Fixed a portability bug:** `viewer.py` pointed at the 3-ID-C
  `id3c/configs/iconfig.yml`; now `paths.ICONFIG` = `<root>/instrument/iconfig.yml`
  (the real MPE location).
- **Fixed a functional gap:** the embedded kernel's default cwd was
  `scratch/tmp_session`, which isn't on the path to `instrument/`. Now defaults to
  `PROJECT_ROOT` (`paths.KERNEL_CWD_DEFAULT`) so `from instrument.collection
  import *` resolves no matter where the GUI was started. Set in
  `main_window._DEFAULT_LAUNCH_DIR` and defended in `kernel_session.launch`.
- **Runtime state kept home-based** (`~/.bluesky_gui/<beamline>`: connection
  files, queue, transcripts) — per-user + writable on shared workstations, not in
  the repo; still overridable via config `session_dir`.
- Verified in `mpe_bluesky_dev` (offscreen): all 17 modules import; every path
  resolves and exists; a synthetic relocation (`deep/nested/gui_qt`) resolves the
  root via markers; py_compile clean.
- **Known leftover (not touched):** `plan_runner.py` still has a 3-ID-C fallback
  module string `"id3c.user.db_bps"` when a plan's origin is unknown — flagged for
  a later MPE-correct default (behavior, not a path).

## 2026-07-17 — Dedicated dev conda env `mpe_bluesky_dev` (exact pins)

Created a project-specific, exactly-pinned conda env for GUI/dev work and made
it the standing environment for this project on this device.

- **`environments/mpe_bluesky_dev.yml`** — every dependency pinned with `==`
  (no `>`/`>=`), so the env is reproducible. Scope decided as **lean dev**
  (not full beamline parity): GUI stack (PyQt5 5.15.11, qtconsole 5.7.2, QtPy,
  ipykernel/jupyter-client/pyzmq, matplotlib) + Bluesky stack (bluesky 1.15.1,
  ophyd 1.11.2, ophyd-registry, apstools, databroker 1.2.5, tiled,
  queueserver(+api), area-detector-handlers) + scientific/util deps. python
  3.11.15.
- **Version source:** pinned to what actually resolves and runs *this same GUI*
  on this Mac — read from the already-working `3idc-bits` env (`pip list`) —
  rather than the beamline `environment_2024_1.yml` (which uses `>=`/`=5` and
  the apsu channel). This guarantees `conda env create` succeeds instead of
  hitting phantom pins.
- **Deliberate exclusions** (documented at the top of the yml): `hkl`/`hklpy`
  (linux-64/apsu only) and `dm`/`aps-dm-api` (APS-subnet only). They're
  imported by `instrument.*`, so full `instrument.collection` import can't work
  on any macOS box regardless. The GUI's own dev features (AST plan parser,
  console, viewer, queue) don't import `instrument` in-process, so they're
  unaffected.
- Built via all-pip under a conda `python`/`pip` base to faithfully reproduce
  the proven 3idc-bits resolution (bluesky/Qt were pip there, not conda).
  Verified: `conda env create` exit 0; 19 key packages import at exactly their
  pinned versions.
- **Standing rule:** develop this project on this device in `mpe_bluesky_dev`
  (recorded in STATE.md and machine-local memory). Beamline runtime still uses
  the Linux `environment_2024_*.yml`.

## 2026-07-17 — GUI: embedded kernel now goes through a starter script (full activation)

The embedded kernel should do everything `blueskyStarter.sh` does (activate the
conda env, record the DM experiment/setup files, load `instrument.collection`) —
just ending in a *connectable* kernel instead of a terminal REPL. Added a new
script and wired the embedded launch to it.

- **`scratch/embedded_kernel_starter.sh`** — copy of blueskyStarter.sh's env
  `pick`/activation + experiment-file writing, but the final step is
  `screen -dmS <name> bash -c "python -m ipykernel_launcher -f <cf>
  --profile=bluesky --ipython-dir=~/.ipython"`. Args:
  `<dm_experiment> <setup_file> <connection_file> <screen_session>`. Using
  `--profile=bluesky` means the profile startup (`__start_bluesky_instrument__.py`)
  runs on kernel start → `from instrument.collection import *` (same activation as
  the console path). Writing the experiment files is guarded (skips if
  `~/bluesky/user/user_defaults` is absent, e.g. on a dev box).
- **`kernel_session.launch`** now: if `config.embedded_starter_script` is set +
  exists → start the kernel via that script (passing `dm_experiment`/`setup_file`
  from config, plus `cf` and the screen name); else fall back to a bare
  ipykernel. Single-instance / sidecar / pid / stop / interrupt unchanged.
- **UI:** `dm_experiment` + `setup_file` fields now show in **both** launch modes
  (both launchers record them); "Run as" shows only in script mode.
  `_launch_embedded` persists those fields to config before starting.
- **Config/Preferences:** new `embedded_starter_script` (default
  `scratch/embedded_kernel_starter.sh`; blank = bare kernel, no activation).
- Verified end-to-end: `launch` via the starter returns `hosted_in="screen
  (starter)"`, a client attaches and executes a cell. On dev it degrades
  gracefully (no bluesky env/profile → base kernel); on the beamline it performs
  the full activation. Note: if `--profile=bluesky` doesn't auto-run the startup
  in some ipykernel versions, the "Load Bluesky" button remains the fallback.
- Script kept in `scratch/` for now (per request).

## 2026-07-17 — GUI: two selectable launch modes (embedded kernel vs launch script)

User wants to trial both launch paths on the beamline and pick one later, with an
easy in-GUI switch. Added a **Launch mode** selector in the toolbar (persisted,
`config.launch_mode`):

- **Embedded kernel** (default) — the existing GUI-managed ipykernel path
  (Attach / transcript / queue / run-controls all work).
- **Launch script** — runs an external launcher (default `blueskyStarter.sh`,
  path set in Python → Configuration `launch_script`) via
  `bash <script> <dm_experiment> <setup_file> <run_mode>`, detached. This starts
  a terminal IPython in a `screen` session (`bluesky_<dm_exp>`), so the embedded
  console/Attach/Load-Bluesky don't apply — those controls are disabled in this
  mode and the status line tells the user to `screen -r bluesky_<dm_exp>`.

- **Script args are declared in the GUI**: a second toolbar row (shown only in
  script mode) has **Experiment** (`dm_experiment`), **Setup file**
  (`setup_file`, default `exp_setup.yml`), and **Run as** (`screen`/`console`/
  `lab`) — persisted to config and passed to the script. These are the args
  `blueskyStarter.sh` writes to `user/user_defaults/{dm_experiment,setup_file}.txt`.
- Toolbar: **"Work dir" → "Bluesky dir"**, width reduced (no stretch).
- Why both: user will A/B them on hardware and remove the loser; the toggle keeps
  both live meanwhile. New config keys: `launch_mode`, `launch_script`,
  `dm_experiment`, `setup_file`, `script_run_mode`. Verified offscreen: mode
  toggle shows/hides the args row and gates embedded-only controls; the script is
  invoked with the correct args and they persist.
- Known limitation of script mode: the GUI's embedded console/queue/recorder
  can't attach (blueskyStarter.sh starts a REPL, not a connectable kernel) — see
  the launch-mechanism question/answer that led here.

## 2026-07-17 — GUI: run controls (Stop run soft/hard + RE recovery + Shutdown)

Added a control bar below the console (`gui_qt/run_controls.py` →
`RunControlBar`) mapping Bluesky's interrupt/recovery model to buttons:

- **Stop run**: *click* = deferred pause (one Ctrl+C → stop at next checkpoint);
  *press-and-hold >1 s* = immediate pause (double Ctrl+C). Implemented with
  QPushButton pressed/released + a 1 s hold timer.
- Delivered as **SIGINT(s) to the kernel** via `kernel_session.interrupt`
  (one / two SIGINTs) — works for our client-only connection because we signal
  the kernel PID directly. **Gotcha:** in screen mode `pgrep -f <cf>` matches the
  SCREEN wrapper and login shell as well as the kernel; signaling the wrapper
  does nothing. Fixed `_kernel_pid` to pick the **python** process. Verified:
  `os.kill(kernel_pid, SIGINT)` interrupts a running cell in ~0.1 s.
- After a pause, four **temporary recovery buttons** appear —
  **Resume / Stop / Abort / Halt** → `RE.resume()` / `RE.stop()` / `RE.abort()`
  / `RE.halt()` (the RunEngine's four paused-state options; stop=success,
  abort=aborted+cleanup, halt=no cleanup) — sent to the console; they hide once
  one is chosen.
- **Shut down kernel** button (same handler as the Console menu); its
  confirmation now states a killed kernel **cannot be resumed later** (vs closing
  the GUI, which leaves it reattachable).
- Known nuance: if a *queued* plan is paused this way and then resumed manually,
  the queue item may still read `error` (the runner saw the interrupted reply) —
  console RE control and queue status can diverge; left as-is for now.

## 2026-07-17 — GUI: show queue-run cells in the console (`include_other_output`)

Queue plans are dispatched by the detached queue-runner — a *separate* kernel
client — so the qtconsole widget didn't echo their `In [N]:`/command (qtconsole
only echoes its own client's input). Set `RichJupyterWidget.include_other_output
= True` (and `other_output_prefix = ""`) in `_wire_widget` so activity from any
client on the kernel renders like a typed cell. The console now mirrors
everything the kernel does (queue runs, other attached GUIs), consistent with
the Session-log transcript.

## 2026-07-16 — GUI: full session transcript (Session log) + how to close the kernel

Follow-up to persistent-kernel: the qtconsole scrollback lives in the *widget*,
so closing the GUI lost all history, a reattached widget started empty, and a
busy kernel showed nothing until idle. Added a durable transcript.

- **Detached recorder** (`gui_qt/session_recorder.py`, launched like the kernel
  with `start_new_session=True`): a `BlockingKernelClient` subscribes to the
  kernel's **IOPub** and appends a plain-text transcript (cell input, stdout/
  stderr, results, errors; ANSI-stripped) to `<connection-file-stem>.log`. IOPub
  is independent of the shell channel, so it records **while the GUI is closed**
  and **while the kernel is busy**. Exits when the kernel dies.
- **Session log view** (`gui_qt/session_log.py`): read-only, loads the whole
  transcript and live-tails it (500 ms poll, follow toggle). Shown as a
  **Console | Session log** tab beside the interactive console; on *attach* the
  GUI switches to it so a busy/mid-plan kernel shows activity immediately
  instead of the blank interactive prompt.
- `ConsolePanel.start()` launches the recorder; `attach()` reuses the existing
  transcript (starts one only if none exists). `log_file` derived from the
  connection file, so any GUI reattaching finds the same transcript.
- **This solves the "wait for the kernel to be free to see anything" problem:**
  live output is visible in the Session log even while the shell is blocked; only
  the *interactive prompt* still waits for idle (qtconsole limitation).
- **Closing the kernel (answer to the recurring question):** closing the GUI now
  only *detaches*. To actually terminate it: **Console → Shut down kernel**, or
  uncheck **Keep the IPython kernel running when the GUI closes** in Python →
  Configuration (then GUI close kills it).

## 2026-07-17 — GUI: persistent, table-based plan queue driven by a detached runner

Reworked the plan queue to be a **persistent, one-per-beamline** table whose
status updates even while the GUI is detached — the key insight being that the
scheduler must live *with the kernel*, not in the GUI.

- **`gui_qt/queue_store.py`** — the queue is a per-beamline JSON file
  (`<session_dir>/<beamline>/queue.json`) with a schema `{state, seq, items[]}`
  (item: id/name/command/notes/status). All writes are **locked read-modify-
  write** (`fcntl.flock`) + atomic (`os.replace`) so the GUI and the runner never
  race. One file per beamline ⇒ exactly one queue per session.
- **`gui_qt/queue_runner.py`** — a **detached** companion process (started like
  the recorder, singleton via its own flock). While `state == running` it
  dispatches the next `waiting` item to the kernel (non-silent, so it shows in
  console + transcript), waits for the execute_reply, and writes back
  `done`/`error`; errors pause the queue (a Ctrl-C/`RunEngineInterrupted`
  surfaces as an errored reply). Because it's detached, the queue progresses and
  status updates **independently of the GUI**. On start it reconciles a leftover
  `running` item → `error`.
- **`gui_qt/queue_panel.py`** — rewritten from a QListWidget scheduler to a
  **QTableWidget view/editor** that **polls** the store (no in-GUI scheduling):
  columns **# / Name / Status (coloured) / Command (truncated, full text + notes
  on hover)**. **Name defaults to the plan name** (parsed from `RE(<plan>(...))`
  in `queue_store._default_name`, not the whole command) and is **editable**.
  Colours per request: DONE red, RUNNING green, WAITING orange (+ ERROR purple to
  stand out). Start/Pause just flip `state`.
- **`console_panel`** launches the runner on start/attach (idempotent — extra
  runners self-exit).
- Verified: runner drives waiting→running→done/error while a simulated detached
  client only polls; the table renders colours/tooltips/editable names; a fresh
  MainWindow **restores** a pre-seeded queue from disk.
- Note: this is the *interactive* queue feeding the embedded kernel. The MPE
  production queue is still the **queueserver** — same shape (persistent,
  client-independent), which is why this mirrors it.

## 2026-07-16 — GUI: single-instance kernel hosted in a screen session

Enforced "one interactive Bluesky kernel per beamline, detachable + attachable"
— mirroring how `qserver.sh` keeps the queueserver in a screen session.

- **`gui_qt/kernel_session.py`** (Qt-free; also a CLI):
  - **Fixed per-beamline paths** `~/.bluesky_gui/<beamline>/kernel.json` (+ `.log`,
    `session.json`). The connection file is the singleton key *and* the attach
    handle — no UUIDs to track.
  - **Liveness = heartbeat**, via a raw ZMQ REQ ping to `hb_port` (no session key,
    works while the kernel is BUSY, leak-free — a `BlockingKernelClient`-based
    check leaked fds → "Too many open files"; the raw ping fully closes its
    socket/context).
  - **`launch()`** refuses if a kernel already answers (`already_running`); else
    cleans stale screen/files and starts `python -m ipykernel_launcher -f <cf>`
    inside `screen -dmS bluesky-kernel-<beamline>` (falls back to a detached
    Popen if screen is absent). `stop()` = shutdown request + `screen -X quit` +
    file cleanup. `status()` for introspection.
  - **CLI**: `python -m gui_qt.kernel_session status|stop|launch [--beamline B]` —
    a qserver.sh-style handle for staff.
- **`console_panel`** delegates lifecycle to kernel_session: `start()` → refuse
  (emit `launch_blocked`) or connect; `attach()` defaults to the fixed
  connection file; `shutdown()` calls `ks.stop()` (quits the screen session too).
- **`main_window`**: on `launch_blocked`, a dialog shows the running session's
  details and offers **Attach instead** (with the CLI stop command). Config dialog
  gains **Beamline id** + **Host in screen** (config keys `beamline`,
  `use_screen`, `session_dir`).
- **Why screen (not tmux):** user requirement + it matches the existing
  `qserver.sh` idiom staff already use (`screen -r bluesky-kernel-<beamline>` to
  attach a terminal). Note: screen doesn't refuse duplicate session names, so the
  real single-instance guarantee is the **heartbeat on the fixed connection
  file**, not the screen name.
- Verified end-to-end: launch→screen-hosted; second launch blocked; detach→
  reattach keeps state; shutdown quits screen + cleans files; no stray sessions;
  CLI status/stop work.

### Fix (2026-07-17): "Too many open files" traceback on Shut down kernel

Shutting down printed a `zmq.error.ZMQError: Too many open files` from a
`jupyter_client` **heartbeat-channel thread** (Thread-N) — the kernel still died,
but the HB thread raised on its own so no try/except could catch it. Cause: fd
exhaustion in a long GUI process + `shutdown_kernel` starting the heartbeat
channel it never needs. Fixes:
- `shutdown_kernel()` now starts **only shell+control** channels
  (`start_channels(..., hb=False, iopub=False, stdin=False)`) — those are
  blocking (no thread), so the HB thread that threw is never created and any
  error is caught synchronously. This alone removes the traceback.
- `stop()` made socket-free-capable: graceful request → `screen -X quit` → **PID
  kill** fallback (`launch()` now records the kernel PID via `pgrep -f
  ipykernel_launcher…<cf>`), so termination never depends on opening sockets.
- `app._raise_fd_limit()` bumps `RLIMIT_NOFILE` soft→min(hard, 8192) at startup
  (macOS default 256) to stop the accumulation biting other ops. Only raises,
  never lowers.
- Verified: under a forced 256-fd limit, stop() emits no stray-thread traceback
  and 30 launch/shutdown cycles run with zero fd errors.

## 2026-07-16 — GUI: persistent IPython kernel + reattach

Made the console's out-of-process kernel **survive the GUI** and added a way to
**reattach** to it (so closing/crashing the GUI doesn't lose a running plan).

- **Root problem:** `QtKernelManager.start_kernel()` ties the kernel to the GUI
  via ipykernel's parent poller — verified the kernel logs "Parent appears to
  have exited, shutting down" and dies when the launcher exits. So the old code
  (which also killed the kernel on clean close) gave no persistence.
- **Fix:** `ConsolePanel.start()` now launches the kernel as a **detached
  process** — `subprocess.Popen([python, -m, ipykernel_launcher, -f, <cf>],
  start_new_session=True)`, its own session/process-group, no parent handle — to
  a connection file we pick in the Jupyter runtime dir, then connects a
  `QtKernelClient` to it. Verified across a real process boundary: kernel
  survives, a second client reattaches and sees prior state (`bluesky_marker`).
- **Reattach:** `ConsolePanel.attach(cf)` connects to an existing connection
  file (defaults to the last one, saved in config `last_kernel_connection_file`).
  Toolbar **Attach** button + **Console → Attach to running kernel…** (falls back
  to a file picker in the runtime dir). Readiness handshake tolerates a *busy*
  kernel — the `kernel_info_reply` just arrives when the running plan finishes.
- **Close behavior:** `close_session()` **detaches** (keeps kernel) by default,
  gated by config `keep_kernel_on_exit` (checkbox in the Config dialog); wired to
  window close + `app.aboutToQuit`. Explicit **Console → Shut down kernel** ends
  it. Restart = shutdown + relaunch (no manager-owned kernel to restart).
- **Consequence:** the panel is always a *client* (never manages the process),
  so `is_running()` no longer checks `has_kernel`; shutdown goes via
  `kc.shutdown()` with a `Popen.terminate()` fallback for kernels we spawned.

### Fix (same day): reattach to a BUSY kernel looked blank

First user test of reattach showed a **blank panel** (kernel had been left mid
`time.sleep`). Root cause: qtconsole paints its banner/prompt only after a
**shell round-trip** (`kernel_info` + a silent `execute('')` for the prompt
number), and a busy kernel's shell channel is blocked — so nothing paints until
it goes idle. The attach had actually *succeeded* (heartbeat alive). Fixes:
- On attach, write an explanatory notice into the widget
  (`ConsolePanel._show_attach_notice`) so it's never blank; the real prompt
  replaces it once the kernel is idle (confirmed: `In [2]:` then appears).
- Distinguish busy-vs-dead with the **heartbeat**: `ConsolePanel.is_alive()`
  (`kernel_client.is_alive()`) is True even while busy. `main_window._verify_attach`
  (a 4.5 s post-attach check) says "kernel is busy" if alive, or "not responding
  — may have shut down" if not. `ready` signal → "Reattached and ready."
- A cleanly shut-down kernel removes its connection file, so `attach` hits the
  "connection file not found" path; a crashed kernel leaves the file but fails
  the heartbeat check. Both surface a clear message instead of a blank panel.
- Note: wiring the widget *before* `start_channels()` is NOT viable —
  QtKernelClient channels need the ioloop that `start_channels()` creates, so
  the widget must bind after. That's why the prompt can't be forced pre-idle.

## 2026-07-16 — GUI: user-facing Configuration window

Added a persistent Configuration dialog (menu **Python → Configuration…**,
`Ctrl+,`) so the two things that were hardcoded/beamline-specific become
user-editable without code edits:

- **Files (search scope):** `plans_dir` (folder scanned), `import_root` (root the
  generated `from <module> import <plan>` line resolves against), and
  `default_plan_file` (checked on startup). Replaces the hardcoded
  `plan_parser.USER_DIR/SRC_DIR/DEFAULT_PLAN_FILE` at runtime.
- **Launch:** `bluesky_startup` — the command(s) run when *Load Bluesky* is
  clicked. Default now `from instrument.collection import *` (was the leftover
  3-ID-C `from id3c.startup import *`).

Design: `gui_qt/config.py` (JSON at `scratch/gui_config.json`, defaults sourced
from `plan_parser` so there's one source of truth for built-in paths);
`gui_qt/config_dialog.py` (the dialog). Panels read config **live** —
`plan_parser` stays pure (config-free; `file_to_module(filepath, src_dir)` takes
the root as an arg), `plan_runner` reads `config` and re-scans via
`apply_config()` on Save, `console_panel.load_bluesky()` reads the command at
click time. `gui_config.json` is user state (only written on Save), not
committed by default. Verified offscreen end-to-end.

## 2026-07-16 — GUI: device-typed plan args (device / device_list)

Designed a **consistent, replicable** way to handle plan parameters that are
device *objects* (not scalars), so it extends to any device/plan/beamline:

- **Docstring grammar** gains two dtypes: `device{<category>}` (one device) and
  `device_list{<category>}` (a list). `<category>` is optional; blank = any
  device. Parsed by `gui_qt/plan_parser.py` (reuses the `choice{…}` brace
  syntax); `ParamSpec` gained a `category` field.
- **`RawCode`** (a `str` subclass in `plan_parser.py`) marks values the command
  builder must emit **unquoted**. Device fields produce `RawCode`, so the line
  reads `expose(det=pg6, scalers=[tc32E])` (real objects) — not `det='pg6'`.
- **Device names come from a static manifest** `scratch/device_manifest.yml`
  (beamline → category → names), read by `gui_qt/device_source.py`, which
  **never imports ophyd / touches hardware**. `DeviceCatalog` is a swappable
  interface: the manifest can later be auto-generated (AST-scan of each device
  module's `__all__`) or replaced by a live QS `devices_allowed` dump / an
  `oregistry` dump without changing the GUI or the grammar. (Later replaced
  entirely by static `device_discovery.py` scanning — see the 2026-07-21
  entry above.)
- **Form widgets:** `device` → dropdown (blank entry when optional, meaning
  "omit → plan default"); `device_list` → multi-select `QListWidget`.

Why a manifest (not live introspection) now: the GUI must run/parse without
hardware or a running queueserver (hard safety rule), and a declarative
per-beamline manifest is the one place to edit and the natural seam for the
future live sources. Verified headlessly + offscreen-Qt end-to-end against
`expose` (`det : device{area_detector}`, `scalers : device_list{scaler}`).

## 2026-07-16 — GUI: port 3-ID-C plan-runner as the starting point

Decided to build the MPE plan-runner GUI + data viewer by porting the working
PyQt app from `3idc-bits/scratch/gui_qt/`. Copied it **verbatim** into
`mpe_bluesky/scratch/gui_qt/` (pristine baseline) rather than half-adapting, so
the diffs to MPE are explicit and we adapt together. Full difference analysis
in `.context/PORTING_NOTES.md`. Key MPE-specific adaptations identified:

- **Startup is account-gated** (`instrument/collection.py` branches on
  `getpass.getuser()`): 1-ID vs 20-ID-D vs 20-ID-E load different device/plan
  namespaces. No single `from ....startup import *` line.
- **Runs go through the bluesky queueserver (QS)** on `kurtag` (ZMQ
  :60615/:60625), native client `queue-monitor`. Leaning toward a QS-native
  queue (`bluesky-queueserver-api`) instead of the 3-ID-C embedded-IPython
  scheduler.
- **Plans are plain generators + `__all__`**, documented (when at all) with an
  uppercase `PARAMETERS` section (no `[units]`/`::`), and some args are device
  objects — so `plan_parser.py` needs a rewrite (still AST-only; importing
  pulls in ophyd devices + `oregistry`).
- **Storage is databroker/MongoDB catalogs by name** (`1id_hexm`, `ht_hedm`,
  `hexm`), not a Tiled URI — so `viewer.py` opens `databroker.catalog[name]`.

Why verbatim-copy: the user explicitly named the 3-ID-C GUI as the starting
point and wants to build the MPE version collaboratively; a clean baseline +
a written diff plan beats a broken partial port.
</content>
