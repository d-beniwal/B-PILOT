# DEPLOY — moving the GUI to the beamline workstation

_What Claude (and the user) need to bring-up the MPE plan-runner GUI on the
actual beamline workstation, and what Claude must/mustn't do there._
_Created 2026-07-18 on the dev Mac, for the first beamline session. Paths
updated 2026-08-01 for the `gui/` → `B-PILOT/` rename (2026-07-20) — the
deploy layout target is now `bluesky/B-PILOT/gui_qt`, not `bluesky/gui/gui_qt`._

## ⚠️ SAFETY (applies to Claude on the beamline — absolute)

On the beamline workstation the standing rule still holds: **Claude never
connects to hardware or EPICS PVs and never runs code that talks to
instruments.** That means Claude does **not** launch the kernel against
hardware, does **not** run plans/the RunEngine, does **not** click "Launch
IPython" / "Load Bluesky" / run the queue. The *user* does all of that. Claude
edits source, sets config, and verifies things that don't touch hardware
(imports, path resolution, `py_compile`, offscreen Qt). "We're on the beamline
to execute" does **not** relax this.

## What you brought (into `<beamline>/mpe_bluesky/`)

- `B-PILOT/` — the GUI bundle: `gui_qt/`, `profiles/`, `embedded_kernel_starter.sh`,
  `gui_config.json`, `AutoPILOT/` (optional, see `.context/ARCHITECTURE.md`'s
  AutoPILOT section), `.context/` (this context), `CLAUDE.md`.

The GUI finds everything else relative to its own location via
`B-PILOT/gui_qt/paths.py` (project root = walk up for `instrument/` +
`blueskyStarter.sh`/`qserver.sh`), so no path edits are needed after the copy.

## Prerequisites that must ALREADY exist on the beamline (verify, don't create)

The GUI depends on these OUTSIDE the `B-PILOT/` folder — part of the existing
beamline bluesky install:

1. `<root>/blueskyStarter.sh` — the script-launch mode + the embedded starter's
   env-pick logic mirror it. Its `DEFAULT_ENV` should be the real beamline env
   (dev copy says `bluesky_2024_2`).
2. `<root>/instrument/` (importable) and `<root>/instrument/iconfig.yml` — the
   kernel's `from instrument.collection import *` (cwd defaults to project root)
   and the viewer's databroker defaults read these.
3. `<root>/user/user_defaults/` — the starters write `dm_experiment.txt` /
   `setup_file.txt` here (skipped with a warning if absent).
4. **IPython `bluesky` profile** at `~/.ipython/profile_bluesky/startup/…`
   (`__start_bluesky_instrument__.py`) — the embedded kernel starts with
   `--profile=bluesky` and relies on it to auto-run the collection import.
5. **`screen`** installed — the kernel + queue are hosted in screen sessions.
6. A **conda env for the KERNEL** with the instrument stack — this is the
   beamline runtime env (`bluesky_2024_2`), activated by the starter script.
7. A **conda env for the GUI PROCESS** (the PyQt app itself) with: `PyQt5`,
   `qtconsole`, `qtpy`, `matplotlib`, `jupyter_client`, `ipykernel`, `databroker`
   (+ `anthropic` if running with AutoPILOT enabled). The GUI process and the
   kernel are decoupled (client connects over a connection file), so they can
   be the same env or different ones.
   - The beamline `bluesky_2024_x` env likely already has these (the `jupyter`
     metapackage pulls `qtconsole`/`qtpy`; `pyqt =5`/`qt =5` are in
     `environments/environment_2024_1.yml`). **Verify** before assuming.
   - If it does NOT, either install the missing bits into it, or make a
     dedicated GUI env. `B-PILOT/environments/mpe_bluesky_dev.yml` (on the dev
     Mac) is a reference for the exact deps — but it is **macOS-pinned and
     excludes `hklpy`/`aps-dm-api`**, so on Linux recreate/resolve fresh rather
     than copying pins verbatim.

## Do you need to bring anything else?

