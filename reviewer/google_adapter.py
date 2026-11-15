"""The adapter for Google's models: Gemini 4 Hydrogen on the panel and, at a lower setting, on the gate.

Both slots answer in text, through the Interactions API. The Decisions API path below it, for Gemini Decision 1,
which answers a yes-or-no question with a probability, is kept and tested but no slot in the settings uses it.

NOT YET RUN AGAINST THE REAL API. It is written from Google's published documentation as read on 14 November 2026
(the Interactions API reference, the Decisions API guide, the token-counting guide, the safety and API-error pages)
and tested against a stand-in. Check it against the current documentation before its first real call.

The panel slot uses the Interactions API, which Google's documentation names as the one to use; generateContent is
described there as legacy.

- The answer's shape is asked for with `response_format` (a JSON schema). No tool is defined.
- Depth is set with `generation_config.thinking_level`, explicitly. No sampling setting is sent.
- `store` is false: nothing is kept on the provider's side for later retrieval.
- The input is counted with `countTokens` before the call.
- A request or an answer that a safety filter blocks comes back as an HTTP 400 with a short code. That is recorded
  as a refusal with the code, and no other model is tried.

The gate slot uses the Decisions API: one yes-or-no question about the submission, answered with a probability and
no text. The question sent is the one in the settings' prompts folder.
"""
import time
from typing import Optional

from .http import Http, error_code, is_passing
from .models import Adapter, Call, ProviderError, Transient

BASE = "https://generativelanguage.googleapis.com/v1beta"
API_REVISION = "2026-05-20"
THINKING = {"lowest": "low", "low": "low", "medium": "medium", "high": "high"}
# For Gemini 4 Hydrogen one limit covers input and output together. Gemini Decision 1 has its own context window.
TOTAL_TOKENS = {"gemini-4-hydrogen": 1_048_576}
DECISION_MODELS = ("gemini-decision-1",)
BLOCKED = ("safety", "jailbreak", "prohibited_content", "blocklist", "recitation", "language", "spii", "image_safety", "content_blocked")
QUESTION_KEY = "spam"


def whole_text(parts) -> str:
    return "\n\n".join(part.text for part in parts)


def interaction_request(slot, assembled, schema: dict) -> dict:
    """Everything sent to the Interactions API for one panel call."""
    return {
        "model": slot.model,
        "system_instruction": assembled.instructions(),
        "input": whole_text(assembled.rest()),
        "generation_config": {"thinking_level": THINKING[slot.effort], "max_output_tokens": slot.max_output_tokens},
        "response_format": {"type": "text", "mime_type": "application/json", "schema": schema},
        "store": False,
    }


def decision_request(slot, assembled, question: str) -> dict:
    """Everything sent to the Decisions API for one gate call. The answer format for text models is left out."""
    return {
        "model": slot.model,
        "input": whole_text(part for part in assembled.parts if part.name != "answer format"),
        "questions": {QUESTION_KEY: {"type": "noul", "instructions": question}},
    }


class GoogleAdapter(Adapter):
    provider = "google"

    def __init__(self, key: str, http: Optional[Http] = None):
        self._headers = {"x-goog-api-key": key, "Api-Revision": API_REVISION}
        self._http = http or Http()

    def _post(self, path: str, body: dict):
        status, answer = self._http.post(f"{BASE}/{path}", self._headers, body)
        if is_passing(status):
            raise Transient()
        return status, answer

    def call(self, slot, assembled, schema: dict, question: Optional[str] = None) -> Call:
        if slot.kind == "probability":
            return self._decide(slot, assembled, question)
        if slot.model not in TOTAL_TOKENS:
            raise ValueError("this adapter does not know that model")
        request = interaction_request(slot, assembled, schema)
        text = request["system_instruction"] + "\n\n" + request["input"]
        status, counted = self._post(f"models/{slot.model}:countTokens", {"contents": [{"parts": [{"text": text}]}]})
        if status == 400 and error_code(counted) in BLOCKED:  # a safety filter can turn the text down at the count already
            return Call("refused", refusal_category=error_code(counted))
        if status != 200:
            raise ProviderError(self.provider, status, error_code(counted))
        total = (counted or {}).get("totalTokens", (counted or {}).get("total_tokens"))
        if not isinstance(total, int):
            raise ProviderError(self.provider, status, "no_token_count")
        if total + slot.max_output_tokens > TOTAL_TOKENS[slot.model]:
            return Call("too_large")

        started = time.monotonic()
        status, answer = self._post("interactions", request)
        took = int((time.monotonic() - started) * 1000)
        if status == 400 and error_code(answer) in BLOCKED:
            return Call("refused", refusal_category=error_code(answer), latency_ms=took)
        if status != 200 or not isinstance(answer, dict):
            raise ProviderError(self.provider, status, error_code(answer))
        usage = answer.get("usage") or {}
        call = Call(
            "ok",
            reported_model=answer.get("model") if isinstance(answer.get("model"), str) else None,
            input_tokens=int(usage.get("total_input_tokens") or 0),
            cached_input_tokens=int(usage.get("total_cached_tokens") or 0),
            output_tokens=int(usage.get("total_output_tokens") or 0) + int(usage.get("total_thought_tokens") or 0),
            latency_ms=took,
        )
        state = answer.get("status")
        if state in ("incomplete", "requires_continuation"):
            call.status = "truncated"
        elif state == "completed":
            pieces = [item.get("text") for step in answer.get("steps") or [] if step.get("type") == "model_output" for item in step.get("content") or [] if item.get("type") == "text"]
            call.answer = "".join(piece for piece in pieces if isinstance(piece, str)) or None
        else:
            blocked = next((e.get("code") for e in answer.get("errors") or [] if isinstance(e, dict) and e.get("code") in BLOCKED), None)
            call.status = "refused" if blocked else "error"
            call.refusal_category = blocked
        return call

    def _decide(self, slot, assembled, question: Optional[str]) -> Call:
        if slot.model not in DECISION_MODELS or not question:
            raise ValueError("this adapter cannot ask that model a yes-or-no question")
        started = time.monotonic()
        status, answer = self._post("decisions", decision_request(slot, assembled, question))
        took = int((time.monotonic() - started) * 1000)
        if status == 400 and error_code(answer) in BLOCKED:
            return Call("refused", refusal_category=error_code(answer), latency_ms=took)
        if status != 200 or not isinstance(answer, dict):
            raise ProviderError(self.provider, status, error_code(answer))
        said = (answer.get("answers") or {}).get(QUESTION_KEY) or {}
        usage = answer.get("usage") or {}
        # This API's answer does not name the model that gave it, so there is nothing to record or compare.
        call = Call("ok", reported_model=None, input_tokens=int(usage.get("total_input_tokens") or 0), output_tokens=int(usage.get("total_output_tokens") or 0), latency_ms=took)
        if said.get("type") == "noul" and isinstance(said.get("noul"), (int, float)) and not isinstance(said.get("noul"), bool):
            call.answer = float(said["noul"])  # the probability that the answer to the question is yes
        else:
            call.status = "error"
        return call
