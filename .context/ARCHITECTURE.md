# ARCHITECTURE — B-PILOT (+ AutoPILOT)

_(On-demand: read when a task needs repo shape or conventions. Not
auto-loaded — see `.context/STATE.md`, which is.)_

> **2026-08-01 merge note:** this file merges what used to be two separate
> write-ups — B-PILOT's own architecture notes (formerly living in
> `mpe_bluesky/.context/ARCHITECTURE.md`, since B-PILOT had no `.context/` of
> its own) and AutoPILOT's (formerly `B-PILOT/AutoPILOT/.context/
> ARCHITECTURE.md`, since AutoPILOT was a separate nested git repo until
> 2026-07-31) — into one file, now that both live in the same project. Old
> cross-references between those two files now just mean "see the other
> section below."

## What B-PILOT is

**B-PILOT** ("Bluesky-PILOT") is a PyQt5 plan-runner GUI + Bluesky data
viewer for the MPE beamlines (Sectors 1/20), nested inside the `mpe_bluesky`
tree as its own git repo (`github.com/d-beniwal/B-PILOT`). It ports and
adapts a GUI originally built for `3idc-bits`. For the underlying
`instrument`/queueserver codebase it drives (layout, startup paths, safety
conventions, console launch flow), see `mpe_bluesky/.context/ARCHITECTURE.md`
— not duplicated here.

**Safety-critical, absolute:** never connect to beamline hardware or EPICS
PVs; never run code that talks to instruments. Read/edit source only.

## Layout

```
gui_qt/                    # the GUI package itself
profiles/<name>.json       # per-beamline settings bundles (s20ide, s1id, s20idd)
environments/               # B-PILOT's own copy of the dev conda env spec
embedded_kernel_starter.sh  # detached-kernel launch script
launch.py                   # entry point
gui_config.json             # gitignored, per-install active-profile pointer
AutoPILOT/                  # optional agentic AI subsystem (this same repo, see below)
```

## GUI feature set (all done; see `.context/DECISIONS.md` for the session-by-session log)

Docstring-driven parameter forms (incl. `device`/`device_list` picker
dtypes, backed by static device discovery) · per-beamline **profiles**
(`profiles/<name>.json`) with a tabbed Configuration dialog (Paths / Plans /
Launch Session / Devices / Scan blocks / Data Viewer / Appearance + a
profile bar to create/load/save/delete) · persistent detachable kernel with
full session transcript · single-instance kernel per beamline (hosted in
`screen`) · persistent run queue with its own runner + status panel · run
controls (pause/resume/abort/halt) · two launch modes (embedded kernel vs
external launch script) · databroker-backed viewer with catalog discovery
and paginated run list · single UI-scale multiplier · scan-building-block
discovery (`plan_opener`/`per_step`/`plan_closer`/suspenders, see
`.context/DOMAIN.md`) · optional AutoPILOT chat dock (see below).

## Two startup paths / two RE builds

See `mpe_bluesky/.context/ARCHITECTURE.md` for the full console-vs-queueserver
detail. B-PILOT's embedded-kernel launch mirrors the console path exactly
(below) rather than the queueserver path.

## B-PILOT "Launch IPython" (embedded mode) does the SAME full activation

`gui_qt/main_window.py:_launch_embedded` → `kernel_session.launch()`
(`kernel_session.py:218`) → runs `B-PILOT/embedded_kernel_starter.sh` in a
detached `screen` session:

```
embedded_kernel_starter.sh <dm_experiment> <setup_file> <connection_file> <screen_session>
   │  1. write dm_experiment.txt / setup_file.txt (skipped if arg is empty —
   │     never clobbers a live experiment)
   │  2. activate conda env (same pick()/pick_environment_executable() as
   │     blueskyStarter.sh)
   └─ 3. screen -dmS <name> python -m ipykernel_launcher -f <cf> --profile=bluesky
            └─ same IPython-profile auto-run as the console path:
               `__start_bluesky_instrument__.py` → `from instrument.collection
               import *` → full account-gated device/plan activation, checks in
               `instrument/framework/check_python.py` + `check_bluesky.py`.
