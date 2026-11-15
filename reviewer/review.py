"""Asks the models about one prepared proposal and writes down what each said. This is the only code that calls a model.

For each model slot, in the order of the settings:

- A slot that already has an answer, or a recorded reason for having none, is left alone. So a later run fills only
  what is missing and never repeats a call that succeeded.
- An input larger than the slot's limit makes no call: `no_verdict: too_large`.
- A passing fault is tried up to three times within the run and not recorded. If the slot still has no answer it
  stays pending for the next run; after the number of runs in the settings it becomes `no_verdict: error`.
- An answer in the wrong shape is asked for once more, then `no_verdict: invalid_output`.
- An answer cut off at the output limit is `no_verdict: truncated`, and is not asked for again.
- A refusal is `no_verdict: refused`, with the provider's category. No other model is ever tried in its place.
- A request the provider will not accept, for a reason that trying again at once will not fix, leaves the slot
  waiting with the provider's status and short code, and the run goes on to the next model. It is asked again on
  later runs, and after the number of runs in the settings it becomes `no_verdict: error`.
- A run stops when a fault needs a person: a refused key or permission, no credit, a model the provider does not
  have, or the same model turning down two proposals in one run.
- A monthly limit that would be passed leaves the slot pending, without counting against it.
"""
import re
from typing import Callable, Dict, List, Optional

from . import verdicts
from .models import Call, ProviderError, Transient

TRIES_PER_RUN = 3
PAUSES = (2, 8)  # seconds before the second try and before the third
TURNED_DOWN_TWICE = 2  # the same model turning down this many requests in one run points to the set-up, not to a proposal
wait: Optional[Callable[[float], None]] = None  # time.sleep for a real run. Dry runs and tests do not wait.


def _base(slot) -> dict:
    return {
        "slot": slot.slot,
        "provider": slot.provider,
        "model": slot.model,
        "reported_model": None,
        "kind": slot.kind,
        "effort": slot.effort,
        "max_output_tokens": slot.max_output_tokens,
        "threshold": slot.threshold,
        "status": "pending",
        "reason": None,
        "refusal_category": None,
        "error_code": None,
        "verdict": None,
        "runs": 0,
        "calls": [],
    }


# A provider may add a version stamp to the ID that was asked for: a date, or three digits. Nothing else is the same model.
_STAMP = r"(?:[-@](?:\d{8}|\d{4}-\d{2}-\d{2}|\d{3}))?"


def _is_requested_model(slot, reported: Optional[str]) -> bool:
    """True only for the model that was asked for: its exact ID, or that ID with a version stamp."""
    if slot.model is None or not isinstance(reported, str):
        return False
    name = reported[len("models/") :] if reported.startswith("models/") else reported
    return re.fullmatch(re.escape(slot.model) + _STAMP, name) is not None


def _one_call(slot, adapter, assembled, schema, question, limits, usage, now: str, result: dict):
    """One request, with passing faults tried again. Returns a Call, or a word for why there is none."""
    blocked = limits.may_call(usage, slot.provider, assembled.tokens, slot.max_output_tokens)
    if blocked:
        return blocked
    for attempt in range(TRIES_PER_RUN):
        if attempt and wait:
            wait(PAUSES[attempt - 1])
        try:
            call = adapter.call(slot, assembled, schema, question)
        except Transient:
            continue
        except ProviderError as turned_down:
            if turned_down.stops_the_run:
                raise
            call = Call("rejected", error_code=turned_down.short, fault=turned_down)  # counted as a call: a request was made
            usage.add_turned_down(slot.slot)
        usage.add_call(slot.provider, call.input_tokens, call.output_tokens)
        result["calls"].append(
            {"at": now, "status": call.status, "input_tokens": int(call.input_tokens or 0), "cached_input_tokens": int(call.cached_input_tokens or 0), "output_tokens": int(call.output_tokens or 0), "latency_ms": int(call.latency_ms or 0)}
        )
        return call
    return "transient_error"


def _answer(slot, call: Call, read: Callable):
    """(verdict as a dict, None) for a usable answer; (None, reason) when there is none; raises Invalid for a bad shape."""
    if call.status == "truncated":
        return None, "truncated"
    if call.status == "too_large":  # the provider's own count says the input cannot fit
        return None, "too_large"
    if call.status == "refused":
        return None, "refused"
    if call.status == "rejected":
        return None, "error"
    if call.status != "ok":
        return None, "error"
    if call.reported_model is None:
        if slot.kind != "probability":  # only an API that answers with a bare probability leaves the model's name out
            return None, "model_not_reported"
    elif not _is_requested_model(slot, call.reported_model):
        return None, "wrong_model"
    verdict = read(slot, call.answer)
    return dict(vars(verdict)), None


def review_stage(stage: str, slots, assembled, results: List[dict], adapters: Dict, settings, limits, usage, now: str, index_numbers=frozenset(), question: Optional[str] = None, second_opinion: bool = False) -> List[dict]:
    """Fills `results` in place, one slot at a time, so that whatever was paid for survives a failure part-way.

    `results` holds what earlier runs recorded for this stage (or nothing). It is the list inside the record.
    """
    schema = verdicts.GATE_SCHEMA if stage == "gate" else verdicts.panel_schema(settings)

    def read(slot, answer):
        if stage == "panel":
            return verdicts.read_panel(answer, settings, index_numbers)
        if slot.kind == "probability":
            return verdicts.gate_from_probability(answer, slot.threshold)
        return verdicts.read_gate(answer, settings.reason_max_characters)

    earlier = {r.get("slot"): r for r in results}
    del results[:]
    for slot in slots:
        result = earlier.get(slot.slot)
        if result is None or result.get("model") != slot.model:
            result = _base(slot)
        results.append(result)
        if result["status"] == "pending":
            _fill(slot, result, assembled, adapters, schema, question if slot.kind == "probability" else None, read, limits, usage, now, settings)
        if stage == "panel" and second_opinion and result["status"] == "verdict" and (result.get("second") or {}).get("status", "pending") == "pending":
            _second(slot, result, assembled, adapters, schema, read, limits, usage, now)
    return results


