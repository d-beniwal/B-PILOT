"""Builds the backend-agnostic staging inputs for a human-gated queue
proposal (`B_PILOT.agent_proposals`) from an already-validated
`plan_spec.py` `clean` kwargs dict -- the only AutoPILOT-side code that
knows how to shape a queue item. `B_PILOT.queue_store`/`B_PILOT.qs_client`
are never imported here or anywhere else in AutoPILOT: staging a proposal
writes only to the new, separate `agent_proposals.json` file, never to
either backend's own queue state. See `agent_proposals.py`'s module
docstring for the full contract this preserves.
"""
from __future__ import annotations

from ._bpilot_path import ensure_bpilot_on_path
from .plan_context import Template
from .plan_renderer import axes_tokens, default_code

ensure_bpilot_on_path()

from B_PILOT import command_builder  # noqa: E402
from B_PILOT.plan_parser import RawCode  # noqa: E402


def area_detectors_for(template: Template, clean: dict) -> list[str]:
    """Device names bound to an area_detector-category field -- same
    information `B_PILOT.main_window._sender_area_detectors()` gets from a
    human-driven panel, needed here so an approved native-backend proposal
    can still trigger the MIDAS_GUI live-view bridge the same way."""
    names: list[str] = []
    for spec in template.param_specs:
        if spec.category != "area_detector":
            continue
        value = clean.get(spec.name)
        if spec.dtype == "device" and value:
            names.append(value)
        elif spec.dtype == "device_list":
            names.extend(value or [])
    return sorted(set(names))


def build_qs_item(template: Template, clean: dict) -> dict | None:
    """The same `{item_type, name, args, kwargs}` shape
    `command_builder.make_queue_item` builds from the GUI's own form values,
    built here from AutoPILOT's validated `clean` dict instead. Returns
    `None` for anything `make_queue_item` itself can't represent (mirrors
    its own contract) -- callers should treat that like the hand-edited-text
    case: queueing via QS isn't available for it.
    """
    values: dict = {}
    if template.skeleton:
        values["__positional__"] = axes_tokens(template, clean)
    for spec in template.param_specs:
        if spec.name not in clean:
            continue
        value = clean[spec.name]
        if spec.dtype in ("device", "device_list", "block"):
            values[spec.name] = RawCode(default_code(spec, value))
        else:
            values[spec.name] = value
    return command_builder.make_queue_item(template.gui_plan_name, list(template.param_specs), values)
