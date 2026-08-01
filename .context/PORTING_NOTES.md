# Porting the plan-runner GUI: 3-ID-C → MPE (Sectors 1 / 20)

This project started as a **verbatim copy** of the working PyQt GUI from
`3idc-bits/scratch/gui_qt/` (our agreed starting point). The code was
unchanged at first so we had a clean, known baseline. This document records
**how the MPE Bluesky setup differs from 3-ID-C**, so we know exactly what to
adapt when building the MPE version (most of it has since been adapted —
this remains the reference for *why* it looks the way it does).

---

## TL;DR — the four differences that mattered most for the GUI

| Concern | 3-ID-C (`id3c`) | MPE (`instrument`) |
|---|---|---|
| **Startup import** | `from id3c.startup import *` | `from instrument.collection import *` (console, **username-gated**) or `from instrument.queueserver import *` (QS) |
| **How plans run** | Embedded IPython kernel → `RE(plan(...))` | **Bluesky queueserver (QS)** over ZMQ; native client is `queue-monitor` |
| **Plan discovery** | `@plan` decorator + strict NumPy `Parameters` grammar | plain **generator functions**, `__all__` exports, **uppercase `PARAMETERS`** docstrings (looser) |
| **Data catalog** | Tiled URI `http://sn.xray.aps.anl.gov:8000` | **databroker + MongoDB** catalogs (`1id_hexm`, `ht_hedm`, `hexm`) |

---

## 1. Startup / how the session loads

**3-ID-C:** one startup module, hardware loads on `from id3c.startup import *`.

**MPE:** two entry points, and *which* devices/plans load depends on the Unix
**account** you are logged in as:

- **Console session** (`console/__start_bluesky_instrument__.py`) →
  `from instrument.collection import *`. `instrument/collection.py` branches on
  `getpass.getuser()`:
  - `s1iduser` / `s1idtest` → `devices.s1id_devices` + `plans.s1id_plans`
  - `s20hedm` → `devices.s20idd_devices` + `plans.s20idd_plans`
  - `s20iduser` → `devices.s20idd_devices` **and** `devices.s20ide_devices` +
    `plans.s20ide_plans`
- **Queueserver** (`qstarter.py` → `from instrument.queueserver import *`) loads
  devices/plans **unconditionally** (`from .devices import *`,
  `from .plans import *`), i.e. not username-gated.

**GUI implication:** there is no single "load Bluesky" line. The beamline is
selected by account (1-ID vs 20-ID-D vs 20-ID-E) — B-PILOT's **profile**
system (see `.context/ARCHITECTURE.md`) is the resolution to this: pick the
profile matching the beamline instead of auto-detecting the account.

## 2. How plans actually run — queueserver, not embedded RE

3-ID-C drove an **out-of-process IPython kernel** and sent `RE(plan(...))`
strings to it; the queue was an in-GUI scheduler.

MPE is built around the **Bluesky queueserver (QS)**:

- QS daemon runs on the beamline workstation (was `kurtag`, now **`redwood`**
  as of the 2026-07-18 session — see DECISIONS.md) via `qserver.sh start`,
  which runs `_run_qs.sh` → `start-re-manager --startup-dir <project root>
  --keep-re …`.
- Clients connect over **ZMQ**: control port `60615`, info port `60625` (an
  HTTP server on `:60610` is referenced in comments).
- The native GUI is **`queue-monitor`** (from `bluesky-queueserver`): connect →
  open environment → add plans to the queue → run the queue.
- Data-processing host: **`erkel`**.

