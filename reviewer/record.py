"""One proposal's result: how it is built, checked, written as a log comment, and read back.

The comment (docs/SPEC.md, section 7):

- starts with a fixed header saying it is not a decision;
- has a table built only from checked values and numbers;
- holds no text written by a contributor, and names the proposal by number in code formatting so GitHub makes no
  cross-reference on the contributor's issue;
- puts the models' reasons inside a code fence longer than any run of backticks in them;
- carries the whole record as base64-encoded JSON in a hidden block, which is the comment's last line. Only a block
  in that place is ever read back, so a block that a model writes into one of its reasons is plain text.

`clean` rebuilds a record field by field and keeps only values of the expected kind. The publish job runs it on the
results file, which it treats as untrusted, and again on every record read back from the log.
"""
import base64
import json
import re
from typing import Dict, List, Optional, Tuple

from . import review
from .actions import actions_for
from .github import COMMENT_LIMIT
from .verdicts import plain

VERSION = 1
HEADER = "**Automated reviewer, in shadow mode. This is not a decision and has no effect on any proposal.**"
DRY_RUN_NOTE = "DRY RUN. No model was called: every verdict below is a stand-in."
_BLOCK = re.compile(r"(?:\A|\n)<!-- automated-reviewer:record v1 ([A-Za-z0-9+/=]+) -->[ \t\r\n]*\Z")

_HEX64 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{4,40}")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_WORD = re.compile(r"[A-Za-z0-9_:.\[\]-]{1,80}")
_MODEL = re.compile(r"[A-Za-z0-9_.:@/-]{1,100}")
_FIXTURE = re.compile(r"[a-z0-9][a-z0-9-]{0,59}")
_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(\[bot\])?")

CONFIRMED = ("hash_match", "never_edited", "first_version")
EXCLUSIONS = ("administrative_triage", "self_decided", "text_unverified", "unparsed", "context_unavailable")
NO_VERDICT = ("too_large", "invalid_output", "truncated", "refused", "error", "wrong_model", "model_not_reported")
PENDING = ("transient_error", "rejected", "no_adapter", "monthly_calls", "monthly_input_tokens", "monthly_output_tokens")
ORDER_WORDS = {
    "after_the_verdict": "This verdict was posted before that decision.",
    "after_part_of_the_verdict": "Part of this verdict was posted before that decision.",
    "before_any_verdict": "That decision was made before any of this verdict was posted.",
}
REVIEW_VERDICTS = ("Supported", "Partly supported", "Not supported", "Cannot tell")
GATE_WORDS = {"spam": "spam", "not_spam": "not spam", "no_majority": "no majority"}
EXCLUSION_WORDS = {
    "administrative_triage": "it was set aside for an administrative reason",
    "self_decided": "it is the maintainer's own proposal",
    "text_unverified": "its text could not be confirmed as submitted",
    "unparsed": "its text does not have the expected structure",
    "context_unavailable": "the project's files from before it was sent could not be read",
}


class BadRecord(Exception):
    """A record is not in the expected form. The message is a short fixed word."""


# ---- building ----------------------------------------------------------------------------------------------------------

def new_record(kind: str, proposal, policy: str, now: str, dry_run: bool) -> dict:
    return {
        "v": VERSION,
        "kind": kind,
        "proposal": proposal,
        "policy_version": policy,
        "dry_run": bool(dry_run),
        "submitted_at": None,
        "first_reviewed_at": None,
        "updated_at": now,
        "calls_made": False,
        "text": {"sha256": None, "confirmed": None, "flags": []},
        "context": {"commit": None, "evidence_source": None, "checks_included": False, "index_size": 0},
        "decision": None,
        "reviewed_before_decision": None,  # True when the verdict was posted before the maintainer decided
        "posted_at": None,  # when the whole verdict (every model's first answer) was first posted. Only the publish job sets it.
        "first_verdict_posted_at": None,  # when any model's answer was first posted. Only the publish job sets it.
        "labels_seen": [],  # the decision and closure labels the issue carried when this record was last written
        "stopped": None,  # a reason from EXCLUSIONS, when a review that had begun was stopped part-way
        "self_decided": False,
        "exclusion": None,
        "fixture": None,
        "contributor": {"ai_draft": None, "ai_review": None, "blind_review_verdict": None},
        "gate": {"slots": [], "recommendation": None},
        "panel": {"slots": [], "recommendation": None, "second_opinion": False},
        "actions": [],
        "complete": False,
    }