```

Single-instance guard: `kernel_session.launch()` checks `is_alive(cf)` first
and refuses (GUI offers **Attach** instead) rather than starting a second
kernel for the same beamline.

**So "Load Bluesky" is a fallback, not a required second step.** Its default
command (`config.py`: `bluesky_startup` = `"from instrument.collection import
*"`) is *identical* to what the profile auto-run already executed at kernel
launch. It only does real work when the auto-run path didn't happen:

- the ipykernel/IPython version in use doesn't auto-run profile startup files
  (open on-hardware question, see STATE.md);
- you **Attach** to a kernel that was never started via
  `embedded_kernel_starter.sh` (e.g. a bare `ipykernel_launcher` with no
  `--profile=bluesky`), so `instrument.collection` was never imported;
- someone reconfigures `bluesky_startup` to run something custom/partial.

It's gated in the UI to embedded-mode-only, enabled only while a console is
running (`main_window.py:215-217`), and guarded by its own "this connects to
EPICS / hardware" confirmation dialog (`main_window.py:528` `_load_bluesky`).

## B-PILOT path resolution (why it's portable across workstations)

`gui_qt/paths.py` is the single source of truth for every filesystem path
the GUI needs. Two anchors, both derived from `__file__` at import time —
never hard-coded absolute paths:

- **`GUI_DIR`/`BUNDLE_DIR`** — `GUI_DIR` = the `gui_qt/` package's own
  directory; `BUNDLE_DIR` = its parent (`B-PILOT/`). Files shipped *next to*
  the GUI (`gui_config.json`, `device_manifest.yml`, `embedded_kernel_starter.sh`)
  are defined relative to `BUNDLE_DIR`, so they travel with the GUI if the
  folder is moved/copied.
- **`PROJECT_ROOT`** — found by walking *up* from `GUI_DIR` looking for a
  directory containing both an `instrument/` subfolder and one of
  `blueskyStarter.sh`/`qserver.sh` (`_find_project_root`, `paths.py:56-74`).
  Whatever directory satisfies that marker check becomes root — portable
  because it only depends on the *relative* layout
  (`<root>/instrument/`, `<root>/blueskyStarter.sh`, `<root>/B-PILOT/gui_qt/`),
  not on any absolute path. Falls back to two levels above the GUI bundle if
  no marker is found. From `PROJECT_ROOT` it derives `PLANS_DIR`
  (`instrument/plans`), `IMPORT_ROOT` (= `PROJECT_ROOT`), and
  `BLUESKY_STARTER` (`blueskyStarter.sh`).

`config.py`'s `DEFAULTS` dict (Plans directory, Import root, Launch script,
Embedded starter, etc. in the Configuration panel) is seeded from these
computed values, then overlaid with any user overrides persisted in the
active **profile** (see below). So on a fresh checkout on a new machine the
panel auto-detects correctly; a previously hand-edited override is
machine-specific and won't auto-adjust if you relocate to a different layout.

## Beamline profiles + device discovery (portability across beamlines)

Where the section above makes B-PILOT portable across *workstations*, this
makes it portable across *beamlines*. See `.context/DECISIONS.md`
(2026-07-21) for the full reasoning; summary:

- **Profiles** (`gui_qt/config.py`) — every beamline-specific setting (plan
  scope, launch/session commands, device search paths, data-viewer
  connection, appearance) lives in `B-PILOT/profiles/<name>.json`. Shipped:
  `20ide.json`, `s1id.json`, `s20idd.json` (device dirs `instrument/devices/
  {s20ide,s1id,s20idd}_devices/` already exist for all three). A saved
  profile is **self-documenting** — every key is written out in full — except
  `config._WORKSTATION_KEYS` (`plans_dir`, `import_root`, `launch_script`,
  `embedded_starter_script`, `session_dir`, `last_kernel_connection_file`),
  which stay diff-only against `config.DEFAULTS` because they're derived from
  *this* GUI's own location (`paths.py`) — baking one workstation's absolute
  path into a profile you commit to git would break the next one.
  `gui_config.json` is just a pointer, `{"active_profile": "<name>"}` (still
  gitignored — per-install). The Configuration dialog
  (`gui_qt/config_dialog.py`) has a profile bar (New…/Save As…/Delete + a
  combo) above seven tabs — **Paths, Plans, Launch Session, Devices, Scan
  blocks, Data Viewer, Appearance**.
