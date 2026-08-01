# HIGHLIGHTS — challenges & solutions (presentation-ready)

A distilled, audience-facing log of the **problems we hit** and the
**solutions we designed**, grouped by **Robustness**, **Security/Safety**, and
**Accessibility/Usability**, plus the reusable design patterns underneath. Use
this to report or present the work; the full chronological reasoning is in
`.context/DECISIONS.md`.

> **Maintenance:** whenever we make a decision or change touching accessibility,
> security, or robustness, add a one-entry **Challenge → Solution → Impact**
> here (keep it terse), in addition to the detailed `DECISIONS.md` entry.

_Last updated: 2026-07-18._

---

## Elevator summary

We are building a PyQt GUI (plan runner + data viewer), B-PILOT, for the MPE
beamlines (APS Sectors 1 & 2) that lets users compose and queue Bluesky plans
and browse results — plus an optional agentic AI layer, AutoPILOT, on top of
it. The recurring theme of the work has been making the GUI **safe to run
without touching hardware**, **robust to being closed/crashing**, and
**usable by non-expert operators** — while fitting the beamline's existing
operational idioms (screen sessions, the queueserver, per-account beamlines).

**One absolute constraint drove many designs:** the tooling must never connect to
beamline hardware or EPICS PVs. That single rule is why we parse instead of
import, ship a static device catalog, reason statically everywhere, and why
AutoPILOT can only ever propose validated command strings, never raw code
execution.

---

## Robustness (survive crashes, disconnects, long runs)

- **GUI launch could misdirect the beamline's own logs.** *Challenge:* the
  embedded-kernel starter overwrote `dm_experiment.txt` / `setup_file.txt`
  unconditionally, so launching the GUI without an experiment set (or with a
  stale test value) would repoint the beamline's session-log path
  (`~/new_data/<dm_exp>/.logs`) — corrupting a live session's logging.
  *Solution:* the starter now writes those files only when a non-empty value is
  passed, otherwise **preserves** the existing ones; the shipped config was
  cleared to empty. *Impact:* running the GUI can no longer clobber a running
  session's experiment/log configuration.

