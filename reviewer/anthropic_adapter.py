"""The adapter for Claude models: the panel's Claude slot and the gate's.

NOT YET RUN AGAINST THE REAL API. It is written from the published API guidance as it stood on 14 November 2026 and
tested against a stand-in client. Check it against the current guidance before its first real call.

What it does, and why (docs/SPEC.md, section 6):

- The model ID is sent exactly as the settings give it, and the model the response reports is recorded.
- The answer's shape is asked for with structured outputs (`output_config.format`). No tool is defined, so there is
  nothing to force and no web access.
- The thinking setting is left out. Depth is set with `output_config.effort`, explicitly.
- No sampling setting is sent, and no fallback model: a refusal is recorded as a refusal, never answered by another
  model. The ordinary (non-beta) Messages endpoint is used, which has no fallback setting at all.
- The request is streamed and the final message read, because a large output limit can outlast a plain request.
- `stop_reason` is checked for a refusal before any content is read.
- The input is counted by the provider before the call, so an input that cannot fit is caught without a model call.
- The parts that never change between proposals are marked for the prompt cache.
"""
import copy
import time
from typing import Optional

from .models import Adapter, Call, ProviderError, Transient

EFFORT = {"lowest": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh"}
CONTEXT_TOKENS = {"claude-sonnet-5-5": 1_000_000, "claude-haiku-5-5": 1_000_000}


def schema_for_claude(schema: dict) -> dict:
    """The answer schema in the form Claude's structured outputs accept: "anyOf" where a type is a list of types."""
    def convert(node):
        if isinstance(node, list):
            return [convert(item) for item in node]
        if not isinstance(node, dict):
            return node
        node = {key: convert(value) for key, value in node.items()}
        if isinstance(node.get("type"), list):
            kinds = node.pop("type")
            return {"anyOf": [dict(node, type=kind) if kind != "null" else {"type": "null"} for kind in kinds]}
        return node

    return convert(copy.deepcopy(schema))


def request_for(slot, assembled, schema: dict) -> dict:
    """Everything sent in one request. Nothing is added to it anywhere else."""
    parts = assembled.rest()
    stable = [i for i, part in enumerate(parts) if part.stable]
    content = []
    for i, part in enumerate(parts):
        block = {"type": "text", "text": part.text}
        if stable and i == stable[-1]:
            block["cache_control"] = {"type": "ephemeral"}  # everything up to here is the same for the next proposal
        content.append(block)
    return {
        "model": slot.model,
        "max_tokens": slot.max_output_tokens,
        "system": [{"type": "text", "text": assembled.instructions()}],
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": EFFORT[slot.effort], "format": {"type": "json_schema", "schema": schema_for_claude(schema)}},
    }


def _is_passing(error: Exception) -> bool:
    """A fault worth trying again: the provider was busy, down or unreachable. Anything else is a real error."""
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    return type(error).__name__ in ("APIConnectionError", "APITimeoutError")


def _short_code(error: Exception) -> Optional[str]:
    """The provider's short word for an error, when the SDK's error carries one. Never its message.

    One fixed phrase is looked for in the message and nothing of the message is kept: no credit arrives with status
    400 and the general word for a bad request, and it has to stop the run, not be taken for one bad request.
    """
    body = getattr(error, "body", None)
    inner = body.get("error") if isinstance(body, dict) else None
    if not isinstance(inner, dict):
        return None
    message = inner.get("message")
    if isinstance(message, str) and "credit balance is too low" in message.lower():
        return "credit_balance_too_low"
    kind = inner.get("type")
    return kind if isinstance(kind, str) else None


class ClaudeAdapter(Adapter):
    provider = "anthropic"

    def __init__(self, client):
        """`client` is an `anthropic.Anthropic` client. It is made by the caller, so this module never sees a key."""
        self._client = client

    def call(self, slot, assembled, schema: dict, question: Optional[str] = None) -> Call:
        if slot.model not in CONTEXT_TOKENS:
            raise ValueError("this adapter does not know that model")
        request = request_for(slot, assembled, schema)
        try:
            counted = self._client.messages.count_tokens(model=request["model"], system=request["system"], messages=request["messages"]).input_tokens
            if counted + slot.max_output_tokens > CONTEXT_TOKENS[slot.model]:
                return Call("too_large")
            started = time.monotonic()
            with self._client.messages.stream(**request) as stream:
                message = stream.get_final_message()
            took = int((time.monotonic() - started) * 1000)
        except Exception as error:
            if _is_passing(error):
                raise Transient() from None
            status = getattr(error, "status_code", None)
            if isinstance(status, int):  # the provider turned the request down: its status and short word are kept, never its text
                raise ProviderError(self.provider, status, _short_code(error)) from None
            raise

        usage = message.usage
        from_cache = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        to_cache = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        call = Call(
            "ok",
            reported_model=message.model,
            input_tokens=int(usage.input_tokens or 0) + from_cache + to_cache,
            cached_input_tokens=from_cache,
            output_tokens=int(usage.output_tokens or 0),
            latency_ms=took,
        )
        if message.stop_reason == "refusal":  # checked before any content is read
            call.status = "refused"
            call.refusal_category = getattr(getattr(message, "stop_details", None), "category", None)
        elif message.stop_reason == "max_tokens":
            call.status = "truncated"
        elif message.stop_reason in ("end_turn", "stop_sequence"):
            call.answer = next((block.text for block in message.content if getattr(block, "type", None) == "text"), None)
        else:
            call.status = "error"
        return call
