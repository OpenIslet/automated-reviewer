"""What a model may answer, and how an answer is checked and trimmed, the same way for every model.

Providers can enforce types and the list of outcomes, and not always lengths or ranges. So those limits are applied
here: keep the first few reasons, cut each to a length, clamp numbers to 0 to 1. An answer is trimmed, not rejected,
for being too long. It is rejected (Invalid) when it is not the right shape at all.
"""
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

GATE_SCHEMA = {
    "type": "object",
    "properties": {"spam": {"type": "boolean"}, "probability": {"type": "number"}, "reason": {"type": ["string", "null"]}},
    "required": ["spam", "probability", "reason"],
    "additionalProperties": False,
}


def panel_schema(settings) -> dict:
    return {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": list(settings.outcome_keys)},
            "confidence": {"type": "number"},
            "covered_by": {"type": ["integer", "null"]},
            "reasons": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["outcome", "confidence", "covered_by", "reasons"],
        "additionalProperties": False,
    }


class Invalid(Exception):
    """The answer is not in the agreed shape. The message is a short fixed word."""


@dataclass
class PanelVerdict:
    outcome: str
    confidence: float
    covered_by: Optional[int]
    reasons: List[str]
    flags: List[str] = field(default_factory=list)


@dataclass
class GateVerdict:
    spam: bool
    probability: float
    reason: Optional[str]
    flags: List[str] = field(default_factory=list)


def _object(raw) -> dict:
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise Invalid("not_json")
    if not isinstance(raw, dict):
        raise Invalid("not_an_object")
    return raw


def _number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise Invalid(f"{name}_not_a_number")
    return float(value)


def plain(text: str, limit: int) -> str:
    """Model text made plain: no control characters, one run of spaces at a time, no longer than `limit`."""
    flat = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f  ]", " ", text)).strip()
    return flat if len(flat) <= limit else flat[:limit].rstrip()


def read_panel(raw, settings, index_numbers) -> PanelVerdict:
    answer = _object(raw)
    flags: List[str] = []
    outcome = answer.get("outcome")
    if outcome not in settings.outcome_keys:
        raise Invalid("outcome_not_allowed")
    given = _number(answer.get("confidence"), "confidence")
    confidence = min(1.0, max(0.0, given))
    if confidence != given:
        flags.append("confidence_clamped")
    covered = answer.get("covered_by")
    if isinstance(covered, float) and covered == int(covered):
        covered = int(covered)
    if covered is not None and (isinstance(covered, bool) or not isinstance(covered, int)):
        raise Invalid("covered_by_not_a_number")
    if covered is not None and covered not in index_numbers:
        covered = None
        flags.append("covered_by_not_in_index")
    reasons = answer.get("reasons")
    if not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons):
        raise Invalid("reasons_not_a_list_of_text")
    if len(reasons) > settings.reasons_kept:
        flags.append("reasons_trimmed")
    kept = [plain(r, settings.reason_max_characters) for r in reasons[: settings.reasons_kept]]
    return PanelVerdict(outcome, confidence, covered, [r for r in kept if r], flags)


def read_gate(raw, reason_limit: int = 300) -> GateVerdict:
    answer = _object(raw)
    spam = answer.get("spam")
    if not isinstance(spam, bool):
        raise Invalid("spam_not_true_or_false")
    given = _number(answer.get("probability"), "probability")
    probability = min(1.0, max(0.0, given))
    reason = answer.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise Invalid("reason_not_text")
    return GateVerdict(spam, probability, plain(reason, reason_limit) or None if reason else None, ["probability_clamped"] if probability != given else [])


def gate_from_probability(probability, threshold: float) -> GateVerdict:
    """For a decision-style model that returns a probability and no text: spam when it reaches the threshold."""
    given = _number(probability, "probability")
    clamped = min(1.0, max(0.0, given))
    return GateVerdict(clamped >= threshold, clamped, None, ["probability_clamped"] if clamped != given else [])


def gate_majority(verdicts: Sequence[Optional[GateVerdict]]) -> str:
    """"spam" or "not_spam" when at least two valid answers agree; otherwise "no_majority"."""
    valid = [v.spam for v in verdicts if v is not None]
    if valid.count(True) >= 2:
        return "spam"
    if valid.count(False) >= 2:
        return "not_spam"
    return "no_majority"


def panel_unanimous(verdicts: Sequence[Optional[PanelVerdict]], slots: int) -> Optional[str]:
    """The outcome all the panel's models gave. None when any is missing or they differ: incomplete is not unanimous."""
    if len(verdicts) != slots or any(v is None for v in verdicts):
        return None
    outcomes = {v.outcome for v in verdicts}
    return outcomes.pop() if len(outcomes) == 1 else None
