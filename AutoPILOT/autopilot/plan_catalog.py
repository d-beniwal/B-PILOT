"""Per-profile plan lookup, reusing B-PILOT's Qt-free plan_parser (AST-only,
never imports a plan module -- same safety guarantee as device_catalog.py).

Two tiers, both read-only, both real plans that exist in ``instrument/plans/``:

* "vetted" -- exactly the profile's ``visible_plan_files`` whitelist (the
  same files the B-PILOT GUI's plan-runner file browser shows). Fully
  documented in the NumPy grammar ``plan_parser.find_plan_specs`` expects, so
  ``params`` is always complete and accurate.
* "extended" -- a hand-maintained allowlist (see ``_EXTENDED_SHARED_FILES``
  and ``_beamline_extended_files()`` below) covering the rest of the real,
  exported (``__all__``-listed) plans in instrument/plans/: grid/alignment/
  tomography scans and beamline-specific plans the vetted tier never
  surfaces. These files mostly predate B-PILOT's parameter-documentation
  grammar, so ``documented=False`` and ``params=()`` is common and NOT the
  same thing as "takes no parameters" -- callers must not conflate the two.

Deliberately excludes ``archived_plans.py`` (explicitly deprecated, no
``__all__``) and any ``user_plans/`` subdirectory (dated, per-person
beamtime scripts, not general-purpose).

Every real profile's ``visible_plan_files`` includes both a legacy
``*_gui_testing.py`` file and its ``bpilot/*.py`` canonical copy -- these
parse to byte-identical specs (confirmed by diffing during the 2026-07-27
survey; see ``.context/DECISIONS.md``). ``load()`` collapses those into one
``PlanInfo`` rather than showing every plan twice.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from ._bpilot_path import ensure_bpilot_on_path

ensure_bpilot_on_path()

from B_PILOT import config as bpilot_config  # noqa: E402
from B_PILOT import paths as bpilot_paths  # noqa: E402
from B_PILOT import plan_parser as bpilot_plan_parser  # noqa: E402

Tier = Literal["vetted", "extended"]

# Hand-maintained, like plan_context.TEMPLATES / plan_parser.SKELETON_SHAPES --
# explicit files, not recursive auto-discovery, so a stray one-off script
# never silently appears in what the model tells users about. plans_dir-
# relative. Update by hand if new shared plan files are added.
_EXTENDED_SHARED_FILES: tuple[str, ...] = (
    "scan_skeletons.py",
    "scans_standard.py",
    "scans_stationary.py",
    "alignment.py",
    "auxiliary_scan.py",
    "auxiliary_ad.py",
    "motor_motions.py",
    "software_triggering.py",
    "scan_hw_triggering.py",
    "scan_sw_triggering.py",
    "dm_workflows.py",
    "generic_databroker.py",
    "tiff_to_hdf.py",
)


@dataclass(frozen=True)
class PlanParam:
    """JSON-safe mirror of one B_PILOT.plan_parser.ParamSpec."""

    name: str
    dtype: str
    units: str
    short: str
    long: str
    # None whenever required=True -- ParamSpec.default is plan_parser._NODEFAULT
    # (a private sentinel object, not JSON-serializable) for required params.
    default: object | None
    required: bool
    choices: list[str] | None
    category: str | None


@dataclass(frozen=True)
class PlanInfo:
    name: str
    summary: str
    file: str  # plans_dir-relative, forward-slash (canonical path after dedup)
    files: tuple[str, ...]  # every plans_dir-relative path this plan was found under
    module: str  # dotted import path, informational only
    tier: Tier
    # False => params is () but the plan likely DOES take arguments -- never
    # report params=() as "takes no parameters" when documented is False.
    documented: bool
    params: tuple[PlanParam, ...]
    skeleton: tuple[str, bool] | None
    has_varargs: bool


def _to_plan_param(spec) -> PlanParam:
    return PlanParam(
        name=spec.name,
        dtype=spec.dtype,
        units=spec.units,
        short=spec.short,
        long=spec.long,
        default=None if spec.required else spec.default,
        required=spec.required,
        choices=list(spec.choices) if spec.choices else None,
        category=spec.category,
    )


def _parse_file(plans_dir: str, rel: str, module_root: str, tier: Tier) -> list[PlanInfo]:
    abspath = os.path.join(plans_dir, rel.replace("/", os.sep))
    module = bpilot_plan_parser.file_to_module(abspath, module_root)
    out = []
    for name, spec in bpilot_plan_parser.find_plan_specs(abspath).items():
        out.append(
            PlanInfo(
                name=name,
                summary=spec["summary"],
                file=rel,
                files=(rel,),
                module=module,
                tier=tier,
                documented=spec["documented"],
                params=tuple(_to_plan_param(p) for p in spec["params"]),
                skeleton=spec["skeleton"],
                has_varargs=spec["has_varargs"],
            )
        )
    return out


def _dedupe(plans: list[PlanInfo]) -> list[PlanInfo]:
    """Collapse exact-duplicate cross-file entries (see module docstring):
    prefer the `bpilot/`-canonical copy when a name's entries are byte-
    identical in parsed spec, otherwise keep every distinct entry untouched
    (never guess which of two genuinely different plans sharing a name is
    "the real one")."""
    by_name: dict[str, list[PlanInfo]] = {}
    for p in plans:
        by_name.setdefault(p.name, []).append(p)

    result: list[PlanInfo] = []
    for group in by_name.values():
        if len(group) == 1:
            result.append(group[0])
            continue
        keys = [(p.summary, p.params, p.documented, p.skeleton, p.has_varargs) for p in group]
        if all(k == keys[0] for k in keys[1:]):
            canonical = next((p for p in group if p.file.startswith("bpilot/")), group[0])
            all_files = tuple(sorted({f for p in group for f in p.files}))
            result.append(
                PlanInfo(
                    name=canonical.name,
                    summary=canonical.summary,
                    file=canonical.file,
                    files=all_files,
                    module=canonical.module,
                    tier=canonical.tier,
                    documented=canonical.documented,
                    params=canonical.params,
                    skeleton=canonical.skeleton,
                    has_varargs=canonical.has_varargs,
                )
            )
        else:
            result.extend(group)
    return result


def _beamline_extended_files(plans_dir: str, beamline: str) -> list[str]:
    """Every top-level (non-recursive) .py file directly under
    `<plans_dir>/<beamline>_plans/`, excluding private/`user_plans`-style
    dotted names -- mirrors the shared-files allowlist's "explicit, no
    surprises" philosophy for the one directory level that's genuinely
    per-beamline. Returns [] if that directory doesn't exist (e.g. an
    unrecognized/renamed beamline profile)."""
    subdir_name = f"{beamline}_plans"
    subdir = os.path.join(plans_dir, subdir_name)
    if not beamline or not os.path.isdir(subdir):
        return []
    files = []
    for entry in sorted(os.scandir(subdir), key=lambda e: e.name):
        if entry.is_file() and entry.name.endswith(".py") and not entry.name.startswith("_"):
            files.append(f"{subdir_name}/{entry.name}")
    return files


def load(profile: str | None = None, *, include_extended: bool = True) -> list[PlanInfo]:
    """All plans visible for `profile` (default: active profile).

    Tier "vetted" always included (the profile's own `visible_plan_files`).
    Tier "extended" included unless `include_extended=False` -- the full
    real plan catalog beyond what B-PILOT's own GUI form currently supports.
    """
    values = bpilot_config.profile_values(profile) if profile else bpilot_config.as_dict()
    plans_dir = values.get("plans_dir") or bpilot_paths.PLANS_DIR
    module_root = bpilot_paths.IMPORT_ROOT
    beamline = values.get("beamline") or ""

    vetted: list[PlanInfo] = []
    for rel in values.get("visible_plan_files") or []:
        vetted.extend(_parse_file(plans_dir, rel, module_root, "vetted"))
    vetted = _dedupe(vetted)

    if not include_extended:
        return vetted

    extended: list[PlanInfo] = []
    for rel in _EXTENDED_SHARED_FILES:
        extended.extend(_parse_file(plans_dir, rel, module_root, "extended"))
    for rel in _beamline_extended_files(plans_dir, beamline):
        extended.extend(_parse_file(plans_dir, rel, module_root, "extended"))

    # Some extended-allowlist files (e.g. scans_standard.py, scan_skeletons.py)
    # are the un-reformatted originals that vetted's bpilot/*.py copies were
    # based on -- same plan, strictly worse (undocumented) info. Vetted always
    # wins on name collision rather than surfacing both.
    vetted_names = {p.name for p in vetted}
    extended = _dedupe([p for p in extended if p.name not in vetted_names])

    return vetted + extended


def building_blocks(profile: str | None = None) -> dict:
    """The active/`profile`'s persisted plan_building_blocks (see
    scan_building_discovery.py) -- already computed and cached in the
    profile's config, so this is a plain dict read, no AST work."""
    values = bpilot_config.profile_values(profile) if profile else bpilot_config.as_dict()
    return dict(values.get("plan_building_blocks") or {})