- **Kernel died with the GUI.** *Challenge:* the interactive IPython kernel was a
  child of the GUI and (via ipykernel's parent poller) died whenever the GUI
  closed or crashed — losing a running session. *Solution:* launch the kernel as
  a fully **detached process** (own session/process group, no parent handle) and
  connect to it as a client; on GUI close we **detach, not kill**. *Impact:* a
  long acquisition survives a GUI crash or intentional close.

- **No way back to a surviving kernel.** *Challenge:* even if the kernel lived,
  you couldn't reconnect. *Solution:* a **fixed per-beamline connection file** +
  an **Attach** action; reattaching to a *busy* kernel shows an explanatory
  notice, and a **heartbeat check** distinguishes "busy" from "dead." *Impact:*
  close the GUI mid-scan, reopen, reattach — right where you left off.

- **Console history was lost on close / invisible while busy.** *Challenge:*
  qtconsole's scrollback lives in the widget, so it vanished on close, was empty
  on reattach, and stayed blank while the kernel was busy (its prompt needs the
  blocked shell channel). *Solution:* a **detached transcript recorder** streams
  the kernel's IOPub to a file continuously; a **Session-log tab** loads and
  live-tails it. *Impact:* the full history is always there, and you can watch a
  running plan's output live even before the prompt returns.

- **"Too many open files" crash on shutdown.** *Challenge:* a jupyter_client
  heartbeat **thread** threw an uncatchable traceback once the process ran low on
  file descriptors. *Solution:* shutdown uses only the shell+control channels (no
  heartbeat thread), termination is **socket-free** (screen quit + recorded-PID
  kill), heartbeat liveness uses a single **raw-ZMQ ping that fully closes**, and
  the app **raises its fd limit** at startup. *Impact:* clean, quiet shutdown
  even after a long session of launch/attach cycles.

- **Queue lost on crash; status stale when detached.** *Challenge:* the queue was
  in-memory and its scheduler lived in the GUI, so a crash lost it and nothing
  updated status while the GUI was away. *Solution:* the queue is a **persistent,
  lock-coordinated file** driven by a **detached queue-runner** process (the
  scheduler lives with the kernel, not the GUI); the panel just polls it.
  *Impact:* the queue restores on reopen and keeps progressing — with live
  status — even while the GUI is closed.

- **A chat-widget agent could subtly corrupt scan parameters.** *Challenge:*
  once AutoPILOT could propose real plan calls, a wrong/hallucinated device
  substitution needed to be visible, not silent. *Solution:* every proposed
  plan is re-validated against the same `ParamSpec`s the GUI's own forms use
  (never trust the model's tool-call conformance alone), and a heuristic
  flags any device substitution not literally named in the user's request.
  *Impact:* generated drafts are transparent about where the model filled in
  a gap.

## Security / Safety (never touch hardware; one driver at a time)

- **Introspecting plans could connect to hardware.** *Challenge:* importing a
  plan module to read its arguments would instantiate ophyd devices and attempt
  EPICS connections. *Solution:* the plan form is built by **AST parsing only —
  the file is never imported**; plans are detected as generator functions /
  `__all__` entries. *Impact:* the GUI can build rich forms with zero hardware
  contact.

- **Listing devices could connect to hardware.** *Challenge:* device-typed plan
  arguments need a picker, but enumerating live devices means importing them.
  *Solution:* device names come from **static, hardware-free discovery** (AST
  scanning `__all__`) behind a swappable interface (later replaceable by a
  live queueserver query). *Impact:* device dropdowns with no `ophyd` import.

- **Two sessions could drive the same hardware.** *Challenge:* clicking Launch
  silently started a second kernel and orphaned the first — risking **two
  RunEngines on the same PVs**. *Solution:* **single-instance enforcement** — one
  kernel per beamline, keyed on a heartbeat over the fixed connection file;
  Launch is refused with an "Attach instead" option; a CLI (`status`/`stop`)
  gives staff a non-GUI handle. *Impact:* it is structurally hard to end up with
  competing hardware drivers.

- **Correct-object command generation.** *Challenge:* a device argument must
  become the real object (`det=pg6`), not a string (`det='pg6'`). *Solution:* a
  `RawCode` marker so device fields emit **unquoted** while everything else is
  safely `repr()`-quoted. *Impact:* generated commands are correct and safe.

- **An AI agent must never get a path to raw code execution.** *Challenge:*
  once AutoPILOT could talk to a live kernel's Qt-free backend, the risk was
  it (or a prompt-injected user) reaching `kernel_session`'s unrestricted
  `execute()` primitive. *Solution:* the standalone/synergy contract hard-
  bans this — AutoPILOT only ever produces pre-validated `RE(plan(...))`
  command strings built from `plan_parser` schemas, and today doesn't even
  have a dispatch tool at all (drafts land in a gitignored folder for a
  human to review and promote manually). A new file-reading tool
  (`read_plan_file`, Slice 7) is hard-scoped to `instrument/plans/` only,
  specifically so it can never reach `instrument/iconfig.yml`'s live
  plaintext MongoDB credentials one directory up. *Impact:* the agent layer
  can get smarter about plans/devices/docstrings without ever gaining a
  hardware- or credential-reaching capability.

## Accessibility / Usability (for non-expert operators)

- **Forms from plain functions.** *Challenge:* users shouldn't hand-type
  `RE(plan(...))`. *Solution:* auto-generated parameter **forms** from each
  plan's signature + docstring, with typed widgets (numbers, choices, checkboxes,
  device pickers), live validation, tooltips, and a live command preview.

- **Nothing hardcoded.** *Challenge:* which plan files show, what "Load
  Bluesky" runs, and which beamline's devices apply were all baked in.
  *Solution:* a **Configuration window** (persistent, now profile-based) to
  set the visible-files scope, the startup command, the beamline profile, and
  session options — no code edits.

- **See what's happening at a glance.** *Challenge:* a bare list gave little
  insight into the queue. *Solution:* a **status table** — number, an **editable
  name that defaults to the plan name** (not the raw command), **colour-coded
  status** (running/waiting/done/error), and a truncated command with the **full
  command on hover**.

- **Two launch paths, switchable in one click.** *Challenge:* the GUI's
  embedded-kernel launch and the beamline's canonical launcher
  (`blueskyStarter.sh`, which activates the env, records the DM experiment, and
  starts an IPython session in `screen`) are genuinely different workflows, and
  the team wants to trial both on hardware before committing. *Solution:* a
  toolbar **Launch mode** selector — *Embedded kernel* (attach/transcript/queue)
  vs *Launch script* (runs the external launcher with GUI-declared args:
  experiment, setup file, run-as) — with the script path set in Preferences.
  Crucially, the *embedded* path also runs a starter script
  (`embedded_kernel_starter.sh`) that performs the **same env activation +
  experiment recording + `collection` load** as the beamline's canonical
  launcher, but ends in a connectable kernel — so "embedded" is fully activated,
  not a bare kernel. *Impact:* both approaches are live side-by-side and
  functionally equivalent in what they load; the team A/Bs them and drops the
  loser, with no rework.

- **Stop a run the Bluesky way, from a button.** *Challenge:* operators need
  Bluesky's Ctrl+C-once (pause at checkpoint) vs Ctrl+C-twice (pause now)
  semantics and its four recovery options — without typing at the console, and
  against a kernel we only connect to as a client. *Solution:* a **Stop-run**
  button where **click** = deferred pause and **press-and-hold >1 s** = immediate
  pause (delivered as one/two SIGINTs to the kernel PID), then **temporary
  Resume / Stop / Abort / Halt** buttons mapping to `RE.resume/stop/abort/halt`.
  *Impact:* safe, discoverable run control for non-experts.

- **Queued plans now show in the console.** *Challenge:* plans dispatched by the
  detached queue-runner (a separate kernel client) ran without their `In [N]:`
  prompt/echo appearing in the console — qtconsole only echoes its own client's
  input. *Solution:* enable qtconsole's `include_other_output` (prefix cleared),
  so activity from any client on the kernel renders like a typed cell. *Impact:*
  the console faithfully reflects everything the kernel does, including the queue.

- **Recover gracefully.** *Challenge:* detached/busy/dead states were confusing.
  *Solution:* clear, specific toolbar/status messaging for each case (reattached,
  busy, not-responding), and dialogs that offer the next useful action.

- **An AI agent that fabricates instead of asking.** *Challenge:* early
  AutoPILOT designs forced the model into exactly one tool call per turn, so
  an ambiguous or off-topic request still produced a confident, sometimes
  fabricated, plan proposal — including a silent device-name substitution the
  user never asked for. *Solution:* a real multi-turn agent loop
  (`tool_choice: "auto"`) offering lookup tools (list devices/plans, describe
  a plan, read a real file) alongside `ask_user`/`cannot_generate_plan`
  decline tools, so the model can check a fact or ask a clarifying question
  instead of guessing. *Impact:* wrong device names get caught and corrected
  instead of silently substituted; non-scan requests get a plain answer
  instead of an invented plan.

## Reusable design patterns (the transferable ideas)

- **Detached "companion" processes tied to the kernel** — recorder and
  queue-runner both outlive the GUI and do their job independently; the GUI is a
  thin client/viewer. Generalises to any "must-keep-working-if-the-UI-dies" need.
- **Fixed connection file + heartbeat as the single source of truth** — for
  "is a session running? / how do I attach? / is it alive?", beating PID- or
  lockfile-based tracking (immune to PID reuse; works while busy).
- **File + lock + poll for UI/background coordination** — atomic writes
  (`os.replace`) under `flock`, with the UI polling to display — simple, robust,
  multi-process-safe, and inherently persistent.
- **Swappable data sources behind a stable interface** — the device catalog is a
  static, discovery-based source today, a live queueserver query tomorrow, with
  no GUI change.
- **Fit the house style** — we reused the beamline's own idioms (screen sessions
  à la `qserver.sh`, the queueserver's client-independent model), so the design
  is familiar to staff and interoperates with existing tooling.
- **A guarded, optional import as the entire integration surface** — AutoPILOT
  plugs into B-PILOT through exactly one `try/except ImportError`
  (`gui_qt/autopilot_bridge.py`), so an entire agentic subsystem can be added,
  removed, or broken without ever risking the host application's own
  stability. Generalizes to any optional-feature-on-a-stable-host design.

## Framing note for a presentation

The interactive-kernel machinery above deliberately mirrors the **Bluesky
queueserver** (persistent, client-independent, single-driver). At MPE the
queueserver remains the production run path; this GUI adds an approachable,
robust *interactive* layer on top and is designed to later point directly at the
queueserver with minimal change. AutoPILOT extends that same philosophy one
layer further: an optional, safety-boxed AI assistant that only ever proposes
work through the exact same validated, human-reviewed paths a person would
use.
</content>
