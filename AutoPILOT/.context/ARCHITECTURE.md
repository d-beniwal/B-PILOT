# ARCHITECTURE

## The standalone/synergy contract (read this first)

**B-PILOT must always be runnable standalone, with `AutoPILOT/` absent or
deleted entirely.** AutoPILOT is a strictly optional, additive layer:

- AutoPILOT code only ever *calls into* B-PILOT's existing Qt-free backend
  functions (below) — it never gets its own copy of business logic, and it
  never touches Qt.
- AutoPILOT reads/writes the **same on-disk state** B-PILOT already uses
  (`profiles/<name>/*.json`, `queue.json`) so both can observe the same
  world without a private sync channel or duplicated source of truth.
- **The dependency arrow points one way: AutoPILOT → B-PILOT, never the
  reverse — but as of the 2026-07-23 GUI-slice planning, B-PILOT MAY
  optionally import AutoPILOT, guarded.** Refined from the original "never
  import" wording once a chat dock tied into B-PILOT's own window was
  requested: the invariant that actually matters is *B-PILOT keeps working
  with `AutoPILOT/` absent or deleted*, not a literal zero-reference rule.
  The guard is a plain `try/except ImportError` around the one import site
  (mirror the existing `fcntl` idiom in `gui_qt/queue_store.py`/
  `gui_qt/queue_runner.py` — the only precedent for an optional/graceful-
  degradation import in this codebase) — if it fails, the chat dock/menu
  item is simply omitted, nothing else in B-PILOT is affected. This is still
  enforced structurally where it can be: separate git repos, `AutoPILOT/`
  gitignored from B-PILOT, so B-PILOT's *repo history* has no knowledge
  AutoPILOT exists even though its *running code* may, optionally, notice it.
- AutoPILOT must never gain a path to raw code execution. It never calls
  `kernel_session`'s `execute()` primitive (unrestricted Python with full
  EPICS/hardware access) directly or indirectly with LLM-authored text —
  only pre-validated `RE(plan(...))` command strings built from
  `plan_parser` schemas may reach the kernel or the queue.

## Integration surface (from a 2026-07-22 codebase survey of B-PILOT)

These B-PILOT modules are already Qt-free and safe to wrap as agent tools:

- `gui_qt/config.py` — profiles as plain dicts: `list_profiles()`,
  `active_profile()`/`set_active_profile()`, `profile_values()`,
  `update()`/`save()`.
- `gui_qt/device_discovery.py:scan(paths) -> list[DiscoveredDevice]` —
  `ast`-based device listing, never imports/connects to hardware.
- `gui_qt/device_source.py:get_catalog()` — config-driven device catalog
  with per-name visibility filtering.
- `gui_qt/plan_parser.py:find_plan_specs(filepath)` — parses plan
  docstrings into structured, serializable param schemas
  (name/dtype/units/choices/required) — the natural source for per-plan
  tool-call JSON schemas.
- `gui_qt/queue_store.py` — `add()`, `remove()`, `move()`, `rename()`,
  `set_state()`, `load()` — pure JSON, `flock`-guarded, fully callable
  outside Qt.
- `gui_qt/viewer.py` — `list_catalogs()`, `connect_catalog()`,
  `list_runs(cat, offset, limit)` — databroker search/paging, plain
  tuples/dicts.

**Confirmed absent (2026-07-23 survey, not just assumed):**
`build_command()` does not exist anywhere in `gui_qt/` yet. The equivalent
logic is still spread across Qt-widget-coupled `PlanRunnerPanel` methods in
`gui_qt/plan_runner.py`: `_parse_params()` (widget values -> typed kwargs +
validation errors), `_make_import_line()`/`_make_re_line()` (kwargs ->
the `RE(plan(...))` string, including `RawCode`-wrapped device refs), and
`_compose_lines()` (glues them together, still reads `self._plan_cb`/
`self._notes` etc.). Extracting a Qt-free `build_command(plan_name, values,
params, *, module, notes="") -> tuple[str, str]` out of these is the one
prerequisite change needed before AutoPILOT can build runnable commands
from structured (agent-produced) kwargs — see `mpe_bluesky/.context/
ARCHITECTURE.md`'s AutoPILOT section for the full survey (queue schema gap,
exact `kc.execute()` dispatch boundary, `viewer.py`'s databroker-search
building blocks).