- **Not strictly.** The GUI + its runtime deps come from the beamline env; the
  `B-PILOT/` folder covers the app + context. Optionally bring
  `B-PILOT/environments/mpe_bluesky_dev.yml` as a *dependency reference* for
  the GUI env (see #7). Nothing else is required.

## Beamline-specific things to CONFIGURE on-site (in-GUI / config, not hardcode)

- **Profile** — switch to (or create) the profile matching the actual
  beamline (`20ide`/`s1id`/`s20idd`) via the Configuration dialog's profile
  bar; this drives `beamline` id, device search paths, plan visibility, etc.
  all at once (see `.context/ARCHITECTURE.md`'s "Beamline profiles" section)
  — superseded the old single `beamline` config key + toolbar picker.
- Viewer **catalog** — DONE (2026-07-18): `viewer.py` now defaults to the
  account's databroker catalog (`hexm` for s20iduser, from iconfig). Just click
  Connect. The optional Data Viewer settings (Configuration dialog) override
  it if ever needed.
- `dm_experiment` / `setup_file` — `gui_config.json` is now just a profile
  pointer; the starter preserves the live `dm_experiment.txt` when the GUI
  value is empty. Set the real experiment in the GUI (Configuration → Launch
  Session) only if you want the GUI to drive it; otherwise it inherits
  whatever `blueskyStarter.sh` last recorded.
- Launch mode — A/B **Embedded kernel** vs **Launch script** on hardware, then
  drop the loser (this was the plan; see STATE.md "Next steps").

## Known 3-ID-C leftovers — FIXED 2026-07-18

- `plan_runner.py` fallback module `"id3c.user.db_bps"` → `instrument.collection`. ✅
- `viewer.py` Tiled/`sn.xray` defaults → databroker `hexm` (see above). ✅
- 3-ID-C docstrings/comments in `plan_runner.py` / `plan_parser.py` reworded. ✅

## Still open (future sessions)

- Parser scope: widen `visible_plan_files` beyond the one docstring-reformatted
  gui-testing file as more of `instrument/plans/` gets reformatted for the
  grammar (see `.context/STATE.md`).
- QS host is now **redwood** (this workstation), not kurtag (`_run_qs.sh`). If a
  QS-native queue path is pursued, point clients at `redwood.xray.aps.anl.gov`.

## How to run (user)

```
conda activate <gui-env>        # beamline env, or a dedicated GUI env
cd <root>/mpe_bluesky/B-PILOT
python launch.py                # or: python -m gui_qt
```

## Testing the viewer off-hardware (e.g. on the dev Mac)

You can exercise the databroker viewer without hardware by exporting a sample of
the real catalog and reconstituting it elsewhere. This is **read-only** on the
MongoDB metadata store — it touches no EPICS/hardware/QS.

1. **On redwood, as `s20iduser`, in the bluesky env** — pack the newest few runs,
   **documents-only** (scalar streams + metadata are inline; detector frames are
   external and stay behind — the viewer only previews scalar columns anyway):
   ```bash
   conda activate /home/beams/S20IDUSER/.conda/envs/bluesky_2024_2
   python - <<'PY'
   from databroker import catalog
   uids = list(catalog['hexm'])[-10:]
   open('uids.txt','w').write("\n".join(uids)+"\n")
   PY
   databroker-pack hexm ./hexm_sample --uids uids.txt   # NO --copy-external (small)
   tar czf hexm_sample.tgz hexm_sample
   ```
2. **On the target machine** (env with `databroker`, e.g. `mpe_bluesky_dev`):
   ```bash
   tar xzf hexm_sample.tgz
   databroker-unpack inplace ./hexm_sample hexm_test   # registers catalog 'hexm_test'
   ```
3. In the viewer, type `hexm_test` in the **Catalog** field → **Connect**.

Caveats: run the pack as `s20iduser` (the `hexm` intake config lives in *their*
`~/.local/share/intake/MPE_mongodb.yml`); a documents-only pack has no detector
images; add `--copy-external` only for 1–2 runs if you must (HEDM frames are
huge).

## First beamline session — Claude's checklist

1. Read STATE.md + this file. Re-confirm the SAFETY rule above.
2. Verify prerequisites #1–#7 exist (filesystem checks + `conda list` in the GUI
   env; NO hardware/kernel launch). Report gaps.
3. `py_compile` + offscreen import check of `gui_qt.*` in the GUI env; confirm
   `paths.PROJECT_ROOT`/`ICONFIG`/`BLUESKY_STARTER` resolve to real files.
4. Help set the beamline-specific config (above) and fix any MPE leftovers.
5. Hand off to the user to launch/A-B the two modes; iterate on what they report.
</content>
