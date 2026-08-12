"""Static, hand-authored context for plan generation: the docstring grammar
B-PILOT's plan_parser expects, and a registry of drafting templates built
from the real plan files.

The docstring grammar (``GRAMMAR`` below) is deliberately hand-written, not
derived at runtime -- it changes rarely and costs real tokens every session.

The template registry (``TEMPLATES``), however, IS derived at import time
from ``gui_qt.plan_parser.find_plan_specs()`` over every plan function in
``scan_skeletons.py`` / ``scans_standard.py`` / ``scans_stationary.py`` --
the same three files, and the same parser, B-PILOT's own plan-runner form
uses. This keeps AutoPILOT's drafting scope exactly in sync with what a
human can already run through B-PILOT's GUI: add a new documented plan to
one of those files (or reformat an existing one into the grammar) and it
becomes draftable with no change here.

Each template wraps one of these real, tested plans rather than reproducing
its body -- the LLM only ever fills in the parameters the plan's own
docstring documents, and the renderer emits an ``RE(<plan>(...))`` command
that drives B-PILOT's form directly. See ``plan_renderer.py``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from ._bpilot_path import ensure_bpilot_on_path

ensure_bpilot_on_path()

from gui_qt import paths as bpilot_paths  # noqa: E402
from gui_qt.plan_parser import ParamSpec, find_plan_specs  # noqa: E402  (reused, not reinvented)

# ── Docstring grammar (verbatim summary of gui_qt/plan_parser.py's own docstring) ──

GRAMMAR = """\
Every generated plan function must document its parameters in this exact
NumPy-style grammar, so B-PILOT's docstring parser renders a working form:

    Parameters
    ----------
    <name> : <dtype>[ [<units>]]
        <short label> :: <longer description>

Rules:
* dtype is one of: str, int, float, bool, choice{a, b, ...}, positions,
  device{category}, device_list{category}, block{category}.
* block{category} names one of the active profile's plan-building-block
  lists (plan_opener, per_step, plan_closer, suspender, pseudo_suspender) --
  like device/device_list it is a real Python identifier, never a quoted
  string, and it must always be given a concrete value (never left blank).
* device{motor:whole} (category "motor" only): use this instead of plain
  device{motor} when the plan wants the bare multi-axis device object itself
  (it indexes sub-axes internally, e.g. `sms.y`) rather than a single
  resolved motor.axis. Do not use ":whole" on any other dtype/category.
* units are optional, in square brackets, e.g. [s], [mm], [deg].
* the body line is split on the FIRST ' :: ' into a short label and a
  longer tooltip -- both are required.
* default value and required/optional come from the Python signature, NOT
  the docstring: no default => required; a literal default => optional.
* device / device_list / block values are Python identifiers bound via
  imports at the top of the file (real ophyd objects or building-block
  functions), never quoted strings.
* only document parameters a human should be able to edit before running;
  everything else should be a plain positional/keyword argument to the
  wrapped real plan, left at that plan's own default.
"""

# Kept for plan_renderer.py's dormant _CALL_BODY/render() fallback path,
# which still keys off these two string constants even though the dynamic
# TEMPLATES registry below no longer populates them -- removing them would
# be a hard ImportError at module load, not a harmless dead branch.
STEP_SCAN = "step_scan"
COUNT = "count"

# The three real plan files whose documented top-level functions are all in
# scope for AutoPILOT drafting -- exactly the files B-PILOT's own plan-runner
# form already supports (see profiles/*/active_config.json's
# visible_plan_files). Update by hand if a new shared plan file joins that set.
_TEMPLATE_FILES: tuple[str, ...] = (
    "scan_skeletons.py",
    "scans_standard.py",
    "scans_stationary.py",
)


@dataclass(frozen=True)
class Template:
    key: str
    title: str
    description: str  # shown to the classifier / included in the system prompt
    module: str  # instrument/plans/<module>.py this template wraps
    function: str  # the real plan function being called
    param_specs: tuple[ParamSpec, ...]
    wrapper_name_hint: str  # slug used to name the generated function/file
    # When set, this template's param names line up with a REAL plan already
    # documented for B-PILOT's own form (see gui_qt/plan_parser.py's grammar),
    # so a validated request can drive that form directly via
    # PlanRunnerPanel.load_from_command() instead of writing a draft file --
    # see plan_renderer.render_command() / pipeline.converse(). None means
    # "not yet drivable" (no dynamically-built template leaves this unset).
    gui_plan_name: str | None = None
    gui_plan_file: str | None = None  # file to check in B-PILOT's file browser
    # (shape, relative) from gui_qt.plan_parser.SKELETON_SHAPES when this
    # plan takes its motor(s)/position(s) through a bare *args -- see
    # plan_spec.py's `axes` handling and plan_renderer.py's positional-token
    # rendering. None for every ordinary keyword-only plan.
    skeleton: tuple[str, bool] | None = None


def _build_templates() -> dict[str, Template]:
    templates: dict[str, Template] = {}
    for filename in _TEMPLATE_FILES:
        path = os.path.join(bpilot_paths.PLANS_DIR, filename)
        for name, spec in find_plan_specs(path).items():
            if not spec["documented"]:
                continue
            templates[name] = Template(
                key=name,
                title=name,
                description=spec["summary"],
                module=os.path.splitext(filename)[0],
                function=name,
                param_specs=tuple(spec["params"]),
                wrapper_name_hint=name,
                gui_plan_name=name,
                gui_plan_file=filename,
                skeleton=spec["skeleton"],
            )
    return templates


TEMPLATES: dict[str, Template] = _build_templates()