def decision_of(standing, same_as: Optional[int] = None, same_as_how: Optional[str] = None, same_as_decided_then: Optional[bool] = None) -> dict:
    return {"label": standing.label, "by": standing.actor, "at": standing.at, "same_as": same_as, "same_as_how": same_as_how, "same_as_decided_then": same_as_decided_then}


def finish(record: dict, settings) -> dict:
    """Works out what each stage concluded, whether the record is complete, and the actions: always none."""
    if record["exclusion"] is None:
        record["gate"]["recommendation"] = review.gate_recommendation(record["gate"]["slots"]) if record["gate"]["slots"] else None
        record["panel"]["recommendation"] = review.panel_recommendation(record["panel"]["slots"]) if record["panel"]["slots"] else None
        gate_only = bool(record["fixture"]) and record["fixture"]["kind"] != "adversarial"  # junk and borderline fixtures go to the gate alone
        gate_done = bool(record["gate"]["slots"]) and review.stage_complete(record["gate"]["slots"])
        panel_done = bool(record["panel"]["slots"]) and review.stage_complete(record["panel"]["slots"], record["panel"]["second_opinion"])
        record["complete"] = bool(record["stopped"]) or (gate_done and (gate_only or panel_done))
    else:
        record["complete"] = True
    record["calls_made"] = any(slot.get("calls") for stage in ("gate", "panel") for slot in record[stage]["slots"])
    record["actions"] = actions_for(record, settings)
    return record


def key(record: dict) -> Tuple[str, object, str]:
    return record["kind"], record["proposal"], record["policy_version"]


def shows_a_verdict(record: dict) -> bool:
    """True when the record's comment shows at least one model's answer."""
    return any(slot["status"] == "verdict" for stage in ("gate", "panel") for slot in record[stage]["slots"])


def first_answers_settled(record: dict) -> bool:
    """True when every gate and panel model has given its first answer, or has a recorded reason for giving none.

    Second opinions do not count. They are never shown in the comment's table or on the scoreboard, so a second
    opinion that is still waiting must not make the verdict look as if it had not been posted.
    """
    if record["exclusion"] is not None or record["stopped"]:
        return False
    gate, panel = record["gate"]["slots"], record["panel"]["slots"]
    return bool(gate) and bool(panel) and review.stage_complete(gate) and review.stage_complete(panel)


def decision_timing(record: dict) -> Optional[str]:
    """Where the maintainer's decision fell, or None while there is none.

    "after_the_verdict": the whole verdict was in the log when he decided.
    "after_part_of_the_verdict": at least one model's answer was, and not all.
    "before_any_verdict": nothing a model said had been posted.
    """
    decided_at = (record.get("decision") or {}).get("at")
    if not decided_at:
        return None
    if record.get("posted_at") and record["posted_at"] < decided_at:
        return "after_the_verdict"
    if record.get("first_verdict_posted_at") and record["first_verdict_posted_at"] < decided_at:
        return "after_part_of_the_verdict"
    return "before_any_verdict"


def posted_before_decision(record: dict) -> bool:
    """True when the whole verdict was already in the log at the moment the maintainer decided."""
    return decision_timing(record) == "after_the_verdict"


# ---- checking ----------------------------------------------------------------------------------------------------------

