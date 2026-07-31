"""Read-only lookup tools available to the multi-turn agent loop in `pipeline.py`,
alongside the propose/decline/ask_user tools in `plan_spec.py`.

Both wrap data AutoPILOT already loads per request (the active profile's
`DeviceCatalog`, `plan_context.TEMPLATES`) -- no new `gui_qt` imports, no
network calls, nothing that could reach hardware. These exist so the model
can double-check a device name or discover what plan types actually exist
instead of confidently guessing when it isn't sure.
"""
from __future__ import annotations

from .device_catalog import DeviceCatalog
from .plan_catalog import PlanInfo
from .plan_context import TEMPLATES

LIST_DEVICES_TOOL_NAME = "list_devices"
LIST_PLANS_TOOL_NAME = "list_plans"
LIST_ALL_PLANS_TOOL_NAME = "list_all_plans"
DESCRIBE_PLAN_TOOL_NAME = "describe_plan"
LIST_SCAN_BUILDING_BLOCKS_TOOL_NAME = "list_scan_building_blocks"


def build_list_devices_schema() -> dict:
    return {
        "name": LIST_DEVICES_TOOL_NAME,
        "description": (
            "List devices available on the active beamline profile, optionally "
            "filtered by category (e.g. 'motor', 'area_detector', 'scaler'). Use "
            "this to check whether a device name you were given actually exists "
            "before guessing a substitute."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional device category to filter by. Omit to list all categories.",
                }
            },
            "required": [],
        },
    }


def build_list_plans_schema() -> dict:
    return {
        "name": LIST_PLANS_TOOL_NAME,
        "description": "List the scan/count plan types AutoPILOT currently knows how to generate.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }


def build_list_all_plans_schema() -> dict:
    return {
        "name": LIST_ALL_PLANS_TOOL_NAME,
        "description": (
            "List every real plan in this beamline's instrument.plans catalog "
            "(not just the ones AutoPILOT can draft) -- includes tomography, "
            "alignment, and grid-scan plans. Use this to find the right plan by "
            "name for a request, then call describe_plan for its parameters. "
            "Compact summaries only; most extended-tier plans have "
            "'documented': false, meaning their parameter list is incomplete, "
            "NOT that they take no parameters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tier": {
                    "type": "string",
                    "enum": ["vetted", "extended", "all"],
                    "description": (
                        "'vetted' = fully parameter-documented plans shown in the "
                        "B-PILOT GUI. 'extended' = the broader real plan catalog "
                        "(often undocumented parameters). Default 'all'."
                    ),
                }
            },
            "required": [],
        },
    }


def build_describe_plan_schema() -> dict:
    return {
        "name": DESCRIBE_PLAN_TOOL_NAME,
        "description": (
            "Get full parameter detail for a specific plan by name from the real "
            "instrument.plans catalog (from list_all_plans). Use this before "
            "answering any question about a plan's arguments, defaults, or units."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact plan (function) name, e.g. 'mpe_count' or 'tomoscan_sw'.",
                }
            },
            "required": ["name"],
        },
    }


def build_list_scan_building_blocks_schema() -> dict:
    return {
        "name": LIST_SCAN_BUILDING_BLOCKS_TOOL_NAME,
        "description": (
            "List the named plan_opener/per_step/plan_closer/suspender/"
            "pseudo_suspender building blocks available on this beamline for "
            "scan_skeletons.py-style plans (e.g. mpe_step_scan's plan_opener= "
            "argument). Use this to answer questions about what building blocks "
            "exist or to check a name before assuming it's valid."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }


def known_categories() -> list[str]:
    return sorted(
        {spec.category for template in TEMPLATES.values() for spec in template.param_specs if spec.category}
    )


def list_devices(catalog: DeviceCatalog, category: str | None) -> dict:
    if category:
        return {"category": category, "devices": catalog.names_for(category)}
    return {cat: catalog.names_for(cat) for cat in known_categories()}


def list_plans() -> dict:
    return {
        key: {
            "title": template.title,
            "description": template.description,
            "parameters": [
                {
                    "name": spec.name,
                    "dtype": spec.dtype,
                    "category": spec.category,
                    "required": spec.required,
                    "description": spec.long,
                }
                for spec in template.param_specs
            ],
        }
        for key, template in TEMPLATES.items()
    }


def _plan_summary(plan: PlanInfo) -> dict:
    return {
        "name": plan.name,
        "file": plan.file,
        "tier": plan.tier,
        "summary": plan.summary,
        "documented": plan.documented,
        "required_params": (
            [p.name for p in plan.params if p.required] if plan.documented else None
        ),
    }


def list_all_plans(plan_catalog: list[PlanInfo], tier: str | None) -> dict:
    tier = tier or "all"
    plans = plan_catalog if tier == "all" else [p for p in plan_catalog if p.tier == tier]
    return {"plans": [_plan_summary(p) for p in plans]}


def _plan_param_detail(param) -> dict:
    return {
        "name": param.name,
        "dtype": param.dtype,
        "units": param.units,
        "short": param.short,
        "long": param.long,
        "default": param.default,
        "required": param.required,
        "choices": param.choices,
        "category": param.category,
    }


def describe_plan(plan_catalog: list[PlanInfo], name: str) -> dict:
    matches = [p for p in plan_catalog if p.name == name]
    if not matches:
        return {"found": False, "name": name}
    return {
        "found": True,
        "matches": [
            {
                "name": p.name,
                "summary": p.summary,
                "file": p.file,
                "files": list(p.files),
                "module": p.module,
                "tier": p.tier,
                "documented": p.documented,
                "params": [_plan_param_detail(param) for param in p.params],
                "skeleton": list(p.skeleton) if p.skeleton else None,
                "has_varargs": p.has_varargs,
            }
            for p in matches
        ],
    }


def list_scan_building_blocks(blocks: dict) -> dict:
    return blocks
