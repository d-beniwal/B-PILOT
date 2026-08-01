# B-PILOT — project context

**B-PILOT** ("Bluesky-PILOT") is a PyQt GUI for composing, queuing, and
monitoring Bluesky plans on the **MPE beamlines** (APS Sectors 1 and 20:
1-ID and HEXM / 20-ID), plus a databroker-backed data viewer. It wraps the
`mpe_bluesky` instrument package (`instrument/`) — this is a GUI layer, not
the instrument codebase itself. Never connect to hardware or EPICS PVs; edit
source only. On the beamline workstation this extends to: never launch the
kernel against hardware, never run plans/the RunEngine, never click "Launch
IPython"/"Load Bluesky"/run the queue — only the user does that (see
`.context/DEPLOY.md`'s SAFETY section for the full on-site checklist).

This is its own git repo (pushed to `github.com/d-beniwal/B-PILOT`), nested
inside the `mpe_bluesky` tree (which is itself git-tracked, `B-PILOT/` kept
gitignored there so the two histories stay independent).

## Always-loaded state (imported → costs tokens every session — keep short)

@.context/STATE.md

## On-demand references (NOT imported — read only when the task needs them)

- `README.md`                — package layout, how to launch, env setup
- `.context/ARCHITECTURE.md` — layout, launch-mode internals, path
  resolution, beamline profiles + device discovery, and the full AutoPILOT
  integration contract
- `.context/DECISIONS.md`    — append-only log of decisions and *why*
  (newest first; merges B-PILOT's and AutoPILOT's former separate logs)
- `.context/DOMAIN.md`       — MPE domain knowledge B-PILOT depends on
  (scan-building-block catalog, beamline account/catalog facts)
- `.context/DEPLOY.md`       — beamline bring-up checklist (prereqs, on-site
  config, the on-beamline safety rule). Read this before deploying/running
  on the beamline workstation.
- `.context/PORTING_NOTES.md` — the original 3-ID-C → MPE porting diary
  (why the parser grammar, catalog, and startup logic look the way they do)
- `.context/HIGHLIGHTS.md`   — presentation-ready challenge→solution log
  (accessibility / security / robustness). **Maintain it:** whenever a
  change touches one of those pillars, add a terse Challenge→Solution→Impact
  entry.

For the underlying `instrument`/queueserver mechanics B-PILOT wraps (not
duplicated here), see `mpe_bluesky/.context/ARCHITECTURE.md`. For any
Bluesky work, use the global **`bluesky` skill** and keep its memory current.

## AutoPILOT (optional subsystem, lives in this same repo)

`AutoPILOT/` is an optional agentic AI layer (natural-language plan-drafting,
lookup tools over devices/plans/docstrings) embedded as a chat dock inside
B-PILOT's own window. It was a separate git repo until 2026-07-31, when it
was folded into this repo as a tracked subdirectory — it is **not** a
separate project anymore, and its docs live here, in this project's own
`.context/`, not in a folder of their own. **B-PILOT must always run
standalone with `AutoPILOT/` absent** — the dependency arrow points
AutoPILOT → B-PILOT only, via one guarded `try/except ImportError`
(`gui_qt/autopilot_bridge.py`). See `.context/ARCHITECTURE.md`'s "AutoPILOT"
section for the full contract, integration surface, and pipeline design.
</content>
