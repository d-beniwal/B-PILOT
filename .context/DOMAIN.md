# DOMAIN

<!-- On-demand: domain knowledge, terminology, external facts, constraints.
     Read only when a task needs it. Not auto-loaded. -->

## `scan_skeletons.py` building blocks (learned 2026-07-24, for B-PILOT direct-handling work)

`instrument/plans/scan_skeletons.py` (`mpe_list_scan`, `mpe_list_grid_scan`,
`mpe_step_scan`, `mpe_step_grid_scan`, `mpe_rel_scan`, `mpe_rel_grid_scan`)
each take `plan_opener`, `per_step`, `plan_closer`, `suspenders`,
`pseudo_suspenders` keyword args — these are Python **function/object
references**, not primitives, so they need their own discovery/cataloging
(this is the gap B-PILOT's `acquisition_modes` profile setting currently
fills by hand — see below).

- **`plan_opener` / `per_step` / `plan_closer`** — sourced from
  `instrument/plans/scan_hw_triggering.py` (hardware-triggered: `hardware_opener`,
  `hardware_sweep_`, `hardware_closer`) and `scan_sw_triggering.py`
  (software-triggered: `non_countable_opener`/`scaler_opener`/`generic_opener`,
  many `*_step_and_shoot_`/`*_sweep_`/`*_ladder_` per-steps,
  `generic_closer`/`scaler_closer`). Both files are common to every MPE
  beamline. Each file's `__all__` list is grouped under section comments —
  `# scan openers`, `# per-step plan stubs`, `# scan closers`, `# lists of
  persteps` — which is how you tell a real per-step function apart from a
  *list* of per-steps (`persteps_with_hardware` etc. are bookkeeping arrays
  used internally by `scan_skeletons.py` for validation, e.g. "can't use a
  sweep per-step with a list scan" — not themselves selectable values for
  `per_step`).
- **`suspenders` / `pseudo_suspenders`** — sourced from
  `instrument/plans/suspenders.py` (common; `__all__` has a `# signals`
  group — `beam_current`/`shutter_permit`, NOT suspenders, just the Signals
  a suspender watches — and a `# suspenders` group, which are true
  suspenders) and `suspenders_pseudo.py` (common; `__all__` has a
  `# pseudos/mutators` group — `filename_mutator`/`drive_checker`, true
  pseudo-suspenders with `pre_exposure()`/`post_exposure()` hooks called
  directly inside the per-step loop — and a `# true suspenders` group,
  e.g. `disk_monitor`, a real `bpp.suspend_decorator`-compatible suspender
  built from one of the pseudo signals). **The section comment inside
  `__all__` is the only signal for true-vs-pseudo** — there's no naming
  convention or class check that reliably distinguishes them, so cataloging
  these needs to parse the comments preceding each name group (`ast` alone
  discards comments; needs `tokenize` or a text-based pass), not just the
  list of exported names.
- **Beamline-specific suspenders** live one per beamline under
  `instrument/plans/<bl>_plans/<bl>_suspenders.py` (`s1id_suspenders.py`,
  `s20idd_suspenders.py`, `s20ide_suspenders.py`, plus ad hoc ones like
  `s20ide_plans/user_plans/soh_apr26_suspenders.py`). None of these files'
  `__all__` lists have a pseudo/true comment split — everything in them is a
  true suspender (the pseudo/mutator concept only exists in the two common
  files above).