- **Device discovery** (`gui_qt/device_discovery.py`) replaces the old
  `device_manifest.yml`. Reads a profile's `device_search_paths`, statically
  `ast.parse`s each `.py` file's `__all__` list (never imports — no ophyd, no
  EPICS), and infers a category from the **source filename**
  (`CATEGORY_FILENAME_SUFFIXES`: `_motors`→`motor`, `_scalers`→`scaler`,
  `_area_detectors`→`area_detector`, `_slits`→`slit`, `_shutters`→`shutter`,
  `_multidet`→`multi_detector`), matching the one-file-per-device-type
  convention already used under `instrument/devices/`. The Devices tab's
  **Discover** button re-scans and shows a checkbox per found name, grouped
  by category (default checked/shown; state preserved across re-Discover) —
  this is how devices get hidden from users, and how newly-implemented
  devices get surfaced. The on-disk shape is nested by category —
  `device_selection: {category: {name: bool}}` — matching the dialog's
  grouped display. Multi-category assignment is a `_CategoryDropdowns`
  composite widget (editable combo per assigned category + "+"/"×"), not
  free text (see `.context/DECISIONS.md` 2026-07-24 for why free text was
  tried and reverted). `device_source.get_catalog()` filters by the active
  profile's `device_selection` and keys off `config.get("beamline")`.
  Onboarding a new beamline: create a profile, point its device search paths
  at `instrument/devices/<bl>_devices/`, click Discover — no code changes,
  provided that beamline follows the same device-directory naming
  convention (if it doesn't, extend `CATEGORY_FILENAME_SUFFIXES`; anything
  unmatched still shows up under an `other` category rather than being
  dropped).
- **Scan-building-block discovery** (`gui_qt/scan_building_discovery.py`,
  added 2026-07-24) — same static/never-imports philosophy, catalogs
  `plan_opener`/`per_step`/`plan_closer`/`suspender`/`pseudo_suspender`
  names out of the canonical common files by parsing `__all__`'s section
  comments via `tokenize` (`ast` alone discards comments — the comment
  grouping is the only signal distinguishing e.g. true suspenders from
  pseudo-suspenders). See `.context/DOMAIN.md` for the full building-block
  domain knowledge. Persisted (not live-rescanned like devices) into
  profile keys `plan_building_search_paths`/`suspender_search_paths`/
  `plan_building_blocks`. Deliberately discovery-and-storage only — does
  **not** wire into `plan_runner.py`'s actual skeleton form yet (see
  `.context/DECISIONS.md` 2026-07-24 scope decision).
- **Data Viewer settings** — `databroker_catalog` / `databroker_uri` /
  `databroker_nexus_dir` (Configuration → Data Viewer tab) seed
  `gui_qt/viewer.py`'s `load_defaults()`, taking priority over its original
  account-based auto-detect from `instrument/iconfig.yml` when set.
  `databroker_catalog` is always a catalog **name** resolved via
  `databroker.catalog[name]` against connection files pre-registered at
  `~/.local/share/intake/*.yml` — never a credentialed
  `mongodb://user:pass@...` string like `iconfig.yml`'s `CATALOG_URL`, since
  profiles are git-tracked and meant to be shared.

## AutoPILOT: optional agentic AI layer

`B-PILOT/AutoPILOT/` is an optional, additive subsystem providing
natural-language plan-building, read-only lookups, and (in chat form)
docstring-drafting assistance on top of B-PILOT. Until 2026-07-31 it was a
separate nested git repo (`AutoPILOT/.gitignore`'d from B-PILOT's repo, own
`CLAUDE.md`/`.context/`); it has since been **folded into B-PILOT's own repo
as a tracked subdirectory** (see `.context/DECISIONS.md` 2026-07-31) — this
file now covers it directly rather than pointing to a separate project.

### The standalone/synergy contract (read this first)

