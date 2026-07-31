"""Static, hand-authored context for plan generation: the docstring grammar
B-PILOT's plan_parser expects, and a small registry of scan-type templates.

Deliberately NOT derived at runtime from ``plan_parser.py``'s own comments or
from reading ``instrument/plans/*.py`` on every call -- that would cost real
tokens (and wall-clock) every session for text that changes rarely. Written
once here from the 2026-07-23 codebase survey recorded in
``.context/DECISIONS.md``; update by hand if the grammar or skeleton plans
change.

Each template wraps one of the real, tested generic plans in
``instrument/plans/`` (``mpe_step_scan``, ``mpe_count``) rather than
reproducing their (complex, internal-helper-heavy) bodies -- the LLM only
ever fills in the handful of parameters a human would actually vary, and the
renderer emits a small function that calls straight through to the real
plan. See ``plan_renderer.py``.
"""
from __future__ import annotations

from dataclasses import dataclass

from ._bpilot_path import ensure_bpilot_on_path

ensure_bpilot_on_path()

from gui_qt.plan_parser import ParamSpec  # noqa: E402  (reused, not reinvented)

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
  device{category}, device_list{category}.
* units are optional, in square brackets, e.g. [s], [mm], [deg].
* the body line is split on the FIRST ' :: ' into a short label and a
  longer tooltip -- both are required.
* default value and required/optional come from the Python signature, NOT
  the docstring: no default => required; a literal default => optional.
* device / device_list values are Python identifiers bound via imports at
  the top of the file (real ophyd objects), never quoted strings.
* only document parameters a human should be able to edit before running;
  everything else should be a plain positional/keyword argument to the
  wrapped real plan, left at that plan's own default.
"""

STEP_SCAN = "step_scan"
COUNT = "count"


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
    # "not yet drivable" (e.g. STEP_SCAN's real plan, mpe_step_scan, doesn't
    # follow the grammar for its extra params yet -- a separately tracked gap).
    gui_plan_name: str | None = None
    gui_plan_file: str | None = None  # file to check in B-PILOT's file browser


_STEP_SCAN_SPECS = (
    ParamSpec("motor", "device", "", "motor", "Motor to move through the scan.",
              None, True, None, False, "motor"),
    ParamSpec("start", "float", "", "start", "Starting position for the motor.",
              None, True, None, False, None),
    ParamSpec("stop", "float", "", "stop", "Ending position for the motor.",
              None, True, None, False, None),
    ParamSpec("nsteps", "int", "", "n steps", "Number of steps in the scan.",
              None, True, None, False, None),
    ParamSpec("det", "device", "", "detector",
              "Area detector to use for acquisition. Omit to use the plan's own default.",
              None, False, None, True, "area_detector"),
    ParamSpec("exposure_time", "float", "s", "exposure", "Time per exposure.",
              1.0, False, None, False, None),
    ParamSpec("scalers", "device_list", "", "scalers",
              "Scaler devices to record alongside the detector. May be empty.",
              [], False, None, False, "scaler"),
)

_COUNT_SPECS = (
    ParamSpec("scalers", "device_list", "", "scalers",
              "Scaler devices whose channels are recorded in the run.",
              None, True, None, False, "scaler"),
    ParamSpec("nframes", "int", "", "# readings", "Number of readings collected.",
              1, False, None, False, None),
    ParamSpec("exposure_time", "float", "s", "exposure", "Time per exposure.",
              1.0, False, None, False, None),
)

TEMPLATES: dict[str, Template] = {
    STEP_SCAN: Template(
        key=STEP_SCAN,
        title="Step scan",
        description=(
            "A single-motor step-and-shoot scan: move a motor through a range "
            "of positions, acquiring at each point. Use for requests like "
            "'scan motor X from A to B in N steps' or '...counting for T seconds "
            "at each point'."
        ),
        module="scan_skeletons",
        function="mpe_step_scan",
        param_specs=_STEP_SCAN_SPECS,
        wrapper_name_hint="step_scan",
    ),
    COUNT: Template(
        key=COUNT,
        title="Count",
        description=(
            "Count one or more scaler devices at a single, stationary position -- "
            "no motor motion, no area detector. Use for requests like "
            "'count the scintillator for T seconds' or 'take N readings from "
            "scaler X'."
        ),
        module="scans_stationary",
        function="mpe_count",
        param_specs=_COUNT_SPECS,
        wrapper_name_hint="count",
        gui_plan_name="mpe_count",
        gui_plan_file="scans_stationary_gui_testing.py",
    ),
}
