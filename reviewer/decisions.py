"""Has the maintainer decided this proposal, and does it belong in the trial?

Everything here is read from the issue's label events, in a fixed order (see the spec, section 3):

1. An administrative closure, which carries no decision label and so must be looked for first.
2. No decision label from an allowed decider: the issue is never touched.
3. A self-decided proposal, found before any text is confirmed or parsed.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .issuebody import first_line_login

GRACE_SECONDS = 300  # a decision label taken off again this soon after it was applied was a slip of the hand
_TIME = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class Rules:
    decision_labels: frozenset
    triage_label: str
    allowed_deciders: frozenset
    skip_issues: frozenset = frozenset()
    reconcile_self_decided: bool = False


@dataclass
class Standing:
    # "not_a_proposal", "undecided", "excluded" or "decided"
    status: str
    reason: Optional[str] = None  # for "excluded": administrative_triage or self_decided
    label: Optional[str] = None
    actor: Optional[str] = None
    at: Optional[str] = None
    self_decided: bool = False
    flags: List[str] = field(default_factory=list)


def _labelled(events: List[Dict], kind: str = "labeled") -> List[Dict]:
    rows = []
    for e in events or []:
        if e.get("event") != kind:
            continue
        name = (e.get("label") or {}).get("name")
        actor = (e.get("actor") or {}).get("login")
        at = e.get("created_at")
        if isinstance(name, str) and isinstance(actor, str) and isinstance(at, str):
            rows.append({"label": name, "actor": actor, "at": at})
    # Oldest first. ISO timestamps in UTC sort as text; sorted() keeps the API's order for events in the same second.
    return sorted(rows, key=lambda r: r["at"])


def _seconds_between(earlier: str, later: str) -> Optional[float]:
    try:
        return (datetime.strptime(later, _TIME) - datetime.strptime(earlier, _TIME)).total_seconds()
    except ValueError:
        return None


def _slips(decisions: List[Dict], removals: List[Dict], rules: Rules) -> List[Dict]:
    """Decision labels that were a slip of the hand, and so are not decisions.

    One counts as a slip when an allowed decider took it off again within the grace time and a decision label was
    applied in its place. The label that replaced it is then the decision. Without this, a click on the wrong label,
    put right seconds later, would stand as the decision and the case would be reported as decided more than once.
    A label that was taken off and not replaced still stands, and so does one taken off later than the grace time.
    """
    found = []
    for row in decisions:
        removed = next((r for r in removals if r["label"] == row["label"] and r["actor"] in rules.allowed_deciders and r["at"] >= row["at"]), None)
        if removed is None:
            continue
        gap = _seconds_between(row["at"], removed["at"])
        replaced = any(other is not row and other["at"] >= row["at"] for other in decisions)
        if gap is not None and gap <= GRACE_SECONDS and replaced:
            found.append(row)
    return found


def standing_of(issue: Dict, events: List[Dict], rules: Rules, named_in_manual_run: bool = False) -> Standing:
    if "pull_request" in issue or issue.get("number") in rules.skip_issues:
        return Standing("not_a_proposal")

    labelled = _labelled(events)
    flags: List[str] = []
    by_others = [r for r in labelled if r["actor"] not in rules.allowed_deciders and (r["label"] in rules.decision_labels or r["label"] == rules.triage_label)]
    if by_others:
        flags.append("decision_label_from_other_account_ignored")
    allowed = [r for r in labelled if r["actor"] in rules.allowed_deciders]
    triage = next((r for r in allowed if r["label"] == rules.triage_label), None)
    decisions = [r for r in allowed if r["label"] in rules.decision_labels]
    slips = _slips(decisions, _labelled(events, "unlabeled"), rules)
    if slips:
        flags.append("decision_label_corrected_at_once")
        decisions = [r for r in decisions if not any(r is slip for slip in slips)]
    first = decisions[0] if decisions else None

    # 1. An administrative closure, unless a decision was recorded before it.
    if triage and (first is None or triage["at"] <= first["at"]):
        return Standing("excluded", "administrative_triage", triage["label"], triage["actor"], triage["at"], flags=flags)

    # 2. Nothing decided: never touched.
    if first is None:
        return Standing("undecided", flags=flags)

    if triage:
        flags.append("administrative_closure_after_decision")
    if any(r["label"] != first["label"] for r in decisions[1:]):
        flags.append("later_decision_round")

    # 3. Self-decided: the issue's author, or the login in its first line, is the account that decided it.
    author = (issue.get("user") or {}).get("login")
    _, submitter = first_line_login(issue.get("body") or "")
    self_decided = first["actor"] in (author, submitter)
    standing = Standing("decided", None, first["label"], first["actor"], first["at"], self_decided, flags)
    if self_decided and not (rules.reconcile_self_decided or named_in_manual_run):
        standing.status, standing.reason = "excluded", "self_decided"
    return standing


def proposal_kind(issue: Dict, rules: Rules, submission_bot: str) -> Optional[str]:
    """Is this issue a proposal the reviewer looks at? "bot", "own" or None.

    "bot": opened by the submission bot for a contributor. Only these are reviewed without being asked for, because
    only the bot's issues have been through sign-in, the template and the blind review. An issue typed straight into
    GitHub by anyone else is never reviewed, whatever it looks like.
    "own": the maintainer's own, opened directly or sent through the form. Left out unless a manual run names it.
    """
    if "pull_request" in issue or issue.get("number") in rules.skip_issues:
        return None
    author = issue.get("user") or {}
    shape, submitter = first_line_login(issue.get("body") or "")
    if author.get("login") == submission_bot and author.get("type") == "Bot" and shape == "bot":
        return "own" if submitter in rules.allowed_deciders else "bot"
    if author.get("login") in rules.allowed_deciders and shape == "maintainer":
        return "own"
    return None