**B-PILOT must always be runnable standalone, with `AutoPILOT/` absent or
deleted entirely.** AutoPILOT is a strictly optional, additive layer:

- AutoPILOT code only ever *calls into* B-PILOT's existing Qt-free backend
  functions (below) — it never gets its own copy of business logic, and it
  never touches Qt itself (except its own dock/settings widgets, which run
  inside B-PILOT's process — see Slice 2 below).
- AutoPILOT reads/writes the **same on-disk state** B-PILOT already uses
  (`profiles/<name>.json`, `queue.json`) so both can observe the same world
  without a private sync channel or duplicated source of truth.
- **The dependency arrow points one way: AutoPILOT → B-PILOT, never the
  reverse.** B-PILOT *may* optionally import AutoPILOT (since the
  2026-07-23 chat-dock work), but only behind a guarded `try/except
  ImportError` around the one import site (`gui_qt/autopilot_bridge.py`,
  mirroring the existing `fcntl` idiom in `gui_qt/queue_store.py`/
  `gui_qt/queue_runner.py`) — if it fails (or `AutoPILOT/` is literally
  deleted), the chat dock is simply omitted, nothing else in B-PILOT is
  affected. This is the invariant that actually matters, not a literal
  zero-reference rule; folding AutoPILOT into the same repo (2026-07-31)
  removed the git-level separation but the code-level guard is what was
  always doing the real enforcement.
- AutoPILOT must never gain a path to raw code execution. It never calls
  `kernel_session`'s `execute()` primitive (unrestricted Python with full
  EPICS/hardware access) directly or indirectly with LLM-authored text —
  only pre-validated `RE(plan(...))` command strings built from
  `plan_parser` schemas may reach the kernel or the queue.

### Integration surface (B-PILOT backend survey, 2026-07-22/23, still current)

These B-PILOT modules are already Qt-free and safe to wrap as agent tools:

- `gui_qt/config.py` (`list_profiles()`, `active_profile()`,
  `profile_values(name)`, `as_dict()`, `get(key)` — all plain dict/list/str).
- `gui_qt/device_discovery.py:scan(paths) -> list[DiscoveredDevice]` —
  `ast`-based device listing, never imports/connects to hardware.
- `gui_qt/device_source.py:get_catalog()` — config-filtered device catalog
  with per-name visibility filtering (prefer this over raw
  `device_discovery.scan()` for a "list devices" tool, since it already
  applies the human-curated `device_selection` filter).
- `gui_qt/plan_parser.py:find_plan_specs(filepath)` — AST-only, never
  imports the plan module — parses docstrings into structured, serializable
  param schemas (name/dtype/units/choices/required) — the natural source for
  per-plan tool-call JSON schemas.
- `gui_qt/queue_store.py` — `load()`, `add()`, `set_item_status()`,
  `remove()`, `move()`, `rename()`, `set_state()` — pure JSON,
  `flock`-guarded, fully callable outside Qt.
- `gui_qt/viewer.py` — `list_catalogs()`, `connect_catalog(name) ->
  (catalog_obj, status)`, `list_runs(cat, offset, limit) -> (rows, total,
  uids)` — databroker search/paging, plain tuples/dicts, but `catalog_obj`
  itself is a live, non-JSON-serializable databroker object (must be cached
  server-side, only derived data handed to an LLM) and there's no
  filter-by-metadata logic today (`list_runs` only paginates newest-first).

**Confirmed absent (2026-07-23 survey, not just assumed): `build_command()`
does not exist anywhere in `gui_qt/`.** The kwargs→`RE(plan(...))` string
logic is still spread across Qt-widget-coupled `PlanRunnerPanel` methods in
`gui_qt/plan_runner.py`: `_parse_params()` (widget values → typed kwargs +
validation errors), `_make_import_line()`/`_make_re_line()` (kwargs → the
command string, including `RawCode`-wrapped device refs), `_compose_lines()`
(glues them together). Extracting a standalone, Qt-free
`build_command(plan_name, values, params, *, module, notes="") ->
tuple[str, str]` is the one prerequisite before any agent can build runnable
commands from structured kwargs instead of writing draft files.