- **Existing hand-curated analog, already in the codebase:** B-PILOT's
  profile config already has an `acquisition_modes` setting
  (`gui_qt/config.py` DEFAULTS, consumed in `gui_qt/plan_runner.py`'s
  skeleton form) mapping a human label to a `{plan_opener, per_step,
  plan_closer}` name trio — but it's **hand-curated on purpose**: its
  own comment says never to auto-scrape it from *user* plan files, several
  of which reference broken/undefined per_step names. That caution is about
  scraping *combinations* out of arbitrary user plans, not about cataloging
  the *individual* building-block names out of the canonical common files
  (`scan_hw_triggering.py`/`scan_sw_triggering.py`/`suspenders.py`/
  `suspenders_pseudo.py`/`<bl>_suspenders.py`) — which is what the new
  discovery work targets (see `.context/DECISIONS.md` 2026-07-24 entry and
  [[scan_skeletons_scope]] memory). `suspenders`/`pseudo_suspenders` aren't
  exposed in the skeleton form at all yet — they fall through to the
  ordinary docstring-driven param grid as a plain text field.

## MPE beamline infra facts (learned on redwood, 2026-07-18)

These are the concrete facts the GUI depends on, verified read-only against the
live install at `/home/beams/S20IDUSER/bluesky`.

### Accounts → beamline → catalog (from `instrument/iconfig.yml`)

`instrument/collection.py` gates devices/plans on `getpass.getuser()`, and each
account maps to a databroker catalog (`DATABROKER_CATALOG` in iconfig):

| Unix account         | Beamline    | databroker catalog | Mongo host   |
|-----------------------|-------------|--------------------|--------------|
| `s1iduser`/`s1idtest`| 1-ID-C,E    | `1id_hexm`         | dbbluesky1   |
| `s20hedm`            | 20-ID-D     | `ht_hedm`          | dbbluesky3   |
| `s20iduser`          | 20-ID-E     | `hexm`             | dbbluesky3   |

- **The GUI + kernel must run as the beamline account** (we use `s20iduser`).
  Any other account (e.g. the dev `dbeniwal`) breaks `from instrument.collection
  import *` — see below.
- Catalog **intake config** (URIs): `~/.local/share/intake/MPE_mongodb.yml` in
  the beamline account's home. Currently only `hexm` is active. Driver:
  `bluesky-mongo-normalized-catalog`. The viewer connects via
  `databroker.catalog[<name>]` — no Tiled.

### Session load path (why account matters)

- Console: `~/.ipython/profile_bluesky/startup/__start_bluesky_instrument__.py`
  → adds `~/bluesky` to `sys.path` → `from instrument.collection import *`.
- **`instrument/session_logs.py` opens `~/bluesky/user/user_defaults/
  dm_experiment.txt` at import time** (unconditional `open()`), so the account
  must have that file — another reason the kernel runs as the beamline account.

### Where things are written (beamline account home)

- **Data:** `~/new_data/<dm_experiment>/` (one dir per experiment; current:
  `liss_jul26`).
- **Session logs:** `~/new_data/<dm_experiment>/.logs/` — `ipython_logger.log`,
  `raw_console.log` (written by `session_logs.py`; `LOG_PATH` there points here).
  ⇒ the GUI must NOT clobber `dm_experiment.txt`, or a live session's logs get
  misdirected (guard added in `embedded_kernel_starter.sh`, 2026-07-18).
- **RunEngine metadata:** `~/.config/Bluesky_RunEngine_md/` (perm-restricted).
- **GUI runtime state:** `~/.bluesky_pilot/<beamline>/` (connection file, queue,
  transcript) — per-user, separate from beamline data.

### Servers / env

- **QS + redis host is now `redwood.xray.aps.anl.gov`** (was kurtag) —
  `_run_qs.sh`. Redis is localhost on that host.
- **Beamline conda env:** `bluesky_2024_2` at
  `/home/beams/S20IDUSER/.conda/envs/`. QS activates it from base
  `/APSshare/miniconda/x86_64`; `blueskyStarter.sh` / `embedded_kernel_starter.sh`
  `pick()` it via the same search order.

### Testing the viewer without hardware

`databroker-pack` (documents-only) on the beamline account → copy →
`databroker-unpack inplace` on the dev machine → point the viewer's Catalog
field at the unpacked name. Read-only, no hardware. See `.context/DEPLOY.md`.
</content>
