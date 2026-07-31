"""Build an Anthropic tool schema from a template's ParamSpecs, and
independently re-validate whatever the model returns against those same
ParamSpecs.

Never trust the LLM's tool-call output as pre-validated just because it
matched the JSON schema -- schemas can't express "this device name must
actually exist in the active profile's catalog," so that check happens here,
by hand, against `device_catalog.DeviceCatalog`.
"""
from __future__ import annotations

from .device_catalog import DeviceCatalog
from .plan_context import Template

_JSON_TYPE = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
}


def build_tool_schema(template: Template, catalog: DeviceCatalog) -> dict:
    """A ``messages.create(tools=[...])`` schema for `template`, device fields
    restricted to names the catalog actually has."""
    properties: dict[str, dict] = {}
    required: list[str] = []

    for spec in template.param_specs:
        if spec.dtype in ("device", "device_list"):
            names = catalog.names_for(spec.category)
            item_schema = {"type": "string", "enum": names, "description": spec.long}
            prop = {"type": "array", "items": item_schema} if spec.dtype == "device_list" else item_schema
        elif spec.dtype == "choice":
            prop = {"type": "string", "enum": list(spec.choices or []), "description": spec.long}
        else:
            prop = {"type": _JSON_TYPE[spec.dtype], "description": spec.long}
        if spec.units:
            prop["description"] += f" (units: {spec.units})"
        properties[spec.name] = prop
        if spec.required:
            required.append(spec.name)

    return {
        "name": f"propose_{template.key}_plan",
        "description": f"Propose concrete parameter values for a {template.title} ({template.description})",
        "input_schema": {"type": "object", "properties": properties, "required": required},
    }


DECLINE_TOOL_NAME = "cannot_generate_plan"


def build_decline_tool_schema() -> dict:
    """A tool the model can call instead of proposing a plan, when the request
    doesn't actually describe a scan/count or is missing details it cannot
    reasonably guess -- without this, a forced single-tool call has no way to
    express "I don't know" and will fabricate a schema-valid guess instead."""
    return {
        "name": DECLINE_TOOL_NAME,
        "description": (
            "Call this instead of proposing a plan when the request does not "
            "describe a scan/count at all, or is missing details you cannot "
            "reasonably guess (e.g. it asks for something else entirely, or "
            "names a device that isn't in the available list)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "A short, user-facing explanation of why no plan was "
                        "proposed, and what info would help."
                    ),
                }
            },
            "required": ["reason"],
        },
    }


ASK_USER_TOOL_NAME = "ask_user"


def build_ask_user_tool_schema() -> dict:
    """A tool the model can call to ask a clarifying question instead of either
    guessing or flatly declining -- for requests that are close to buildable but
    missing one resolvable detail (e.g. two similarly-named devices could both
    be what was meant)."""
    return {
        "name": ASK_USER_TOOL_NAME,
        "description": (
            "Ask the user a clarifying question instead of guessing, when the "
            "request is close to something you can build but is missing a "
            "critical, non-guessable detail. Prefer this over "
            f"`{DECLINE_TOOL_NAME}` when a single question would resolve the "
            "ambiguity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "A short, specific question for the user.",
                }
            },
            "required": ["question"],
        },
    }


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate(template: Template, raw: dict, catalog: DeviceCatalog) -> dict:
    """Return a clean kwargs dict, or raise :class:`ValidationError`."""
    errors: list[str] = []
    clean: dict = {}

    for spec in template.param_specs:
        present = spec.name in raw and raw[spec.name] not in (None, "")
        if not present:
            if spec.required:
                errors.append(f"{spec.name}: required field missing")
            else:
                clean[spec.name] = spec.default
            continue

        value = raw[spec.name]
        if spec.dtype == "device":
            valid_names = catalog.names_for(spec.category)
            if value not in valid_names:
                errors.append(f"{spec.name}: {value!r} is not a known {spec.category} device")
            clean[spec.name] = value
        elif spec.dtype == "device_list":
            valid_names = catalog.names_for(spec.category)
            bad = [v for v in value if v not in valid_names]
            if bad:
                errors.append(f"{spec.name}: unknown {spec.category} device(s) {bad}")
            clean[spec.name] = list(value)
        elif spec.dtype == "choice":
            if value not in (spec.choices or []):
                errors.append(f"{spec.name}: {value!r} not in {spec.choices}")
            clean[spec.name] = value
        elif spec.dtype == "int":
            try:
                clean[spec.name] = int(value)
            except (TypeError, ValueError):
                errors.append(f"{spec.name}: {value!r} is not an int")
        elif spec.dtype == "float":
            try:
                clean[spec.name] = float(value)
            except (TypeError, ValueError):
                errors.append(f"{spec.name}: {value!r} is not a float")
        elif spec.dtype == "bool":
            clean[spec.name] = bool(value)
        else:  # str
            clean[spec.name] = str(value)

    if errors:
        raise ValidationError(errors)
    return clean