**The dispatch-to-kernel boundary is exactly two call sites in the whole
codebase**: `gui_qt/queue_runner.py`'s dispatch loop (`kc.execute(nxt
["command"], ...)`, only picks up items whose status is `WAITING`) and
`gui_qt/console_panel.py`'s interactive "Run" button. Nothing else ever
reaches a live kernel's `execute()`.

**`queue_store.py`'s schema has no pending-approval state** — item status is
only `waiting|running|done|error`, queue state is only
`idle|running|paused`. A human-approval gate for agent-originated queue
items needs an additive schema change (e.g. an `origin: "agent"|"human"`
field per item + a `queue_store.approve()` function) and a filter change in
`queue_runner.py`'s dispatch loop (`next(... if it["status"] == WAITING and
it.get("origin") != "agent")`) — `queue_panel.py`'s status/state
dictionaries already tolerate unknown values via `.get(..., default)`, so
this wouldn't crash the GUI on its own, just render with no distinct color
until that dict is updated too.

### The plan-builder pipeline (current shape, as of Slice 7 / 2026-08-01)

`plan_context.classify()` (a 2-keyword heuristic) existed only in Slice 1,
when the model was forced into exactly one tool call; it was **removed**
once the design moved to a real multi-turn loop with `tool_choice: "auto"`
(Slice 4) — every template's proposal tool, plus lookup/decline/ask tools,
are offered together every turn instead of pre-picking one.

```
NL request (+ prior conversation history, if any)
   |
   v
[pipeline.converse()]  loop, up to max_turns=6:
   |
   |  every turn offers: propose_<template>_plan (one per plan_context.TEMPLATES
   |  entry) + list_devices/list_plans/list_all_plans/describe_plan/
   |  list_scan_building_blocks/read_plan_file/validate_docstring
   |  (autopilot/tools.py) + ask_user/cannot_generate_plan (plan_spec.py)
   |  -- tool_choice: {"type": "auto", "disable_parallel_tool_use": True}
   |
   |  [llm_client.call()] -> Argo, one turn
   |     |
   |     +-- no tool_use (plain text)        -> PlanResult(ok=False, message=text), STOP
   |     +-- a lookup tool                    -> run locally, append tool_result, CONTINUE
   |     +-- ask_user / cannot_generate_plan  -> PlanResult(ok=False, message=question|reason), STOP
   |     +-- propose_<template>_plan          -> STOP, fall through below
   |
   v  (only on a propose_* terminal call)
[plan_spec.validate()]           re-checks the model's output against the
   |                             SAME ParamSpecs (dtype/choices/required,
   |                             device names against the real catalog) --
   |                             never trust tool-schema conformance alone
   v
[plan_renderer.render()]         deterministic string templating (no LLM) ->
   |                             a small wrapper function that calls straight
   |                             through to the real skeleton plan
   v
[pipeline._flag_device_substitutions()]  heuristic: if a chosen device name
   |                                     doesn't literally appear in the
   |                                     request text, append a transparency
   |                                     note (it was likely a substitution)
   v
AutoPILOT/generated_plans/<func_name>.py   (draft; gitignored; human promotes
                                             into instrument/plans/ manually)