def _fill(slot, result, assembled, adapters, schema, question, read, limits, usage, now, settings) -> None:
    if assembled.too_large(slot.max_input_tokens):
        result.update(status="no_verdict", reason="too_large")
        return
    adapter = adapters.get(slot.provider)
    if adapter is None:
        result.update(status="pending", reason="no_adapter")
        return
    for attempt in (1, 2):  # the second is the one retry allowed after an answer in the wrong shape
        call = _one_call(slot, adapter, assembled, schema, question, limits, usage, now, result)
        if isinstance(call, str):
            if call == "transient_error":
                result["runs"] += 1
                if result["runs"] >= settings.caps.runs_before_error:
                    result.update(status="no_verdict", reason="error")
                else:
                    result.update(status="pending", reason="transient_error")
            else:
                result.update(status="pending", reason=call)  # a monthly limit: not counted against the slot
            return
        if call.status == "rejected":
            _turned_down(slot, result, call, usage, settings)
            return
        result["reported_model"] = call.reported_model
        try:
            verdict, why = _answer(slot, call, read)
        except verdicts.Invalid:
            result["calls"][-1]["status"] = "invalid"
            if attempt == 1:
                continue
            result.update(status="no_verdict", reason="invalid_output")
            return
        if verdict is None:
            result.update(status="no_verdict", reason=why, refusal_category=call.refusal_category if why == "refused" else None, error_code=call.error_code if why == "error" else None)
        else:
            result.update(status="verdict", reason=None, verdict=verdict)
        return


def _turned_down(slot, result, call: Call, usage, settings) -> None:
    """The provider would not take the request, for a reason that trying again at once will not fix.

    What one proposal says can cause that. So can a fault in the set-up that nobody has noticed yet, and an error
    recorded for a slot is never asked again. So the slot waits and is asked again on later runs, as many as the
    settings allow, before it becomes an error. And if the same model turns down a second proposal in the same run,
    the run stops there: that points to the set-up, not to either proposal.
    """
    result["runs"] += 1
    settled = result["runs"] >= settings.caps.runs_before_error
    result.update(status="no_verdict" if settled else "pending", reason="error" if settled else "rejected", error_code=call.error_code)
    _stop_if_turned_down_twice(slot, call, usage)


def _stop_if_turned_down_twice(slot, call: Call, usage) -> None:
    if call.fault is not None and usage.turned_down_this_run.get(slot.slot, 0) >= TURNED_DOWN_TWICE:
        call.fault.repeated = True
        raise call.fault


def _second(slot, result, assembled, adapters, schema, read, limits, usage, now) -> None:
    """Self-consistency: one more independent call to a model that answered, to see whether it says the same."""
    adapter = adapters.get(slot.provider)
    call = _one_call(slot, adapter, assembled, schema, None, limits, usage, now, result) if adapter else "no_adapter"
    if isinstance(call, str):
        if call != "transient_error" and call != "no_adapter":
            result["second"] = {"status": "pending", "reason": call}  # a monthly limit: asked on a later run
            return
        result["second"] = {"status": "no_verdict", "reason": "error"}
        return
    if call.status == "rejected":
        result["second"] = {"status": "no_verdict", "reason": "error"}
        _stop_if_turned_down_twice(slot, call, usage)
        return
    try:
        verdict, why = _answer(slot, call, read)
    except verdicts.Invalid:
        result["calls"][-1]["status"] = "invalid"
        verdict, why = None, "invalid_output"
    if verdict is None:
        result["second"] = {"status": "no_verdict", "reason": why}
    else:
        result["second"] = {"status": "verdict", "outcome": verdict["outcome"], "same": verdict["outcome"] == result["verdict"]["outcome"]}


def stage_complete(results: List[dict], second_opinion: bool = False) -> bool:
    if any(r["status"] == "pending" for r in results):
        return False
    return not second_opinion or all((r.get("second") or {}).get("status") in ("verdict", "no_verdict") for r in results if r["status"] == "verdict")


def gate_recommendation(results: List[dict]) -> Optional[str]:
    """"spam", "not_spam" or "no_majority" once every gate slot is settled; None while any is pending."""
    if any(r["status"] == "pending" for r in results):
        return None
    answers = [verdicts.GateVerdict(r["verdict"]["spam"], r["verdict"]["probability"], None) if r["status"] == "verdict" else None for r in results]
    return verdicts.gate_majority(answers)


def panel_recommendation(results: List[dict]) -> Optional[str]:
    """The outcome every panel model gave, or None. A missing answer makes the case incomplete, not unanimous."""
    answers = [verdicts.PanelVerdict(r["verdict"]["outcome"], r["verdict"]["confidence"], None, []) if r["status"] == "verdict" else None for r in results]
    return verdicts.panel_unanimous(answers, len(results))
