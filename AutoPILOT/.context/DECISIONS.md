# DECISIONS — append-only, newest first

Each entry: what was decided and *why* (the reasoning that would be expensive
to reconstruct later). Never rewrite history; add a new entry to supersede.

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
- **Deferred, not built this session** (see also `mpe_bluesky/.context/
  ARCHITECTURE.md`'s AutoPILOT section for the underlying B-PILOT-side
  survey): an MCP server (the `mcp` SDK is installed nowhere on this
  machine — needs a new dependency and its own testable pass) and
  queue/dispatch tools (needs `build_command()` extracted from the
  Qt-coupled `plan_runner.py`, confirmed still absent, plus a
  `queue_store.py` schema change for human-gated dispatch — this touches
  the literal `kc.execute()` hardware-dispatch boundary and deserves its own
  carefully-reviewed initiative, not bundling into a "make the chat
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

## 2026-07-23 — Slice 2: chat dock embedded in B-PILOT via a guarded import

Built the chat window inside B-PILOT itself, per the user's explicit ask that
AutoPILOT "tie in" with the existing GUI rather than stay a separate CLI.

- **Guarded import, not a hard dependency.** New `gui_qt/autopilot_bridge.py`
  in B-PILOT does `sys.path.insert(..., BUNDLE_DIR/AutoPILOT)` then
  `try: from autopilot.gui.chat_panel import ChatDockWidget; AVAILABLE = True
  except ImportError: AVAILABLE = False` — the exact shape of the only
  existing precedent for optional-dependency code in this codebase, the
  `fcntl` guard in `gui_qt/queue_store.py`/`queue_runner.py`. This is the
  concrete implementation of the 2026-07-22 refinement to the standalone
  rule (see below): B-PILOT's `main_window.py` gets one small `if
  autopilot_bridge.AVAILABLE:` block that adds the dock; every other line of
  B-PILOT is unchanged. **Verified, not just asserted**: two offscreen Qt
  smoke tests — one with AutoPILOT present (full round-trip: typed request →
  real draft plan file), one with the `autopilot` import forced to fail
  (`builtins.__import__` patched to raise for that name) confirming
  `MainWindow` still constructs with no dock and no exception. This is the
  strongest evidence available short of literally deleting the directory —
  literal deletion wasn't done because AutoPILOT's own git repo and this
  session's work live there.
- **Shared pipeline, not duplicated orchestration.** The classify → build
  system prompt → call Argo → validate → render → write sequence used to
  live only in `scripts/try_plan_builder.py`. Extracted to
  `autopilot/pipeline.py:generate_plan()` (returns a `PlanResult` dataclass)
  so the CLI and the new chat dock share one implementation — a
  classification bug or a rendering fix now only needs fixing once. Verified
  the refactor was behavior-preserving by rerunning the CLI's existing
  manual test prompts before/after and confirming identical output shape.
- **`autopilot/gui/` isolates all PyQt5-dependent code.** Everything else in
  `autopilot/` (`pipeline`, `llm_client`, `plan_context`, `plan_spec`,
  `plan_renderer`, `device_catalog`) stays Qt-free and importable from
  AutoPILOT's own CLI-only `.venv`; only `autopilot/gui/chat_panel.py`
  imports PyQt5, and it only ever runs inside B-PILOT's own process (borrows
  B-PILOT's PyQt5 install via the guarded-import path, never gets its own).
  This means `anthropic` had to be separately installed into
  `mpe_bluesky_dev` (the conda env B-PILOT itself runs under) — a genuinely
  new dependency there, added to `B-PILOT/environments/mpe_bluesky_dev.yml`.
- **`_ChatWorker` mirrors `viewer.py`'s `_CatalogWorker` exactly** (persistent
  daemon thread consuming a `queue.Queue`, `pyqtSignal` back to the GUI
  thread) rather than spinning up a fresh thread per request — reusing the
  one proven background-worker shape in this codebase rather than inventing
  a second pattern to reason about.
- **No settings UI added.** A Configuration-dialog "AI" tab for Argo
  base_url/key/model was considered and deliberately deferred — the existing
  env-var-based `llm_client` config already works and there's no concrete
  need yet for per-install overrides via the GUI. Revisit if that need
  actually appears.
- **Chat transcript HTML-escapes both sides** (`html.escape()` before
  `QTextEdit.append()`) — `QTextEdit` renders `.append()` input as rich text,
  so an unescaped scan request containing `<`/`>` (plausible: "<10mm") would
  otherwise corrupt the transcript's rendering.

## 2026-07-23 — Slice 1: spec-first plan generation, Argo client design, token minimization

Built the first real feature: NL request -> validated structured spec ->
deterministic file render -> draft `.py` plan in `generated_plans/`. Key
decisions, each chosen specifically to minimize token spend (the user's
explicit ask) without sacrificing correctness/safety:

- **The LLM never writes Python.** It fills a small tool-forced JSON spec
  (`plan_spec.build_tool_schema`) matching a template's `ParamSpec`s (reused
  directly from `gui_qt.plan_parser.ParamSpec` via `autopilot/_bpilot_path.py`
  putting B-PILOT on `sys.path` — no reimplementation). A plain Python
  renderer (`plan_renderer.py`) turns the validated spec into the actual file
  text. This makes LLM output short (cheap) and generated files
  syntactically guaranteed-valid — verified by round-tripping two generated
  files through B-PILOT's real `plan_parser.find_plan_specs()` and confirming
  `documented: True` with exactly the expected params.
- **Templates wrap the real skeleton plans, not clones of them.**
  `mpe_step_scan` (`instrument/plans/scan_skeletons.py`) and `mpe_count`
  (`instrument/plans/scans_stationary.py`) are ~100-300 lines each with
  internal helpers (suspenders, prescan checks, ladder logic) that would be
  both wasteful and risky to regenerate. A template instead describes just
  the handful of fields a human would vary; the rendered file is a small
  wrapper function whose body is one static `yield from
  <real_plan>(...)` call (`plan_renderer._CALL_BODY`), with the LLM-chosen
  values becoming the *wrapper's own signature defaults* — so the generated
  plan is itself immediately GUI-editable (a human can still tweak values via
  B-PILOT's plan-runner form) without opening the file.
- **Static context, not re-derived per call.** The docstring grammar
  (`plan_context.GRAMMAR`) is a hand-written ~30-line constant from this
  session's codebase survey — never re-read from `plan_parser.py`'s comments
  or from `instrument/plans/*.py` at runtime. Only the 1-2 templates relevant
  to the classified scan type go into the prompt, and the device list sent is
  the active profile's already-small, already-structured
  `device_discovery.scan()` output (names + category only), never device
  source files or the project's markdown docs.
- **Prompt caching on the system block.** `llm_client.call_with_tool` sets
  `cache_control: {"type": "ephemeral"}` on the system prompt (grammar +
  template + device catalog) since that's the largest, most session-static
  chunk of input tokens — the highest-leverage lever for a chat-style
  back-and-forth where the same context is reused turn after turn.
- **Cheap model by default**: `claude-haiku-4-5-20251001` (`llm_client.
  DEFAULT_MODEL`, override via `ARGO_MODEL` env var) — confirmed acceptable
  by the user for this templated, structured-output task.
- **Cheap classification, no LLM call.** `plan_context.classify()` is a
  keyword heuristic (`"count" in text and "scan" not in text -> count, else
  step_scan`) — fine for a 2-template registry; upgrade to a tiny
  forced-choice tool call only once the registry grows enough that keywords
  stop being reliable.
- **Argo client** (`autopilot/llm_client.py`): `base_url` defaults to
  `https://apps-dev.inside.anl.gov/argoapi` (ANL's internal
  Anthropic-Messages-API-compatible gateway — confirmed by reading this very
  Claude Code session's own `~/.claude/settings.json`/env:
  `ANTHROPIC_BASE_URL` + `CLAUDE_CODE_SKIP_ANTHROPIC_AUTH=1` +
  `apiKeyHelper: "echo <username>"`). `api_key` resolution deliberately never
  hardcodes a username — `ARGO_API_KEY` env, else `ANTHROPIC_API_KEY` env,
  else `getpass.getuser()` — so any beamline staff member's own OS login
  works unmodified, the same generalization Claude Code's own
  `apiKeyHelper` convention relies on. **Verified live**: the smoke test
  (`try_plan_builder.py --smoke-test`) got a real response, and
  `messages.create(tools=..., tool_choice={"type":"tool",...})` returned a
  proper `tool_use` block — so Argo supports forced tool-calling and no
  JSON-mode fallback was needed for this slice.
- **Generated files land in `AutoPILOT/generated_plans/` (gitignored), never
  directly in `instrument/plans/`** — confirmed with the user beforehand.
  Each file's header comment says so explicitly; promotion into the real
  plans directory is a manual, human step.
- **Environment**: this slice needed only `anthropic` (see
  `requirements.txt`), installed into AutoPILOT's own `.venv` — NOT into
  `mpe_bluesky_dev`, since no Qt code runs yet. The upcoming GUI slice will
  need `anthropic` inside `mpe_bluesky_dev` instead (same process as B-PILOT),
  and can likely drop this `.venv` at that point.
- **Standalone/synergy contract refined**: the original rule ("B-PILOT's own
  code must never import from ... AutoPILOT/") is refined to allow a
  *guarded* import (try/except, mirroring the existing `fcntl` idiom in
  `queue_store.py`/`queue_runner.py`) once the GUI slice adds a chat dock —
  the invariant that matters is B-PILOT keeps working with `AutoPILOT/`
  absent, not a literal zero-reference rule. See `.context/ARCHITECTURE.md`
  and `mpe_bluesky/.context/ARCHITECTURE.md` (updated to match).

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

## 2026-07-22 — Architecture: MCP server over B-PILOT's Qt-free backend, human-gated dispatch

Scoped during a planning conversation (see B-PILOT's own history/notes for
the full exploration) that surveyed B-PILOT's codebase for agent-callable
surfaces. Key findings and resulting decisions:

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

## 2026-07-22 — Adopt two-layer .context system

Adopted the STATE (disposable-but-current) + DECISIONS (permanent) split so
returning to this project after a long gap is cheap: only STATE.md auto-loads;
detail is read on demand. `.context/` is committed to git so it travels
between workstations.
