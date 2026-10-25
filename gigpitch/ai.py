"""Claude API calls, with every call's cost recorded and a monthly spending cap enforced before each call."""

import json
import os
from dataclasses import dataclass

import anthropic

from .store import Store

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000
MAX_CONTINUATIONS = 5

# USD per million tokens for MODEL, and per web search. Check these against
# Anthropic's current pricing page when you change MODEL.
PRICE_INPUT = 4.00
PRICE_OUTPUT = 20.00
PRICE_CACHE_READ = 0.20
PRICE_CACHE_WRITE = 5.00
PRICE_WEB_SEARCH = 0.01

# Caps in USD per calendar month. Override with environment variables.
BAND_MONTHLY_CAP = float(os.environ.get("GIGPITCH_BAND_MONTHLY_CAP_USD", "5"))
GLOBAL_MONTHLY_CAP = float(os.environ.get("GIGPITCH_GLOBAL_MONTHLY_CAP_USD", "25"))
# Headroom kept back so one more call can't push spend far past a cap.
CALL_RESERVE = float(os.environ.get("GIGPITCH_CALL_RESERVE_USD", "0.50"))


class CapReached(Exception):
    pass


class Refused(Exception):
    pass


@dataclass
class Reply:
    text: str
    data: dict | None
    cost_usd: float


def cost_of(usage) -> float:
    searches = usage.server_tool_use.web_search_requests if usage.server_tool_use else 0
    return (
        usage.input_tokens * PRICE_INPUT
        + usage.output_tokens * PRICE_OUTPUT
        + (usage.cache_read_input_tokens or 0) * PRICE_CACHE_READ
        + (usage.cache_creation_input_tokens or 0) * PRICE_CACHE_WRITE
    ) / 1_000_000 + searches * PRICE_WEB_SEARCH


class Claude:
    def __init__(self, store: Store, client: anthropic.Anthropic | None = None):
        self.store = store
        self.client = client or anthropic.Anthropic()

    def check_cap(self, band_id: int) -> None:
        band_spend = self.store.spend_this_month(band_id)
        if band_spend + CALL_RESERVE > BAND_MONTHLY_CAP:
            raise CapReached(f"Band has spent ${band_spend:.2f} of its ${BAND_MONTHLY_CAP:.2f} monthly cap")
        total = self.store.spend_this_month()
        if total + CALL_RESERVE > GLOBAL_MONTHLY_CAP:
            raise CapReached(f"All bands have spent ${total:.2f} of the ${GLOBAL_MONTHLY_CAP:.2f} monthly cap")

    def call(self, band_id: int, purpose: str, system: str, prompt: str, *,
             web_search: int = 0, schema: dict | None = None, effort: str = "medium") -> Reply:
        """One request. `web_search` is the most searches allowed; `schema` asks for JSON back."""
        self.check_cap(band_id)
        output_config: dict = {"effort": effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": web_search}] if web_search else []

        messages: list = [{"role": "user", "content": prompt}]
        total_cost = 0.0
        for _ in range(MAX_CONTINUATIONS):
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                system=system,
                messages=messages,
                output_config=output_config,
                **({"tools": tools} if tools else {}),
            )
            cost = cost_of(response.usage)
            total_cost += cost
            u = response.usage
            self.store.record_usage(
                band_id, purpose, response.model, u.input_tokens, u.output_tokens,
                u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0,
                u.server_tool_use.web_search_requests if u.server_tool_use else 0, cost,
            )
            if response.stop_reason != "pause_turn":
                break
            # The server paused a long web search; send the turn back so it resumes.
            messages = [messages[0], {"role": "assistant", "content": response.content}]

        if response.stop_reason == "refusal":
            raise Refused(f"{purpose}: the model declined this request")
        text = "".join(b.text for b in response.content if b.type == "text")
        data = json.loads(text) if schema else None
        return Reply(text=text, data=data, cost_usd=total_cost)
