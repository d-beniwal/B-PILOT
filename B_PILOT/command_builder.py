"""Compose the two-line ``from ... import ...`` + ``RE(plan(...))`` command
string queued/run by :class:`B_PILOT.plan_runner.PlanRunnerPanel`,
:class:`B_PILOT.switchto_popup.SwitchToPopup`, and
:class:`B_PILOT.contacq_popup.ContAcqPopup`.

Extracted out of `PlanRunnerPanel` so both panels build commands the same
way; `plan_name`/`module`/`params` are passed explicitly instead of being
read off `self`.
"""
from __future__ import annotations

from .plan_parser import ParamSpec
from .plan_parser import RawCode


def make_import_line(plan_name: str, module: str) -> str:
    return f"from {module} import {plan_name}"


def make_re_line(
    plan_name: str,
    params: list[ParamSpec],
    values: dict,
    notes: str = "",
) -> str:
    if "__args__" in values:
        inner = f"{plan_name}({values['__args__']})"
    else:
        values = dict(values)
        # Leading positional args (scan_skeletons.py's *args, from
        # MotorRowsWidget.tokens()) must come first — Python syntax requires
        # positional args before keyword args.
        args = list(values.pop("__positional__", []))
        for spec in params:
            if spec.name not in values:
                continue
            val = values[spec.name]
            # RawCode (device/block refs) emit verbatim; everything else via repr().
            rendered = str(val) if isinstance(val, RawCode) else repr(val)
            args.append(f"{spec.name}={rendered}")
        inner = f"{plan_name}({', '.join(args)})"
    if notes:
        # Lands in the run's start document (cat[uid].metadata["start"]["notes"]).
        return f"RE({inner}, md={{'notes': {notes!r}}})"
    return f"RE({inner})"
