"""The adapter for OpenAI's models: GPT-6.1 Sol on the panel, and GPT-6 Luna on the gate.

Both slots answer in text, through the Responses API. The Decisions API path below it, for a model that answers a
yes-or-no question with a probability, is kept and tested but no slot in the settings uses it.

NOT YET RUN AGAINST THE REAL API. It is written from OpenAI's published documentation as read on 14 November 2026
(the GPT-6.1 Sol model page, the Responses reference, the structured-outputs and reasoning guides, the input-token
count reference, the Decisions guide and reference) and tested against a stand-in. Check it against the current
documentation before its first real call.

The panel slot uses the Responses API.

- The answer's shape is asked for with structured outputs (`text.format`, a strict JSON schema). No tool is defined.
- Depth is set with `reasoning.effort`, explicitly. No sampling setting is sent.
- `store` is false: the response is not kept on the provider's side.
- The input is counted with the input-token count endpoint before the call.
- A refusal arrives as a "refusal" part in the answer, or as a prompt the provider declined. Both are recorded as a
  refusal, and no other model is tried.
- An answer that stopped at the output limit is recorded as cut off.

The gate slot uses the Decisions API: one "predicate" question about the submission, answered with a probability and
no text. The question sent is the one in the settings' prompts folder.
"""
import time
from typing import Optional

from .http import Http, error_code, is_passing
from .models import Adapter, Call, ProviderError, Transient

BASE = "https://api.openai.com/v1"
EFFORT = {"lowest": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh"}
MAX_INPUT_TOKENS = {"gpt-6.1-sol": 922_000, "gpt-6-luna": 922_000}
DECISION_MODELS = ("gpt-6-luna",)
DECLINED = ("invalid_prompt",)
NO_CREDIT = ("insufficient_quota",)  # arrives with status 429, like a rate limit, and is not one
PASSING = ("server_error", "rate_limit_exceeded")  # in a "failed" answer: the provider's own fault, worth another try
QUESTION_NAME = "spam"


def response_request(slot, assembled, schema: dict) -> dict:
    """Everything sent to the Responses API for one panel call."""
    return {
        "model": slot.model,
        "instructions": assembled.instructions(),
        "input": [{"role": "user", "content": [{"type": "input_text", "text": part.text} for part in assembled.rest()]}],
        "reasoning": {"effort": EFFORT[slot.effort]},
        "max_output_tokens": slot.max_output_tokens,
        "text": {"format": {"type": "json_schema", "name": "verdict", "schema": schema, "strict": True}},
        "store": False,
    }


def decision_request(slot, assembled, question: str) -> dict:
    """Everything sent to the Decisions API for one gate call. The answer format for text models is left out."""
    material = "\n\n".join(part.text for part in assembled.rest() if part.name != "answer format")
    return {
        "model": slot.model,
        "input": [{"role": "developer", "content": assembled.instructions()}, {"role": "user", "content": material}],
        "questions": [{"type": "predicate", "name": QUESTION_NAME, "instructions": question}],
    }


def _usage(answer: dict):
    usage = answer.get("usage") or {}
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens")
    thought = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")  # already counted inside output_tokens
    return int(usage.get("input_tokens") or 0), int(cached or 0), int(usage.get("output_tokens") or 0), int(thought or 0)


class OpenAIAdapter(Adapter):
    provider = "openai"

    def __init__(self, key: str, http: Optional[Http] = None):
        self._headers = {"Authorization": f"Bearer {key}"}
        self._http = http or Http()

    def _post(self, path: str, body: dict):
        status, answer = self._http.post(f"{BASE}/{path}", self._headers, body)
        if status == 429 and error_code(answer) in NO_CREDIT:
            raise ProviderError(self.provider, status, error_code(answer))  # trying again cannot help: the run stops
        if is_passing(status):
            raise Transient()
        return status, answer

    def call(self, slot, assembled, schema: dict, question: Optional[str] = None) -> Call:
        if slot.kind == "probability":
            return self._decide(slot, assembled, question)
        if slot.model not in MAX_INPUT_TOKENS:
            raise ValueError("this adapter does not know that model")
        request = response_request(slot, assembled, schema)
        status, counted = self._post("responses/input_tokens", {key: request[key] for key in ("model", "instructions", "input")})
        if status == 400 and error_code(counted) in DECLINED:  # the provider can decline the text at the count already
            return Call("refused", refusal_category=error_code(counted))
        if status != 200 or not isinstance((counted or {}).get("input_tokens"), int):
            raise ProviderError(self.provider, status, error_code(counted) or "no_token_count")
        if counted["input_tokens"] > MAX_INPUT_TOKENS[slot.model]:
            return Call("too_large")

        started = time.monotonic()
        status, answer = self._post("responses", request)
        took = int((time.monotonic() - started) * 1000)
        if status == 400 and error_code(answer) in DECLINED:
            return Call("refused", refusal_category=error_code(answer), latency_ms=took)
        if status != 200 or not isinstance(answer, dict):
            raise ProviderError(self.provider, status, error_code(answer))
        used, cached, written, thought = _usage(answer)
        call = Call("ok", reported_model=answer.get("model") if isinstance(answer.get("model"), str) else None, input_tokens=used, cached_input_tokens=cached, output_tokens=written, thinking_tokens=thought, latency_ms=took)
        state = answer.get("status")
        if state == "incomplete":
            reason = (answer.get("incomplete_details") or {}).get("reason")
            if reason == "max_output_tokens":
                call.status = "truncated"
            elif reason == "content_filter":
                call.status, call.refusal_category = "refused", "content_filter"
            else:
                call.status = "error"
        elif state == "completed":
            message = next((item for item in answer.get("output") or [] if isinstance(item, dict) and item.get("type") == "message"), None)
            content = (message or {}).get("content") or []
            if any(isinstance(part, dict) and part.get("type") == "refusal" for part in content):  # checked before any text is read
                call.status, call.refusal_category = "refused", "refusal"
            else:
                call.answer = "".join(part.get("text") or "" for part in content if isinstance(part, dict) and part.get("type") == "output_text") or None
        else:
            if error_code(answer) in PASSING:
                raise Transient()
            declined = error_code(answer) in DECLINED
            call.status = "refused" if declined else "error"
            call.refusal_category = error_code(answer) if declined else None
        return call

    def _decide(self, slot, assembled, question: Optional[str]) -> Call:
        if slot.model not in DECISION_MODELS or not question:
            raise ValueError("this adapter cannot ask that model a yes-or-no question")
        started = time.monotonic()
        status, answer = self._post("decisions", decision_request(slot, assembled, question))
        took = int((time.monotonic() - started) * 1000)
        if status == 400 and error_code(answer) in DECLINED:
            return Call("refused", refusal_category=error_code(answer), latency_ms=took)
        if status != 200 or not isinstance(answer, dict):
            raise ProviderError(self.provider, status, error_code(answer))
        used, cached, written, thought = _usage(answer)
        call = Call("ok", reported_model=answer.get("model") if isinstance(answer.get("model"), str) else None, input_tokens=used, cached_input_tokens=cached, output_tokens=written, thinking_tokens=thought, latency_ms=took)
        said = next((a for a in answer.get("answers") or [] if isinstance(a, dict) and a.get("name") == QUESTION_NAME), {})
        if said.get("type") == "refusal":
            call.status, call.refusal_category = "refused", "refusal"
        elif said.get("type") == "predicate" and isinstance(said.get("probability"), (int, float)) and not isinstance(said.get("probability"), bool):
            call.answer = float(said["probability"])  # the probability that the condition in the question is true
        else:
            call.status = "error"
        return call
