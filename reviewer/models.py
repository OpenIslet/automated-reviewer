"""How the trial talks to a model: one small interface, and a stand-in that makes no call.

A real adapter (one per provider, written in a later step) turns an assembled input into one request and returns a
Call. It must never send a fallback-model setting, never retry on another model, and never use tools or web access
(docs/SPEC.md, section 6).

The stand-in here answers from a script held in memory. It is what the dry run and the tests use.
"""
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


class Transient(Exception):
    """The provider could not be reached or answered with a passing fault. Worth trying again; never recorded."""


class ProviderError(Exception):
    """A provider turned a request down for a reason that trying again will not fix.

    A refused key, a refused permission, no credit, or a model or address the provider does not have stops the run,
    because every later request would fail the same way and a person has to put it right. Anything else, such as one
    request the provider will not accept, is noted against that model for that proposal and the run goes on. If the
    same model then turns down a second proposal in the same run, that stops the run too (`repeated`).

    The message holds the provider's name, the status and the provider's short code, and nothing else.
    """

    # None of these is caused by what one proposal says, and each fails every later request the same way.
    STATUSES_THAT_STOP_THE_RUN = (401, 402, 403, 404)  # 404: a model or an address the provider does not have
    CODES_THAT_STOP_THE_RUN = (
        "insufficient_quota",  # OpenAI: no credit. It arrives with status 429, like a rate limit.
        "credit_balance_too_low",  # Anthropic: no credit. It arrives with status 400. The word is this project's own.
        "API_KEY_INVALID",  # Google's older endpoints: a refused key. It arrives with status 400.
        "BILLING_DISABLED",
        "FAILED_PRECONDITION",  # Google: a prerequisite such as billing is not met. It arrives with status 400.
        "failed_precondition",
    )
    repeated = False  # set on the fault that stops a run because the same model turned down two proposals in it

    def __init__(self, provider: str, status: int, code=None):
        code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", code) else None
        super().__init__(f"{provider}: status {status}" + (f", {code}" if code else ""))
        self.provider, self.status, self.code = provider, status, code

    @property
    def stops_the_run(self) -> bool:
        return self.status in self.STATUSES_THAT_STOP_THE_RUN or self.code in self.CODES_THAT_STOP_THE_RUN

    @property
    def short(self) -> str:
        """The status and the short code as one word, for the record."""
        return f"{self.status}" + (f":{self.code}" if self.code else "")


@dataclass
class Call:
    """What came back from one request."""

    status: str  # "ok" | "truncated" | "refused" | "too_large" | "error" | "rejected" (the provider would not take the request)
    answer: object = None  # for "ok": the answer as the provider gave it (an object, JSON text, or a probability)
    reported_model: Optional[str] = None  # the model the provider says answered
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    refusal_category: Optional[str] = None
    error_code: Optional[str] = None  # for "rejected": the provider's status and short code
    fault: Optional[Exception] = None  # for "rejected": the refusal itself, in case it has to stop the run


class Adapter:
    """One provider. `call` makes exactly one request for one slot."""

    provider = ""

    def call(self, slot, assembled, schema: dict, question: Optional[str] = None) -> Call:
        raise NotImplementedError


class FakeAdapter(Adapter):
    """Answers from a script, makes no request, and keeps a list of what it was asked.

    `answers` maps a slot name to a list of things to return in turn: a Call, an answer for an "ok" Call, or an
    exception to raise. When a slot's list runs out, `default` is called with the slot.
    """

    def __init__(self, provider: str, answers: Optional[Dict[str, List[object]]] = None, default: Optional[Callable] = None):
        self.provider = provider
        self.answers = {slot: list(items) for slot, items in (answers or {}).items()}
        self.default = default or default_answer
        self.asked: List[str] = []

    def call(self, slot, assembled, schema: dict, question: Optional[str] = None) -> Call:
        self.asked.append(slot.slot)
        queue = self.answers.get(slot.slot)
        item = queue.pop(0) if queue else self.default(slot)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, Call):
            call = item
        else:
            call = Call("ok", item)
        if call.reported_model is None:
            call.reported_model = slot.model
        if not call.input_tokens:
            call.input_tokens = assembled.tokens
        if not call.output_tokens and call.status == "ok":
            call.output_tokens = 50
        return call


def default_answer(slot):
    """What the stand-in says when nothing was scripted: not spam, and a fixed outcome that means nothing."""
    if slot.stage == "gate":
        return 0.02 if slot.kind == "probability" else {"spam": False, "probability": 0.02, "reason": "Stand-in answer. No model was called."}
    return {"outcome": "not_taken_up", "confidence": 0.5, "covered_by": None, "reasons": ["Stand-in answer. No model was called."]}


def fake_adapters(providers, answers: Optional[Dict[str, List[object]]] = None) -> Dict[str, FakeAdapter]:
    return {provider: FakeAdapter(provider, answers) for provider in providers}
