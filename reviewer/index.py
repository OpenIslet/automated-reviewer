"""The prior-decisions index: every earlier proposal, with the outcome it had when this one was submitted.

Built from label events as they stood at this proposal's submission time, never from the labels an issue carries now,
which may include later decisions and reversals. Proposals still undecided at that moment are listed as such.

Titles and claims are the contributors' own words, cut to a length and put on one line. No model writes anything here.
The lines are untrusted text and go inside the untrusted part of the input.
"""
import re
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

from .decisions import proposal_kind
from .issuebody import Unparsed, clean_title, parse_body

TITLE_LIMIT = 150
SUMMARY_LIMIT = 300
PENDING = "not yet decided"
HEADER = "Earlier proposals, as they stood when this one was submitted. Each line is: number | type | outcome at that time | title | claim. Titles and claims are the contributors' own words."
NONE_YET = "No earlier proposal had been submitted when this one was."


@dataclass(frozen=True)
class IndexLine:
    number: int
    title: str
    kind: Optional[str]
    outcome: Optional[str]  # an outcome key, or None while undecided
    summary: Optional[str]


@dataclass
class Index:
    lines: Tuple[IndexLine, ...]
    text: str
    numbers: FrozenSet[int]


def one_line(text: Optional[str], limit: int) -> str:
    """Contributor text made safe for one cell of one line: no line breaks, no column marks, no longer than `limit`."""
    flat = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f  ]", " ", text or "")).replace("|", "/").strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def label_standing_at(events: List[Dict], rules, as_of: str) -> Tuple[Optional[str], bool]:
    """(the decision label standing just before `as_of`, whether the issue was administratively closed by then)."""
    standing: Dict[str, str] = {}
    rows = sorted((e for e in events or [] if e.get("event") in ("labeled", "unlabeled") and isinstance(e.get("created_at"), str)), key=lambda e: e["created_at"])
    for event in rows:
        if event["created_at"] >= as_of:
            break
        name = (event.get("label") or {}).get("name")
        if name not in rules.decision_labels and name != rules.triage_label:
            continue
        if event["event"] == "unlabeled":
            standing.pop(name, None)
        elif (event.get("actor") or {}).get("login") in rules.allowed_deciders:
            standing[name] = event["created_at"]
    if rules.triage_label in standing:
        return None, True
    if not standing:
        return None, False
    return max(standing.items(), key=lambda item: item[1])[0], False


def build_index(target: Dict, everything: List[Tuple[Dict, List[Dict]]], settings, submitted_for: Callable) -> Index:
    """`everything` is every issue with its events. `submitted_for(issue, events)` gives a Submitted for one of them."""
    rules = settings.rules()
    as_of = target["created_at"]
    lines: List[IndexLine] = []
    for issue, events in everything:
        number = issue.get("number")
        if number == target.get("number"):
            continue
        if not isinstance(issue.get("created_at"), str) or issue["created_at"] >= as_of:
            continue
        if proposal_kind(issue, rules, settings.submission_bot) is None:
            continue  # a bug report, a pull request, or text that someone typed in by hand to look like a proposal
        label, closed = label_standing_at(events, rules, as_of)
        if closed:
            continue
        outcome = settings.outcome_for_label(label).key if label else None
        submitted = submitted_for(issue, events)
        title = one_line(clean_title(submitted.title), TITLE_LIMIT)
        kind = summary = None
        if submitted.text is not None:
            try:
                parsed = parse_body(submitted.text)
                kind = parsed.kind
                claim = submitted.marker.claim if submitted.marker and submitted.marker.claim else parsed.claim
                summary = one_line(claim, SUMMARY_LIMIT) or None
            except Unparsed:
                pass
        lines.append(IndexLine(number, title, kind, outcome, summary))
    lines.sort(key=lambda line: line.number)

    if not lines:
        return Index((), NONE_YET, frozenset())
    rows = [HEADER]
    for line in lines:
        outcome_name = settings.outcome(line.outcome).name if line.outcome else PENDING
        rows.append(f"#{line.number} | {line.kind or 'type not stated'} | {outcome_name} | {line.title or 'no title'} | {line.summary or 'no claim stated'}")
    return Index(tuple(lines), "\n".join(rows), frozenset(line.number for line in lines))