See `mpe_bluesky/.context/ARCHITECTURE.md` for where B-PILOT itself is
documented (it doesn't yet have its own `.context/`).

## The plan-builder pipeline (current shape, as of Slice 4 / 2026-07-23)

`plan_context.classify()` (a 2-keyword heuristic) existed in Slice 1 only
because the model was forced into exactly one tool call; it was **removed**
once the design moved to a real multi-turn loop with `tool_choice: "auto"`
(see `.context/DECISIONS.md` Slice 4 for the full reasoning) — every
template's proposal tool, plus lookup/decline/ask tools, are offered
together every turn instead of pre-picking one.

```
NL request (+ prior conversation history, if any)
   |
   v
[pipeline.converse()]  loop, up to max_turns=4:
   |
   |  every turn offers: propose_<template>_plan (one per plan_context.TEMPLATES
   |  entry) + list_devices/list_plans (autopilot/tools.py) + ask_user/
   |  cannot_generate_plan (plan_spec.py) -- tool_choice: {"type": "auto",
   |  "disable_parallel_tool_use": True}
   |
   |  [llm_client.call()] -> Argo, one turn
   |     |
   |     +-- no tool_use (plain text)        -> PlanResult(ok=False, message=text), STOP
   |     +-- list_devices / list_plans        -> run locally, append tool_result, CONTINUE
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

### Package layout

- `autopilot/_bpilot_path.py` — the one sys.path bootstrap for the AutoPILOT
  → B-PILOT import direction (`import gui_qt.<module>`). Confirmed safe: none
  of `gui_qt/{config,device_discovery,device_source,plan_parser,paths}.py`
  import PyQt5 at module level, so they're importable from a plain venv with
  no Qt installed.
- `autopilot/llm_client.py` — `ArgoClient`: base_url/api_key/model
  resolution (see DECISIONS), `smoke_test()`, `call(system, messages, tools,
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
- `autopilot/tools.py` (NEW, Slice 4) — read-only lookup tools:
  `list_devices(catalog, category)`, `list_plans()`, plus their schema
  builders and `known_categories()`. Pure wrappers over data already loaded
  by `device_catalog`/`plan_context` — no new `gui_qt` imports.
- `autopilot/plan_renderer.py` — `render(template, clean, catalog, summary)
  -> (filename, file_text)`. `_CALL_BODY` holds the one static call-body
  string per template — add an entry here (and a `Template` in
  `plan_context.py`) when adding a new scan type.
- `autopilot/settings.py` (NEW, Slice 3) — Qt-free JSON persistence
  (`load()`/`save()`) for chat-dock preferences (model, temperature, font,
  colors, debug toggle, Argo overrides) at `AutoPILOT/autopilot_settings.json`
  (gitignored).
- `scripts/try_plan_builder.py` — CLI entry point;
  `--smoke-test` for bare connectivity, `--profile <name>` to pick a beamline
  profile explicitly (defaults to whatever profile is currently active —
  **be careful**: `gui_qt.config.profile_values(name)` silently creates an
  empty profile directory on first access if `name` doesn't exist yet; a
  typo'd `--profile` value leaves a stray `B-PILOT/profiles/<typo>/` behind.
  Real profile names as of this writing: `s20ide`, `s1id`, `s20idd` — note
  the `s`-prefix, not `20ide`).

## Slice 2 (2026-07-23): chat dock embedded in B-PILOT

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
    == the multi-turn agent loop described above (Slice 4) -- history is
       memory, threaded through by the caller, not the pipeline
```

`autopilot/pipeline.py` is the one orchestration implementation; both
`scripts/try_plan_builder.py` (CLI, via the stateless `generate_plan()`
wrapper) and `autopilot/gui/chat_panel.py` (dock, via `converse()` with
memory) call into it — no duplicated logic between them.

### Package layout addition

- `autopilot/pipeline.py` — `PlanResult` dataclass (`ok`, `message`,
  `template_key`, `raw_spec`, `clean_spec`, `errors`, `filepath`, `model`,
  `tool_name`, `tool_calls`), `converse(request, history=None, profile=None,
  client=None, temperature=None, max_turns=4) -> (PlanResult, history)`,
  `generate_plan(request, ...) -> PlanResult` (thin single-shot wrapper).
- `autopilot/gui/__init__.py`, `autopilot/gui/chat_panel.py`,
  `autopilot/gui/settings_dialog.py` (Slice 3, NEW) — the only PyQt5-
  dependent code in `autopilot/`; runs only inside B-PILOT's process
  (borrows its PyQt5 + now also its `anthropic` install in `mpe_bluesky_dev`,
  added to `B-PILOT/environments/mpe_bluesky_dev.yml`). Never imported by
  anything in AutoPILOT's own CLI-only `.venv`.
- `B-PILOT/gui_qt/autopilot_bridge.py` — the one guarded-import module in
  B-PILOT itself. `AVAILABLE: bool`, `ChatDockWidget` (or `None`).

### Verified, not just designed

Two offscreen Qt smoke tests (`QT_QPA_PLATFORM=offscreen`, run inside
`mpe_bluesky_dev`) proved both halves of the contract in practice:
1. **Presence**: real `MainWindow` construction with the `s20ide` profile
   active → chat dock exists → a typed request ("step scan tomoE from 0 to
   180 deg...") produced a real, correct draft file within a bounded wait on
   `_ChatWorker.result_ready`.
2. **Absence**: `builtins.__import__` patched to raise for `autopilot`/
   `autopilot.*` (simulating it being uninstalled/deleted without touching
   the real filesystem) → `autopilot_bridge.AVAILABLE` is `False` →
   `MainWindow` still constructs with no dock and no exception.

**Since superseded (see `.context/DECISIONS.md` Slices 3-4):** a Settings
dialog for model/temperature/appearance/Argo overrides now exists
(`autopilot/gui/settings_dialog.py`), and the single-forced-tool-call
pipeline was replaced by the multi-turn `converse()` loop described above.
Still not done: `gui_qt/plan_runner.py`'s `build_command()` extraction
(confirmed absent by a 2026-07-23 survey — still the prerequisite for a
future queue-enqueue tool) and the MCP server originally scoped on
2026-07-22 (the `mcp` SDK isn't installed anywhere on this machine yet).