def _match(pattern, value) -> Optional[str]:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _unit(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return min(1.0, max(0.0, float(value)))


def _words(values) -> List[str]:
    return [v for v in (values if isinstance(values, list) else []) if _match(_WORD, v)][:40]


def _text(value, limit: int) -> Optional[str]:
    return (plain(value, limit) or None) if isinstance(value, str) else None


def _clean_slot(raw, stage: str, settings) -> dict:
    if not isinstance(raw, dict):
        raise BadRecord("slot_not_an_object")
    status = raw.get("status") if raw.get("status") in ("verdict", "no_verdict", "pending") else None
    if status is None or not _match(_WORD, raw.get("slot")):
        raise BadRecord("slot_without_status")
    reasons = NO_VERDICT if status == "no_verdict" else PENDING
    slot = {
        "slot": raw["slot"],
        "provider": _match(_WORD, raw.get("provider")),
        "model": _match(_MODEL, raw.get("model")),
        "reported_model": _match(_MODEL, raw.get("reported_model")),
        "kind": raw.get("kind") if raw.get("kind") in ("text", "probability", "panel") else None,
        "effort": _match(_WORD, raw.get("effort")),
        "max_output_tokens": _count(raw.get("max_output_tokens")),
        "threshold": _unit(raw.get("threshold")),
        "status": status,
        "reason": raw.get("reason") if status != "verdict" and raw.get("reason") in reasons else None,
        "refusal_category": _text(re.sub(r"[^A-Za-z0-9_ .:-]", " ", raw["refusal_category"]), 60) if isinstance(raw.get("refusal_category"), str) else None,
        "error_code": _match(_WORD, raw.get("error_code")) if (status, raw.get("reason")) in (("no_verdict", "error"), ("pending", "rejected")) else None,
        "verdict": None,
        "runs": _count(raw.get("runs")),
        "calls": [],
    }
    for call in raw.get("calls") if isinstance(raw.get("calls"), list) else []:
        if isinstance(call, dict) and _match(_TIME, call.get("at")):
            slot["calls"].append({"at": call["at"], "status": _match(_WORD, call.get("status")), "input_tokens": _count(call.get("input_tokens")), "cached_input_tokens": _count(call.get("cached_input_tokens")), "output_tokens": _count(call.get("output_tokens")), "thinking_tokens": _count(call.get("thinking_tokens")), "latency_ms": _count(call.get("latency_ms"))})
    verdict = raw.get("verdict")
    if status == "verdict":
        if not isinstance(verdict, dict):
            raise BadRecord("verdict_missing")
        if stage == "panel":
            confidence = _unit(verdict.get("confidence"))
            if verdict.get("outcome") not in settings.outcome_keys or confidence is None:
                raise BadRecord("verdict_not_in_the_expected_form")
            covered = verdict.get("covered_by")
            listed = verdict.get("reasons") if isinstance(verdict.get("reasons"), list) else []
            slot["verdict"] = {
                "outcome": verdict["outcome"],
                "confidence": confidence,
                "covered_by": covered if isinstance(covered, int) and not isinstance(covered, bool) and covered > 0 else None,
                "reasons": [t for t in (_text(r, settings.reason_max_characters) for r in listed[: settings.reasons_kept]) if t],
                "flags": _words(verdict.get("flags")),
            }
        else:
            probability = _unit(verdict.get("probability"))
            if not isinstance(verdict.get("spam"), bool) or probability is None:
                raise BadRecord("verdict_not_in_the_expected_form")
            slot["verdict"] = {"spam": verdict["spam"], "probability": probability, "reason": _text(verdict.get("reason"), settings.reason_max_characters), "flags": _words(verdict.get("flags"))}
    second = raw.get("second")
    if stage == "panel" and isinstance(second, dict):
        if second.get("status") == "verdict" and second.get("outcome") in settings.outcome_keys and slot["verdict"]:
            slot["second"] = {"status": "verdict", "outcome": second["outcome"], "same": second["outcome"] == slot["verdict"]["outcome"]}
        elif second.get("status") == "pending" and second.get("reason") in PENDING:
            slot["second"] = {"status": "pending", "reason": second["reason"]}
        else:
            slot["second"] = {"status": "no_verdict", "reason": second.get("reason") if second.get("reason") in NO_VERDICT else "error"}
    return slot


def clean(raw, settings) -> dict:
    """A record rebuilt from `raw`, keeping only values of the expected kind. Raises BadRecord if it cannot be one."""
    if not isinstance(raw, dict) or raw.get("v") != VERSION:
        raise BadRecord("not_a_record")
    kind = raw.get("kind")
    proposal = raw.get("proposal")
    if kind == "proposal":
        if isinstance(proposal, bool) or not isinstance(proposal, int) or proposal < 1:
            raise BadRecord("proposal_not_a_number")
    elif kind == "fixture":
        if not _match(_FIXTURE, proposal):
            raise BadRecord("fixture_without_an_id")
    else:
        raise BadRecord("unknown_kind")
    policy = _match(_HEX64, raw.get("policy_version"))
    updated = _match(_TIME, raw.get("updated_at"))
    if not policy or not updated:
        raise BadRecord("no_policy_version_or_time")
    if raw.get("actions") not in ([], None):
        raise BadRecord("actions_not_empty")

    record = new_record(kind, proposal, policy, updated, raw.get("dry_run") is True)
    record["first_reviewed_at"] = _match(_TIME, raw.get("first_reviewed_at"))
    record["submitted_at"] = _match(_TIME, raw.get("submitted_at"))
    record["posted_at"] = _match(_TIME, raw.get("posted_at"))
    record["first_verdict_posted_at"] = _match(_TIME, raw.get("first_verdict_posted_at"))
    watched = settings.decision_labels | {settings.administrative_closure_label}
    seen = raw.get("labels_seen") if isinstance(raw.get("labels_seen"), list) else []
    record["labels_seen"] = sorted({label for label in seen if isinstance(label, str) and label in watched})
    text = raw.get("text") if isinstance(raw.get("text"), dict) else {}
    record["text"] = {"sha256": _match(_HEX64, text.get("sha256")), "confirmed": text.get("confirmed") if text.get("confirmed") in CONFIRMED else None, "flags": _words(text.get("flags"))}
    context = raw.get("context") if isinstance(raw.get("context"), dict) else {}
    record["context"] = {"commit": _match(_COMMIT, context.get("commit")), "evidence_source": _match(_WORD, context.get("evidence_source")), "checks_included": context.get("checks_included") is True, "index_size": _count(context.get("index_size"))}
    decision = raw.get("decision")
    if isinstance(decision, dict):
        label = decision.get("label")
        if label not in settings.decision_labels and label != settings.administrative_closure_label:
            raise BadRecord("decision_label_not_known")
        same = decision.get("same_as")
        record["decision"] = {
            "label": label,
            "by": _match(_LOGIN, decision.get("by")),
            "at": _match(_TIME, decision.get("at")),
            "same_as": same if isinstance(same, int) and not isinstance(same, bool) and same > 0 else None,
            "same_as_how": decision.get("same_as_how") if decision.get("same_as_how") in ("same_as_line", "fallback_wording") else None,
            "same_as_decided_then": decision.get("same_as_decided_then") if isinstance(decision.get("same_as_decided_then"), bool) else None,
        }
    record["self_decided"] = raw.get("self_decided") is True
    record["reviewed_before_decision"] = raw.get("reviewed_before_decision") if isinstance(raw.get("reviewed_before_decision"), bool) and record["decision"] else None
    exclusion = raw.get("exclusion")
    if isinstance(exclusion, dict):
        if exclusion.get("reason") not in EXCLUSIONS:
            raise BadRecord("exclusion_not_known")
        record["exclusion"] = {"reason": exclusion["reason"], "detail": _match(_WORD, exclusion.get("detail"))}
    stopped = raw.get("stopped")
    record["stopped"] = stopped if isinstance(stopped, str) and stopped in EXCLUSIONS and record["exclusion"] is None else None
    fixture = raw.get("fixture")
    if kind == "fixture":
        if not isinstance(fixture, dict) or fixture.get("kind") not in ("junk", "borderline", "direct", "adversarial"):
            raise BadRecord("fixture_without_a_kind")
        record["fixture"] = {
            "kind": fixture["kind"],
            "expect_spam": fixture.get("expect_spam") if isinstance(fixture.get("expect_spam"), bool) else None,
            "steers_towards": fixture.get("steers_towards") if fixture.get("steers_towards") in settings.outcome_keys else None,
        }
    contributor = raw.get("contributor") if isinstance(raw.get("contributor"), dict) else {}
    record["contributor"] = {
        "ai_draft": _text(contributor.get("ai_draft"), 120),
        "ai_review": _text(contributor.get("ai_review"), 120),
        "blind_review_verdict": contributor.get("blind_review_verdict") if contributor.get("blind_review_verdict") in REVIEW_VERDICTS else None,
    }
    for stage in ("gate", "panel"):
        given = raw.get(stage) if isinstance(raw.get(stage), dict) else {}
        slots = given.get("slots") if isinstance(given.get("slots"), list) else []
        record[stage]["slots"] = [_clean_slot(slot, stage, settings) for slot in slots[:8]]
    record["panel"]["second_opinion"] = isinstance(raw.get("panel"), dict) and raw["panel"].get("second_opinion") is True
    if record["exclusion"] is not None and (record["gate"]["slots"] or record["panel"]["slots"]):
        raise BadRecord("excluded_but_reviewed")
    return finish(record, settings)


# ---- writing -----------------------------------------------------------------------------------------------------------

def _cell(slot: dict, stage: str, settings, stopped: bool = False) -> Tuple[str, str]:
    if slot["status"] == "pending":
        if stopped:
            return "not asked", ""
        if slot["reason"] == "rejected":  # the provider would not take the request; it is asked again on later runs
            return "turned down by the provider" + (f" (`{slot['error_code']}`)" if slot.get("error_code") else "") + ", to be asked again", ""
        return "waiting for a later run", ""
    if slot["status"] == "no_verdict":
        code = f" (`{slot['error_code']}`)" if slot.get("error_code") else ""  # the provider's status and short word
        return f"no verdict: {slot['reason'] or 'error'}".replace("_", " ") + code, ""
    verdict = slot["verdict"]
    if stage == "gate":
        return ("spam" if verdict["spam"] else "not spam"), f"{verdict['probability']:.2f}"
    return settings.outcome(verdict["outcome"]).name, f"{verdict['confidence']:.2f}"


def _shown(reason: str) -> str:
    """A reason as it is displayed: nothing in it can open or close an HTML comment. The record keeps it as written."""
    return reason.replace("<!--", "<!- -").replace("-->", "- ->")


def render(record: dict, settings) -> str:
    """The comment for one record. `record` must have been through `clean`."""
    name = f"`#{record['proposal']}`" if record["kind"] == "proposal" else f"fixture `{record['proposal']}`"
    lines = [HEADER, ""]
    if record["dry_run"]:
        lines += [DRY_RUN_NOTE, ""]
    lines += [f"Proposal {name} · policy `{record['policy_version'][:12]}` · last updated `{record['updated_at']}`", ""]
    decision = record["decision"]
    outcome = settings.outcome_for_label(decision["label"]) if decision else None
    if outcome:
        order = ORDER_WORDS[decision_timing(record) or "before_any_verdict"]
        lines += [f"Outcome the maintainer recorded: {outcome.name}. " + ("" if record["exclusion"] or record["stopped"] else order), ""]
    elif decision:
        lines += ["The maintainer set this proposal aside.", ""]
    elif not record["exclusion"] and record["kind"] == "proposal":
        lines += ["The maintainer has not decided this proposal yet.", ""]
    reasons: List[str] = []
    if record["fixture"]:
        lines += [f"This is a made-up test submission ({record['fixture']['kind']}), not a real proposal.", ""]
    if record["exclusion"]:
        lines += [f"Left out of the trial: {EXCLUSION_WORDS[record['exclusion']['reason']]}. No model was asked.", ""]
    else:
        if record["stopped"]:
            lines += [f"The review was stopped part-way: {EXCLUSION_WORDS[record['stopped']]}. The answers already given are kept.", ""]
        lines += ["| Stage | Model | Verdict | Confidence |", "|---|---|---|---|"]
        for stage, label in (("gate", "Gate"), ("panel", "Panel")):
            for slot in record[stage]["slots"]:
                verdict, confidence = _cell(slot, stage, settings, bool(record["stopped"]))
                lines.append(f"| {label} | `{slot['model'] or slot['slot']}` | {verdict} | {confidence} |")
                if slot["status"] == "verdict":
                    said = slot["verdict"]["reasons"] if stage == "panel" else ([slot["verdict"]["reason"]] if slot["verdict"]["reason"] else [])
                    reasons += [f"{slot['slot']}: {_shown(reason)}" for reason in said]
        gate = GATE_WORDS.get(record["gate"]["recommendation"], "not settled yet")
        panel = record["panel"]["recommendation"]
        agreed = f"all agree on {settings.outcome(panel).name}" if panel else ("no outcome all three agree on" if record["complete"] else "not settled yet")
        if not record["panel"]["slots"] and record["complete"]:
            agreed = "not asked"
        lines += ["", f"Gate: {gate}. Panel: {agreed}. Action taken: none.", ""]
    if reasons:
        longest = max((len(run) for reason in reasons for run in re.findall(r"`+", reason)), default=0)
        fence = "`" * max(3, longest + 1)
        lines += ["The models' reasons, as plain text:", "", fence + "text"] + reasons + [fence, ""]
    packed = base64.b64encode(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")).decode("ascii")
    lines.append(f"<!-- automated-reviewer:record v1 {packed} -->")
    text = "\n".join(lines)
    if len(text) > COMMENT_LIMIT:
        raise BadRecord("comment_too_long")
    return text


# ---- reading back ------------------------------------------------------------------------------------------------------

def parse(body: str) -> Optional[dict]:
    """The record packed in the last line of a comment, or None. The result still has to go through `clean`.

    A block anywhere else in the comment is not a record. The models' reasons are shown above the last line, and a
    model can be talked into writing anything, including a block.
    """
    found = _BLOCK.search(body or "")
    if not found:
        return None
    try:
        data = json.loads(base64.b64decode(found.group(1), validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def trusted_records(comments: List[Dict], settings) -> List[Tuple[int, dict]]:
    """(comment id, record) for every record in the log that the workflow itself wrote.

    Only comments by the workflow's own identity count. Anyone can write text that imitates the format, so the author
    is the only thing that makes a record genuine. Where two comments hold a record for the same proposal and policy
    version, only the later one is returned.
    """
    found: Dict[Tuple[str, object, str], Tuple[int, dict]] = {}
    for comment in comments or []:
        author = comment.get("user") or {}
        if author.get("login") != settings.record_author or author.get("type") != "Bot":
            continue
        raw = parse(comment.get("body") or "")
        if raw is None:
            continue
        try:
            cleaned = clean(raw, settings)
        except BadRecord:
            continue
        # One record for each proposal and policy version: the last one in the log. A comment can be written twice,
        # when GitHub takes it and the answer is lost on the way back, and every count is made from this list.
        found.pop(key(cleaned), None)
        found[key(cleaned)] = (comment["id"], cleaned)
    return list(found.values())
