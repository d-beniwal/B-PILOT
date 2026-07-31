# Project context

AutoPILOT: agentic AI layer (natural-language data search, plan-building,
GUI driving, queue control) for **B-PILOT**, the PyQt Bluesky GUI for the
MPE beamlines (Sectors 1/20). Optional add-on — **B-PILOT must always run
standalone without AutoPILOT present.**

This is a separate git repo, nested inside `B-PILOT/AutoPILOT/` and
gitignored from B-PILOT's own repo (see `../.gitignore`). Never connect to
beamline hardware or EPICS PVs; edit source only.

## Always-loaded state (imported → costs tokens every session — keep short)

@.context/STATE.md

## On-demand references (NOT imported — read only when the task needs them)

- `.context/DECISIONS.md`   — append-only log of decisions and *why* (newest first)
- `.context/ARCHITECTURE.md` — system shape, components, data flow, the
  standalone/synergy contract with B-PILOT
- `.context/DOMAIN.md`       — domain knowledge, terminology, external facts

For any Bluesky work, use the global **`bluesky` skill** and keep its memory
current. B-PILOT itself is documented in `mpe_bluesky/.context/ARCHITECTURE.md`
(it has no `.context/` of its own yet).

<!--
Two-layer rule: only STATE.md auto-loads. Do NOT add @-imports for the
on-demand files, or they will burn tokens every session. The `.context/`
folder is committed to git so this learning travels with the repo.
-->
