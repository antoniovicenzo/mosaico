"""Claude API access with cost accounting and a hard daily budget."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from .config import Config
from .db import DB, now, today

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# USD per million tokens: (input, output, cache_read). Unknown models fall back to Opus pricing.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
WEB_SEARCH_USD = 0.01  # $10 per 1,000 searches

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}


class BudgetExceeded(RuntimeError):
    pass


class ItemError(Exception):
    """A failure specific to one input (refusal, unusable output) — retrying it as-is won't help."""


class Refused(ItemError):
    pass


def is_item_error(e: Exception) -> bool:
    """True when the failure belongs to the item being processed, not to the system.

    Auth, network, rate-limit and server errors return False so the item stays queued
    and is retried on a later tick once the problem is fixed.
    """
    return isinstance(e, (ItemError, ValidationError, anthropic.BadRequestError))


def price_call(model: str, usage) -> float:
    inp, out, cread = PRICES.get(model, PRICES["claude-opus-5-5"])
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    searches = 0
    stu = getattr(usage, "server_tool_use", None)
    if stu is not None:
        searches = getattr(stu, "web_search_requests", 0) or 0
    cost = (
        usage.input_tokens * inp
        + usage.output_tokens * out
        + cache_read * cread
        + cache_write * inp * 1.25
    ) / 1_000_000
    return cost + searches * WEB_SEARCH_USD


@dataclass
class ResearchResult:
    text: str
    sources: list[dict[str, str]]


class LLM(Protocol):
    def structured(self, purpose: str, role: str, system: str, prompt: str, schema: type[T], max_tokens: int = 8000) -> T: ...

    def research(self, purpose: str, system: str, prompt: str, max_searches: int = 8) -> ResearchResult: ...

    def remaining_budget(self) -> float: ...


class ClaudeLLM:
    """Thin wrapper over the Anthropic SDK. `role` selects a model from config.models."""

    def __init__(self, cfg: Config, db: DB, client: anthropic.Anthropic | None = None):
        self.cfg = cfg
        self.db = db
        self.client = client or anthropic.Anthropic(max_retries=4)
        self._fallbacks_ok = cfg.models.server_fallbacks

    # -- budget -----------------------------------------------------------------
    def spent_today(self) -> float:
        return float(self.db.scalar("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE day = ?", (today(),)))

    def remaining_budget(self) -> float:
        return self.cfg.budget.daily_usd - self.spent_today()

    def _guard(self, purpose: str) -> None:
        remaining = self.remaining_budget()
        if remaining < min(self.cfg.budget.max_call_usd, 0.05) or remaining <= 0:
            raise BudgetExceeded(f"Daily budget ${self.cfg.budget.daily_usd:.2f} reached; skipping '{purpose}'")

    def _record(self, purpose: str, model: str, usage) -> float:
        cost = price_call(model, usage)
        stu = getattr(usage, "server_tool_use", None)
        self.db.insert("llm_calls", {
            "ts": now(),
            "day": today(),
            "purpose": purpose,
            "model": model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "web_searches": (getattr(stu, "web_search_requests", 0) or 0) if stu else 0,
            "cost_usd": cost,
        })
        return cost

    def _model(self, role: str) -> str:
        return getattr(self.cfg.models, role)

    def _kwargs(self, model: str, effort: str | None) -> dict:
        kw: dict = {}
        if effort:
            kw["output_config"] = {"effort": effort}
        if self._fallbacks_ok and model in FALLBACK_MODELS:
            kw["betas"] = [FALLBACK_BETA]
            kw["fallbacks"] = "default"
        return kw

    def _call(self, fn, purpose: str, model: str, effort: str | None, **params):
        """Run a beta messages call, dropping server fallbacks if the platform rejects them."""
        self._guard(purpose)
        kw = self._kwargs(model, effort)
        if "output_config" in params and "output_config" in kw:
            params["output_config"] = {**params["output_config"], **kw.pop("output_config")}
        try:
            resp = fn(model=model, **kw, **params)
        except anthropic.BadRequestError as e:
            if "betas" in kw and "fallback" in str(e).lower():
                log.warning("Server-side fallbacks unavailable; disabling them")
                self._fallbacks_ok = False
                kw = self._kwargs(model, effort)
                if "output_config" in params and "output_config" in kw:
                    params["output_config"] = {**params["output_config"], **kw.pop("output_config")}
                resp = fn(model=model, **kw, **params)
            else:
                raise
        self._record(purpose, getattr(resp, "model", model) or model, resp.usage)
        if resp.stop_reason == "refusal":
            raise Refused(f"Model declined '{purpose}'")
        return resp

    # -- public API -------------------------------------------------------------
    def structured(self, purpose: str, role: str, system: str, prompt: str, schema: type[T], max_tokens: int = 8000) -> T:
        model = self._model(role)
        effort = "low" if role == "extract" else "medium"
        resp = self._call(
            self.client.beta.messages.parse,
            purpose,
            model,
            effort,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
        if resp.stop_reason == "max_tokens":
            raise ItemError(f"Output truncated for '{purpose}' (raise max_tokens)")
        parsed = resp.parsed_output
        if parsed is None:
            raise ItemError(f"No structured output for '{purpose}'")
        return parsed

    def research(self, purpose: str, system: str, prompt: str, max_searches: int = 8) -> ResearchResult:
        model = self._model("research")
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]
        messages: list[dict] = [{"role": "user", "content": prompt}]
        texts: list[str] = []
        sources: dict[str, str] = {}
        def run_stream(**params):
            with self.client.beta.messages.stream(**params) as stream:
                return stream.get_final_message()

        for _ in range(4):  # resume server-side loops that hit pause_turn
            resp = self._call(
                run_stream,
                purpose,
                model,
                self.cfg.models.research_effort,
                max_tokens=32000,
                system=system,
                messages=messages,
                tools=tools,
            )
            for block in resp.content:
                if block.type == "text":
                    texts.append(block.text)
                    for c in getattr(block, "citations", None) or []:
                        url = getattr(c, "url", None)
                        if url:
                            sources[url] = getattr(c, "title", "") or url
                elif block.type == "web_search_tool_result" and isinstance(block.content, list):
                    for r in block.content:
                        url = getattr(r, "url", None)
                        if url and url not in sources:
                            sources[url] = getattr(r, "title", "") or url
            if resp.stop_reason != "pause_turn":
                break
            messages = [messages[0], {"role": "assistant", "content": resp.content}]
        return ResearchResult(
            text="".join(texts).strip(),
            sources=[{"url": u, "title": t} for u, t in sources.items()],
        )
