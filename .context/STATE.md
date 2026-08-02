# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-01 (AutoPILOT drafting scope generalized to all 21 plans)_

## Dev environment (this device)

- **Always use the `mpe_bluesky_dev` conda env for development on this
  machine.** Activate: `conda activate mpe_bluesky_dev`. Run the GUI:
  `cd B-PILOT && python launch.py` (or `python -m gui_qt`).
- Spec: `environments/mpe_bluesky_dev.yml` (exact `==` pins), python 3.11.15.
  Excludes `hklpy`/`aps-dm-api` (Linux/APS-subnet only) — full
  `instrument.collection` import isn't possible on macOS, but GUI dev is
  unaffected.
- Git: own repo, pushed to `github.com/d-beniwal/B-PILOT`. `AutoPILOT/` is a
  tracked subdirectory of this same repo since 2026-07-31 (commit `1c62ebe`)
  — no longer a separate repo; its old standalone 3-commit history is
  preserved at `.context/autopilot_standalone_history.bundle`.

## GUI feature set (all DONE — see DECISIONS.md for the session-by-session log)

Docstring-driven parameter forms (incl. `device`/`device_list` picker dtypes
backed by static device discovery) · per-beamline **profiles**
(`profiles/<name>.json`) with a tabbed Configuration dialog (Paths / Plans /
Launch Session / Devices / Scan blocks / Data Viewer / Appearance) ·
persistent detachable kernel with full session transcript · single-instance
kernel per beamline (hosted in `screen`) · persistent run queue with runner +
status panel · run controls (pause/resume/abort/halt) · single embedded-kernel
launch path (the "Launch script" mode was removed 2026-08-01 — never worked
with B-PILOT) · databroker-backed viewer (launched from Python → Open Bluesky
Viewer) with catalog discovery + paginated run list · UI-scale multiplier ·
scan-building-block discovery (openers/per-steps/closers/suspenders, catalog
only, not yet wired into the skeleton form).

## AutoPILOT (optional agentic AI layer, folded into this repo 2026-07-31)

Chat dock (`gui_qt/autopilot_bridge.py`, guarded import — B-PILOT always runs
standalone without it) driving a multi-turn agent (`pipeline.converse()`,
`tool_choice: auto`, `max_turns=6`) that proposes plan calls via
`propose_<template>_plan` tools plus read-only lookup tools (`list_devices`,
`list_plans`, `list_all_plans`, `describe_plan`, `list_scan_building_blocks`,
`read_plan_file`, `validate_docstring`) and `ask_user`/`cannot_generate_plan`
decline tools, never raw code execution. Drafts land in gitignored
`AutoPILOT/generated_plans/` for a human to review and promote manually.

- **Slice 8 done (2026-08-01):** drafting scope generalized from 2 hardcoded
  templates to all 21 documented plans across `scan_skeletons.py` (6),
  `scans_standard.py` (9), `scans_stationary.py` (6) — `plan_context.TEMPLATES`
  is now built at import time from `gui_qt.plan_parser.find_plan_specs()` over
  those 3 files instead of hand-coded, so a new plan becomes draftable with no
  AutoPILOT code change. Added two dtype/shape capabilities `plan_spec.py`
  didn't have before: **`block`** (plan_opener/per_step/plan_closer/
  suspender/pseudo_suspender — enum-restricted to the profile's building
  blocks, forced required, never blank) and **motor-axis qualification**
  (`device`/`device_list` with `category=="motor"` resolve to `motor.axis`
  via a sibling `_axis`/`_axes` schema field and `catalog.axes_for()`, mirroring
  `gui_qt/skeleton_widgets.py`'s `MotorAxisPicker`'s 0/1/>1-axis logic — new
  in `device_catalog.py` too), plus an `axes` array schema for the 6
  scan_skeletons.py plans whose motor/position args are a bare `*args`
  (`plan_renderer.render_command` flattens `clean["__axes__"]` into
  positional tokens ahead of the keyword args). Also fixed a prerequisite bug:
  all 6 profile plan-file configs (`default_config.json`/`active_config.json`
  ×3 profiles) still pointed at plan files deleted by upstream commit
  `ce82efb` — fixed to the real 3 files. Verified in 3 layers: a no-network
  unit script (validate+render for one function per shape), live-Argo
  synthetic prompts via `try_plan_builder.py` spanning all 3 files + a
  `mpe_count` regression check + a scope-boundary decline, and an
  offscreen-Qt round-trip through the real `PlanRunnerPanel.load_from_command()`
  confirming exact field-level restoration (motor/axis picker, block
  dropdowns, plain fields). See `.context/DECISIONS.md` for the full trace.
- **Slice 7 (2026-08-01):** `read_plan_file` (hard-scoped to
  `instrument/plans/` only) + `validate_docstring` let AutoPILOT draft
  B-PILOT-compliant docstrings for a real plan file, chat-only, never edits
  the file. Verified live against real Argo on `nfdev_jul26.py`. See
  `.context/DECISIONS.md` for the full trace.
- Full architecture (standalone/synergy contract, integration surface,
  pipeline diagram, package layout): `.context/ARCHITECTURE.md`'s "AutoPILOT"
  section.

## Now working on / not yet verified on redwood

- **AutoPILOT Slice 8 (2026-08-01, see above)** — thoroughly tested via the
  CLI harness and an offscreen `PlanRunnerPanel`, but never click-tested
  through the actual chat-dock UI in a running GUI session. Worth one manual
  "Open in form" click-through before considering it fully done.
