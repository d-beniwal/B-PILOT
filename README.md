# B-PILOT (Bluesky-PILOT)

A PyQt5 GUI for running [Bluesky](https://blueskyproject.io/) plans at APS
beamlines: point it at a plan module, get a parameter form generated straight
from the plan's docstring, run it in a live embedded IPython console, queue up
a sequence of runs, and page through the resulting data — all from one
window.

Built for the MPE beamlines (APS Sectors 1 and 20 — 1-ID and HEXM/20-ID), and
designed to generalize to other beamlines' Bluesky instrument packages over
time.

## Features

- **Docstring-driven parameter forms** — no plan-specific GUI code to write.
  Document a plan's arguments in a small NumPy-style `Parameters` grammar
  (see `gui_qt/plan_parser.py`) and B-PILOT builds a typed form for it
  automatically: text/number fields with live validation, dropdowns for
  `choice{...}` options, and device pickers for `device{category}` /
  `device_list{category}` arguments. Plan files are read with `ast` only —
  **never imported** — so building the form never touches EPICS or connects
  to hardware.
- **Embedded IPython console** — a persistent, detachable Bluesky kernel
  (single instance per beamline, hosted in `screen`) that survives closing
  and reopening the GUI, with a full session transcript.
- **Run queue** — build up a sequence of plan invocations, run them
  unattended, and track status per item.
- **Run controls** — pause/resume/stop/abort the RunEngine from the toolbar
  without switching to a terminal.
- **Data viewer** — browse runs from a `databroker` catalog.
- **Configurable plan scope** — a Configuration dialog controls which
  directory is scanned for plans, which files are even shown in the file
  browser ("Plan visibility," with select-all/deselect-all), and what startup
  command loads the beamline's device/plan session.

## Requirements

A PyQt5 + Bluesky/ophyd environment. The environment this was developed and
verified against lives one level up, at `../environments/mpe_bluesky_dev.yml`
(PyQt5, qtconsole, ipykernel, bluesky, ophyd, databroker, queueserver, etc.,
python 3.11). Create it with:

```bash
conda env create -f ../environments/mpe_bluesky_dev.yml
conda activate mpe_bluesky_dev
```

## Running it

From inside this directory:

```bash
python launch.py
```

equivalently:

```bash
python -m gui_qt
```

## Where it needs to live

B-PILOT auto-discovers the Bluesky instrument package it belongs to by
walking **up** from its own location looking for an `instrument/` directory
alongside a `blueskyStarter.sh` or `qserver.sh` script (see
`gui_qt/paths.py`). That means B-PILOT should sit as a subfolder directly
inside a beamline's Bluesky project root, e.g.:

```
<beamline-bluesky-project>/
├── instrument/
├── blueskyStarter.sh (or qserver.sh)
└── B-PILOT/            <- this repo
    ├── launch.py
    └── gui_qt/
```

Everything else (which directory holds plans, where the device manifest is,
where runtime/session state is kept) is derived from that discovery — no
hard-coded absolute paths, so the same checkout works unmodified regardless
of where the parent project lives on disk.

## Configuring which plans show up

Open **Python → Configuration…** in the menu bar:

- **Files** — the plans directory scanned, the import root used to build the
  generated `from <module> import <plan>` line, and the default file checked
  on startup.
- **Plan visibility** — every `.py` file found under the plans directory,
  with a checkbox for whether it appears as a row in the main window's file
  browser at all (Select all / Deselect all / Refresh list). This is separate
  from the per-row checkbox in the main panel, which controls whether a
  *visible* file's plans are merged into the plan dropdown.
- **Launch** — the command(s) run in the console on "Load Bluesky" (e.g.
  `from instrument.collection import *`), and whether the kernel is kept
  alive when the GUI closes.

## Status

Actively developed for MPE (Sectors 1/20). The plan-parsing grammar,
device-picker, queue, and console machinery are beamline-agnostic by
construction. Two beamline-specific pieces are already just Configuration
values, not code: the plans directory/visibility and the launch/startup
commands. One is still a data file that ships alongside the GUI rather than a
Configuration setting — `device_manifest.yml` (which categories of device
names populate the `device{...}`/`device_list{...}` pickers) — so a new
beamline currently supplies its own manifest by editing/replacing that file.
The intent is for other beamlines to point B-PILOT at their own
`instrument/` package, drop in their manifest, and Configure the rest.