**GUI implication (design decision, still open):** the "run a plan" and
"queue" panels could talk to the **QS API** (`bluesky-queueserver-api`, ZMQ or
HTTP) rather than pasting `RE(...)` into an embedded console — this remains
open (see `.context/STATE.md` "Open questions"). B-PILOT today drives an
embedded/persistent interactive kernel (its own queue implementation mirrors
the QS's persistent, client-independent shape deliberately — see
`.context/HIGHLIGHTS.md`'s framing note) rather than the production QS
itself.

## 3. Plan discovery & the docstring/parameter form

This was the biggest change for `plan_parser.py` (now done — this section is
historical reference for the grammar's origin).

**3-ID-C convention** (what the original parser expected):
- functions decorated with `@plan`
- title-case `Parameters\n----------` NumPy section
- `name : dtype [units]` then indented `short :: long`
- dtype ∈ {str, int, float, bool, `choice{...}`, positions}

**MPE convention** (observed in `instrument/plans/…`):
- **Plain generator functions** — no `@plan` decorator. Plans are identified by
  being generator functions (contain `yield`) and/or by being listed in each
  module's `__all__` (e.g. `scans_standard.py` exports `tomoscan_sw`,
  `hedmscan_sw`, `pfscan_sw`, `supersweep_sw`, `grid_sweepx_sw`,
  `grid_stepx_sw`). The QS itself lists plans with `inspect.isgeneratorfunction`
  (see `print_plans()` in `queueserver.py`).
- Docstrings, when present, use an **uppercase `PARAMETERS\n----------`** section
  with `name : type` + an indented free-text description. **No `[units]`
  suffix and no ` :: ` short/long split.** Example verbatim from
  `tomoscan_sw` (`instrument/plans/scans_standard.py`):

  ```
  PARAMETERS
  ----------
  exptime : float
      Exposure time in seconds.
  fname : str
      File name (must be in single or double quotes).
  det : `MPEAreaDetector`
      Area detector obkject collecting data.
  sms : Device
      Sample manipulation stage being used (ex samC, samD, or samE)
  bright_config : str, optional
      ...
  ```
- The type field is **descriptive prose**, and some args are **device objects**
  (``det : `MPEAreaDetector` ``, `sms : Device`). Those cannot be free-typed into
  a scalar field — the GUI needs a **dropdown of loaded devices of that type**.
  B-PILOT's grammar was extended with `device{<category>}`/`device_list{<category>}`
  dtypes plus a static device catalog to resolve exactly this (see
  `.context/DECISIONS.md` 2026-07-16).
- Required vs optional still derivable from the **signature** defaults (and the
  docstring often says `, optional`). Many plans are undocumented — the generic
  "arguments as Python" fallback form matters more here.

**GUI implication (done):** the parser detects generator functions /
`__all__` instead of `@plan`; parses the uppercase `PARAMETERS` grammar with a
looser type map; treats device types specially via a device dropdown. Note
also **type annotations** may be present on some signatures and are a cleaner
source than docstring prose where available.

**Keep parsing AST-only.** As in 3-ID-C, importing a plan module must be avoided
— MPE modules instantiate ophyd device objects at import (module-level vars) and
`instrument/framework/initialize.py` builds an **`oregistry`
(`ophydregistry.Registry(auto_register=True)`)** so any imported device is
auto-registered and connections attempted. The parser reads files with `ast`
only.

**Decorator gotcha for plan detection:** several plans apply preprocessor
decorators to a **nested `inner()`** and `return (yield from inner())`, so the
top-level function is still a generator — detecting "contains a yield anywhere"
or "is a generator function" works; keying on a decorator does not.

**Where user plans live:** `user/user_plan_template_{1id,s20idd,s20ide}.py` plus
per-user files under `instrument/plans/<beamline>_plans/user_plans/*.py` (dozens,
named `lastname_monthYY.py`). Users iterate with
`%run ~/bluesky/instrument/plans/<beamline>_plans/user_plans/<file>.py`. B-PILOT's
Plan visibility setting (`.context/DECISIONS.md` 2026-07-20) points at these
real directories, not a scratch folder.

## 4. Data catalog / viewer

**3-ID-C:** Tiled server URI `http://sn.xray.aps.anl.gov:8000` (`/raw` tree),
config from `iconfig.yml` (`TILED_PROFILE_NAME`/`TILED_PATH_NAME`).

**MPE:** **databroker catalogs backed by MongoDB**, chosen per account in
`instrument/iconfig.yml`:
- `s1iduser`/`s1idtest` → catalog **`1id_hexm`** (`dbbluesky1.xray.aps.anl.gov`)
- `s20hedm` → catalog **`ht_hedm`** (`dbbluesky3…`)
- `s20iduser` → catalog **`hexm`** (`dbbluesky3…`)

RE wiring (`instrument/queueserver_framework.py`):
`cat = databroker.catalog[iconfig[USERNAME]["DATABROKER_CATALOG"]]`, then
`RE.subscribe(cat.v1.insert)` (falls back to `databroker.temp().v2`). Also
optional SPEC file writer (`WRITE_SPEC_DATA_FILES: true`, `callbacks/
spec_data_file_writer.py`) and APS **Data Management (DM)** hooks
(`framework/dm_setup.py`, `plans/dm_workflows.py`).

**GUI implication (done):** `viewer.py` opens a **databroker catalog by name**
(`databroker.catalog['1id_hexm']`), not a Tiled URI. The catalog list depends on
`~/.local/share/intake/*.yml` on the workstation. Keeps a temp-catalog fallback.

## 5. Project layout quick map (MPE)

- `instrument/collection.py` — console startup (username-gated).
- `instrument/queueserver.py` + `queueserver_framework.py` — QS startup, builds
  `RE`, `cat`, `sd`.
- `instrument/framework/` — `initialize.py`, `metadata.py`, `dm_setup.py`,
  `check_python.py`, `check_bluesky.py`.
- `instrument/devices/` — per-beamline device modules (`s1id_devices/`,
  `s20idd_devices/`, `s20ide_devices/`, `global_variables.py`, …).
- `instrument/plans/` — generic workhorse plans + `s1id_plans/`, `s20idd_plans/`,
  `s20ide_plans/`, each with a `user_plans/` subdir.
- `instrument/callbacks/` — `spec_data_file_writer.py`.
- `user/` — templates + `exp_setup.yml`, tomocupy args, `user_defaults/`.
- **Project root** — QS startup dir; **every `.py` here runs on QS start**
  (`qstarter.py`). Do not add code to the root.

## 6. Open questions (status as of this merge, 2026-08-01)

1. **QS-native vs embedded-console** for running/queuing plans — still open,
   still leaning QS-native (`bluesky-queueserver-api`). See `.context/STATE.md`.
2. Beamline selection — **RESOLVED**: switching beamline is now "switch
   profile" in the Configuration dialog (2026-07-21), not an account
   auto-detect or a separate toolbar picker.
3. ~~Device-typed plan args: dropdown from QS `devices_allowed`, or free text?~~
   **RESOLVED (2026-07-16):** `device{cat}` / `device_list{cat}` dtypes +
   `plan_parser.RawCode` (unquoted emission) + static device discovery behind
   the swappable `device_source.DeviceCatalog` interface. See DECISIONS.md.
4. Parse plans by AST (offline) or by QS introspection (`plans_allowed`,
   `plans_existing` carry signatures + docstrings)? Possibly both — still
   AST-only today.
5. Viewer: databroker-by-name is confirmed and shipped; live-run plots via QS
   console stream / `BestEffortCallback` not yet pursued.
</content>