- **GUI layout cleanup round (2026-08-01)** — Browse button → icon button,
  "Launch script" mode removed entirely (embedded-only now), "Open Bluesky
  Viewer" + AutoPILOT moved from toolbar buttons into the Python menu
  (AutoPILOT menu toggle kept in sync with the Configuration checkbox),
  session-log transcript now cleared on kernel shutdown (file deleted +
  panel blanked) but left untouched by attach. Offscreen-Qt-verified only
  (window/dialog construction, widget presence) — needs a real desktop
  click-test, especially the AutoPILOT menu↔checkbox sync and the
  shutdown→relaunch log-clearing. See DECISIONS.md.
- **B-PILOT usability follow-up round (2026-07-24)** — Build/Update button
  removed, Run notes reordered above Command, edit-mode highlighting, device
  categories back to dropdowns. Offscreen-Qt-verified only; needs a real
  desktop click-test (this bug class — dropdown focus/timing — can't be
  fully reproduced offscreen). See DECISIONS.md.
- **Two items need ON-HARDWARE verification** by the user as `s20iduser`:
  (a) whether `ipykernel --profile=bluesky` auto-runs the collection import
  (else use "Load Bluesky"), (b) whether the viewer's Connect reaches the
  `hexm` catalog.
- **Viewer pagination fix (2026-07-22)** — backwards-offset bug fixed and
  verified against a fake catalog; a defensive fix for a suspected pymongo
  hang (`socket.setdefaulttimeout(30)`) could not be verified against real
  hardware from this dev machine. Re-test against the live `hexm` catalog.
- Profiles + device discovery + scan-block discovery are all offscreen-Qt-
  verified only — none exercised on redwood since being built.

## Next steps

- **Desktop click-test the 2026-08-01 GUI layout cleanup** (see above) before
  trusting it on redwood — offscreen Qt verification can't reproduce
  focus/timing issues.
- **Deploy B-PILOT on redwood (as s20iduser):** copy `B-PILOT/` →
  `/home/beams/S20IDUSER/bluesky/B-PILOT/`, then A/B the two launch modes.
  Revert dev-tree perms afterwards. See `.context/DEPLOY.md`.
- **Test the viewer off-hardware (on the Mac):** export a sample of the
  `hexm` catalog with `databroker-pack` → unpack → point the viewer at it.
  Read-only, no hardware. Recipe in `.context/DEPLOY.md`.
- Widen the Plan visibility default beyond the one gui-testing file as more
  plans get docstring-reformatted for the grammar.
- **Scan-building-block wiring:** the discovered catalog
  (openers/per-steps/closers/suspenders) is stored but not yet wired into
  `plan_runner.py`'s skeleton form (still a free-text fallback) — an
  unrequested follow-up, don't add speculatively.
- **AutoPILOT — two explicitly deferred initiatives** (see
  `.context/DECISIONS.md` and `.context/ARCHITECTURE.md`):
  - **MCP server** — needs `pip install mcp` (absent everywhere on this
    machine) plus a real MCP client to test against.
  - **Queue/dispatch tools** (enqueue, human-approval gate) — needs
    `build_command()` extracted from `gui_qt/plan_runner.py` (confirmed
    still absent) and a `queue_store.py` schema change (an `origin`/status
    field). Touches the literal `kc.execute()` hardware-dispatch boundary —
    its own carefully-reviewed initiative, not bundled with chat-dock work.
  - AutoPILOT docstring-assist stays chat-only (no file write) per the
    user's explicit instruction — don't add a save/promote path
    speculatively.
- Eventually: NL search over databroker for AutoPILOT (structured
  filter-dict, not embeddings) — `gui_qt/viewer.py` has the raw building
  blocks but no filter-by-metadata logic yet.

## Open questions / blockers

- QS-native (`bluesky-queueserver-api`) vs embedded-console for
  running/queuing plans — recommend QS-native. QS host is **redwood**, not
  kurtag.
- How to enumerate loaded devices for device-typed plan args (QS
  `devices_allowed` vs oregistry) — today static discovery only.
- `instrument/iconfig.yml` has live plaintext MongoDB credentials (flagged
  2026-07-23, left as-is by user's choice) — why `read_plan_file` is
  hard-scoped to `instrument/plans/` only.

## Recent changes (last 3-5 sessions, dated; drop the oldest as it grows)

- 2026-08-01: **AutoPILOT Slice 8** — drafting scope generalized from 2
  hardcoded templates to all 21 documented plans across the three plan
  files, plus `block` dtype and motor-axis qualification support and a
  prerequisite profile-config fix (see AutoPILOT section above and
  DECISIONS.md for the full trace).
- 2026-08-01: **GUI layout cleanup** — Browse button → icon, "Launch script"
  mode removed (`main_window.py`/`config.py`/`config_dialog.py`), Viewer +
  AutoPILOT moved into the Python menu, session log cleared on shutdown but
  preserved on attach (`kernel_session.py::stop()` now deletes `kernel.log`
  too). See DECISIONS.md for the per-item reasoning.
- 2026-08-01: B-PILOT and AutoPILOT context merged into `B-PILOT/CLAUDE.md`
  + `B-PILOT/.context/*`, retiring `B-PILOT/AutoPILOT/CLAUDE.md`/`.context/*`
  and trimming `mpe_bluesky/.context/*` back to instrument/QS-only content.
  Also: AutoPILOT Slice 7 (docstring-drafting assist) shipped and pushed
  (`bc54ac9`).
- 2026-07-31: AutoPILOT folded into this repo as a tracked subdirectory
  (commit `1c62ebe`), no longer a separate repo.
- 2026-07-28: AutoPILOT Slice 6 — two-tier real plan catalog + 3 lookup tools.
</content>