```

`converse(request, history=None, ...) -> (PlanResult, history)` — `history`
*is* the conversation memory; callers persist and pass back whatever it
returns. `generate_plan(request, ...) -> PlanResult` is a thin wrapper
(`converse(request, history=None, ...)[0]`) kept for the CLI, which has no
concept of a running conversation. `PlanResult.tool_calls: list[str]`
records every tool called during a turn, in order (not just the terminal
one) — e.g. `["list_devices", "propose_step_scan_plan"]`.

Two of the lookup tools (`read_plan_file`, `validate_docstring`, added
Slice 7) are read-only, chat-only docstring-drafting aids and never feed the
`propose_*_plan` → draft-file path — `read_plan_file` is hard-scoped to
`instrument/plans/` only (never the project root, since
`instrument/iconfig.yml` sits one level up with live plaintext MongoDB
credentials).

### Package layout

- `autopilot/_bpilot_path.py` — the one sys.path bootstrap for the AutoPILOT
  → B-PILOT import direction (`import gui_qt.<module>`). Confirmed safe: none
  of `gui_qt/{config,device_discovery,device_source,plan_parser,paths}.py`
  import PyQt5 at module level, so they're importable from a plain venv with
  no Qt installed.
- `autopilot/llm_client.py` — `ArgoClient`: base_url/api_key/model
  resolution, `smoke_test()`, `call(system, messages, tools,
  temperature=None)` — one turn of a conversation, `tool_choice: auto`,
  retries once without `temperature` on the "deprecated for this model" 400.
- `autopilot/plan_context.py` — `GRAMMAR` constant, `Template` dataclass,
  `TEMPLATES` registry (`step_scan` -> `mpe_step_scan`, `count` ->
  `mpe_count`). No classifier — see above.
- `autopilot/device_catalog.py` — `load(profile=None) -> DeviceCatalog`;
  unlike `gui_qt.device_source.get_catalog()` (names only), this keeps each
  device's `source_file` so `plan_renderer` can build the right relative
  import (`from ..devices.<bl>_devices.<file> import <name>`).
- `autopilot/plan_spec.py` — `build_tool_schema()`, `validate()`,
  `ValidationError`, `DECLINE_TOOL_NAME`/`build_decline_tool_schema()`,
  `ASK_USER_TOOL_NAME`/`build_ask_user_tool_schema()`.
- `autopilot/plan_catalog.py` (NEW, Slice 6) — two-tier real plan catalog:
  "vetted" (a profile's `visible_plan_files`, fully documented) and
  "extended" (a hand-maintained allowlist of `_EXTENDED_SHARED_FILES` plus
  each beamline's own `<bl>_plans/*.py`, mostly undocumented); vetted always
  wins on name collision.
- `autopilot/tools.py` — read-only lookup tools: `list_devices`,
  `list_plans`, `list_all_plans`, `describe_plan`,
  `list_scan_building_blocks` (Slice 6), `read_plan_file`,
  `validate_docstring` (Slice 7), plus their schema builders and
  `known_categories()`. Pure wrappers over data already loaded by
  `device_catalog`/`plan_context`/`plan_catalog`/`gui_qt.plan_parser` — no
  new `gui_qt` imports beyond `plan_parser`.
- `autopilot/plan_renderer.py` — `render(template, clean, catalog, summary)
  -> (filename, file_text)`. `_CALL_BODY` holds the one static call-body
  string per template — add an entry here (and a `Template` in
  `plan_context.py`) when adding a new scan type.
- `autopilot/pipeline.py` — `PlanResult` dataclass (`ok`, `message`,
  `template_key`, `raw_spec`, `clean_spec`, `errors`, `filepath`, `model`,
  `tool_name`, `tool_calls`), `converse(request, history=None, profile=None,
  client=None, temperature=None, max_turns=6) -> (PlanResult, history)`,
  `generate_plan(request, ...) -> PlanResult` (thin single-shot wrapper).
- `autopilot/settings.py` — Qt-free JSON persistence (`load()`/`save()`) for
  chat-dock preferences (model, temperature, font, colors, theme, debug
  toggle, Argo overrides) at `AutoPILOT/autopilot_settings.json`
  (gitignored).
- `autopilot/gui/__init__.py`, `autopilot/gui/chat_panel.py`,
  `autopilot/gui/settings_dialog.py`, `autopilot/gui/themes.py` (Slice 5) —
  the only PyQt5-dependent code in `autopilot/`; runs only inside B-PILOT's
  process (borrows its PyQt5 + `anthropic` install in `mpe_bluesky_dev`).
- `B-PILOT/gui_qt/autopilot_bridge.py` — the one guarded-import module in
  B-PILOT itself. `AVAILABLE: bool`, `ChatDockWidget` (or `None`).
- `scripts/try_plan_builder.py` — CLI entry point;
  `--smoke-test` for bare connectivity, `--profile <name>` to pick a beamline
  profile explicitly (defaults to whatever profile is currently active —
  **be careful**: `gui_qt.config.profile_values(name)` silently creates an
  empty profile directory on first access if `name` doesn't exist yet; a
  typo'd `--profile` value leaves a stray `B-PILOT/profiles/<typo>/` behind.
  Real profile names as of this writing: `s20ide`, `s1id`, `s20idd` — note
  the `s`-prefix, not `20ide`).

### Chat dock embedding (Slice 2, 2026-07-23)

```
B-PILOT/gui_qt/main_window.py
    import autopilot_bridge
    if autopilot_bridge.AVAILABLE:
        self.autopilot_chat = autopilot_bridge.ChatDockWidget(self)
        self.addDockWidget(RightDockWidgetArea, self.autopilot_chat)
            |
            v
B-PILOT/gui_qt/autopilot_bridge.py   (guarded import -- the ONE integration point)
    sys.path.insert(0, BUNDLE_DIR/AutoPILOT)
    try: from autopilot.gui.chat_panel import ChatDockWidget; AVAILABLE = True
    except ImportError: AVAILABLE = False
            |
            v
AutoPILOT/autopilot/gui/chat_panel.py   (PyQt5 -- runs inside B-PILOT's process)
    ChatDockWidget: bubble-style QTextEdit transcript (fake "bubbles" via
        <table align=left|right> -- Qt rich text has no border-radius/flexbox)
        + auto-growing QTextEdit composer (Enter sends, Shift+Enter newline)
        + header row: model caption, "New Chat" (resets conversation memory),
        "⚙" settings button -> settings_dialog.AutoPilotSettingsDialog
    _ChatWorker(QtCore.QObject): daemon thread + queue.Queue, mirrors
        viewer.py's _CatalogWorker; holds one persistent ArgoClient
        (reconfigure() rebuilds it live from Settings -> Save) and
        self._history (conversation memory, reset_conversation() clears it);
        submit(request) -> result_ready(PlanResult)
            |
            v
AutoPILOT/autopilot/pipeline.py   (Qt-free -- shared with the CLI)
    converse(request, history=None, ...) -> (PlanResult, history)
    == the multi-turn agent loop described above -- history is memory,
       threaded through by the caller, not the pipeline
```

`autopilot/pipeline.py` is the one orchestration implementation; both
`scripts/try_plan_builder.py` (CLI, via the stateless `generate_plan()`
wrapper) and `autopilot/gui/chat_panel.py` (dock, via `converse()` with
memory) call into it — no duplicated logic between them.

### Verified, not just designed

Two offscreen Qt smoke tests (`QT_QPA_PLATFORM=offscreen`, run inside
`mpe_bluesky_dev`) proved both halves of the standalone/synergy contract in
practice:
1. **Presence**: real `MainWindow` construction with the `s20ide` profile
   active → chat dock exists → a typed request ("step scan tomoE from 0 to
   180 deg...") produced a real, correct draft file within a bounded wait on
   `_ChatWorker.result_ready`.
2. **Absence**: `builtins.__import__` patched to raise for `autopilot`/
   `autopilot.*` (simulating it being uninstalled/deleted without touching
   the real filesystem) → `autopilot_bridge.AVAILABLE` is `False` →
   `MainWindow` still constructs with no dock and no exception.

Slices 1-7 are all done (NL plan-builder core; chat dock embedded in
B-PILOT; settings dialog + chat aesthetics; multi-turn agent loop with
lookup/decline/ask tools; named theme presets; two-tier real plan catalog;
read-only docstring-drafting assistance), each verified live against real
Argo in addition to offline/offscreen tests — see `.context/DECISIONS.md`
for the full slice-by-slice detail.

**Still deferred, not built** (see `.context/STATE.md` "Next steps" for the
concrete prerequisites): an MCP server (the `mcp` SDK isn't installed
anywhere on this machine yet, and no MCP client is available to test
against) and queue/dispatch tools (needs `build_command()` extracted from
`plan_runner.py` and the `queue_store.py` schema change described above —
touches the literal `kc.execute()` hardware-dispatch boundary, treated as
its own carefully-reviewed initiative rather than bundled with chat-dock
work).
</content>
