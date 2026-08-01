"""Argo-backed Claude client.

ANL's internal Argo gateway (``https://apps-dev.inside.anl.gov/argoapi``) is an
Anthropic-Messages-API-compatible proxy -- the same mechanism this Claude Code
session itself uses (see ``~/.claude/settings.json``: ``ANTHROPIC_BASE_URL`` +
an ``apiKeyHelper`` that just echoes the caller's ANL username; there is no
real secret). AutoPILOT reuses that exact convention rather than a hardcoded
key, so it works unmodified for any beamline staff member's own login.
"""
from __future__ import annotations

import getpass
import os

from anthropic import Anthropic, BadRequestError

DEFAULT_BASE_URL = "https://apps-dev.inside.anl.gov/argoapi"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _resolve_base_url() -> str:
    return os.environ.get("ARGO_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL") or DEFAULT_BASE_URL


def _resolve_api_key() -> str:
    return os.environ.get("ARGO_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or getpass.getuser()


def _resolve_model() -> str:
    return os.environ.get("ARGO_MODEL") or DEFAULT_MODEL


class ArgoClient:
    """Thin wrapper over the ``anthropic`` SDK, pointed at Argo instead of api.anthropic.com."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        self.base_url = base_url or _resolve_base_url()
        self.api_key = api_key or _resolve_api_key()
        self.model = model or _resolve_model()
        self._client = Anthropic(base_url=self.base_url, api_key=self.api_key)

    def smoke_test(self) -> str:
        """Bare connectivity check -- confirm Argo answers a trivial chat request."""
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with exactly one word: ok"}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")

    def call(self, system: str, messages: list[dict], tools: list[dict], temperature: float | None = None):
        """One turn of a (possibly multi-turn) conversation. Returns the raw
        Anthropic ``Message`` response (``.content`` blocks, ``.stop_reason``) --
        the caller (see `pipeline.converse`) decides what to do with whichever
        single tool, if any, the model chooses to call.

        `tool_choice` is always ``{"type": "auto", "disable_parallel_tool_use":
        True}`` -- the model may reply with plain text, or call exactly one of
        `tools` (never more than one at a time, which keeps a multi-turn loop
        simple: a turn either ends the conversation, asks a lookup question, or
        resolves to a final decision -- never several things at once).

        `system` is passed with a prompt-caching breakpoint since it carries the
        (large, static-per-session) grammar + template + device-catalog context --
        see ``.context/DECISIONS.md`` for why this is the main token-cost lever.

        `temperature` is only sent when explicitly given -- the Anthropic API's
        own default otherwise applies.

        On some models (e.g. claude-sonnet-5 via this Argo proxy), a turn can
        spend its entire token budget on an opaque extended-thinking block,
        stopping at `stop_reason == "max_tokens"` with no visible text or
        tool_use at all -- the caller would then see a blank reply. Detect
        that and retry once with a larger budget before giving up.
        """
        kwargs = dict(
            model=self.model,
            max_tokens=4096,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            tools=tools,
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
        )
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = self._create(kwargs)
        if resp.stop_reason == "max_tokens" and not any(
            block.type in ("text", "tool_use") for block in resp.content
        ):
            resp = self._create({**kwargs, "max_tokens": 8192})
        return resp

    def _create(self, kwargs: dict):
        try:
            return self._client.messages.create(**kwargs)
        except BadRequestError as exc:
            # Newer models (e.g. claude-sonnet-5) reject `temperature` outright
            # ("`temperature` is deprecated for this model", HTTP 400) rather
            # than just ignoring it -- retry once without it instead of
            # failing every request for any model that doesn't support it.
            if "temperature" in kwargs and "temperature" in str(exc).lower():
                kwargs = {k: v for k, v in kwargs.items() if k != "temperature"}
                return self._client.messages.create(**kwargs)
            raise
