"""From one issue to what the models are sent, or to the reason it is left out. No model is called here.

The order is fixed (docs/SPEC.md, section 3):

1. Whether the issue is a proposal at all, an administrative closure and the maintainer's own proposals are
   recognised from who opened the issue and from label events alone. A proposal does not have to be decided: it is
   reviewed soon after it is submitted.
2. Then the text is confirmed as submitted, and parsed by its structure.
3. Then the context at the commit before submission, the cited passages, the evidence lookup, the bot's checks and the
   index of earlier proposals are gathered, and the two inputs are assembled.

A failure to read GitHub is not an exclusion: it raises, and the proposal is tried again on the next run.
"""
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import excerpts as excerpts_module
from .assemble import Assembled, Prompts, assemble_gate, assemble_panel
from .checks import OtherChecks, other_checks
from .context import Context, ContextReader, ContextUnavailable
from .decisions import Standing, proposal_kind, standing_of
from .evidence import EvidenceReader, Lookup
from .index import Index, build_index, one_line
from .issuebody import ParsedBody, Unparsed, clean_title, parse_body
from .submitted import Submitted, as_submitted

TITLE_LIMIT = 300
SAME_AS = re.compile(r"^\s*Same as:\s*IIP-0*(\d{1,6})\s*$", re.IGNORECASE | re.MULTILINE)
# The wording used before the "Same as" line was agreed (issue 14). A link or bold marks may sit round the number.
SAME_AS_FALLBACK = re.compile(r"\bfirst in\W{0,6}IIP-0*(\d{1,6})\b", re.IGNORECASE)


@dataclass
class Excluded:
    number: int
    reason: str  # administrative_triage | self_decided | text_unverified | unparsed | context_unavailable
    detail: Optional[str]
    standing: Standing


@dataclass
class Prepared:
    number: int
    standing: Standing
    submitted: Submitted
    parsed: ParsedBody
    title: str
    context: Context
    excerpts: object
    evidence: Lookup
    checks: OtherChecks
    index: Index
    gate: Assembled
    panel: Assembled
    flags: List[str] = field(default_factory=list)


class Readers:
    """Everything one run reads from GitHub, kept so that nothing is fetched twice."""

    def __init__(self, source, settings):
        self.source = source
        self.contexts = ContextReader(source, settings.context_files)
        self.evidence = EvidenceReader(source, settings.evidence_places, settings.corpus_list)
        self._submitted: Dict[int, Submitted] = {}

    def submitted(self, issue: Dict, events: List[Dict]) -> Submitted:
        number = issue["number"]
        if number not in self._submitted:
            self._submitted[number] = as_submitted(issue, events, self.source)
        return self._submitted[number]


def same_as_named(comments: List[Dict], standing: Standing) -> Tuple[Optional[int], Optional[str]]:
    """The earlier proposal the maintainer named when closing one as already covered: (number, how it was found).

    Read from the decider's own comments posted up to the decision. This is ground truth for the scoreboard and is
    never part of any input.
    """
    own = [c for c in comments or [] if (c.get("user") or {}).get("login") == standing.actor and isinstance(c.get("created_at"), str)]
    own = sorted((c for c in own if c["created_at"] <= (standing.at or "")), key=lambda c: c["created_at"], reverse=True)
    for pattern, how in ((SAME_AS, "same_as_line"), (SAME_AS_FALLBACK, "fallback_wording")):
        for comment in own:
            found = pattern.search(comment.get("body") or "")
            if found:
                return int(found.group(1)), how
    return None, None


def prepare(
    issue: Dict,
    events: List[Dict],
    comments: List[Dict],
    everything: List[Tuple[Dict, List[Dict]]],
    readers: Readers,
    settings,
    prompts: Prompts,
    named_in_manual_run: bool = False,
    new_token: Optional[Callable[[], str]] = None,
):
    """Returns None (not a proposal: never touched), an Excluded, or a Prepared. The proposal may be undecided."""
    rules = settings.rules()
    kind = proposal_kind(issue, rules, settings.submission_bot)
    if kind is None:
        return None
    standing = standing_of(issue, events, rules, named_in_manual_run)
    if standing.status == "not_a_proposal":
        return None
    number = issue["number"]
    if standing.status == "excluded" and standing.reason == "administrative_triage":
        return Excluded(number, standing.reason, None, standing)
    if kind == "own" or standing.self_decided or standing.reason == "self_decided":
        standing.self_decided = True
        if not (named_in_manual_run or rules.reconcile_self_decided):
            return Excluded(number, "self_decided", None, standing)

    submitted = readers.submitted(issue, events)
    if submitted.text is None:
        return Excluded(number, "text_unverified", submitted.why, standing)
    try:
        parsed = parse_body(submitted.text)
    except Unparsed as refused:
        return Excluded(number, "unparsed", str(refused), standing)

    flags = list(standing.flags) + list(submitted.flags) + list(parsed.notes)
    if standing.at and submitted.edited_before(standing.at):
        flags.append("edited_before_decision")
    if standing.self_decided:
        flags.append("self_decided")

    try:
        context = readers.contexts.context_for(issue["created_at"])
        cited = excerpts_module.build(
            parsed.draft_for_models, context, readers.contexts, settings.excerpt_lines_either_side, settings.excerpt_lines_when_no_line_is_given, settings.excerpt_max_tokens
        )
        evidence = readers.evidence.lookup(submitted.marker.pmids if submitted.marker else (), parsed.draft_for_models, context)
    except ContextUnavailable as unavailable:
        if unavailable.transient:
            raise
        return Excluded(number, "context_unavailable", str(unavailable), standing)
    flags += context.notes

    checks = other_checks(issue, comments, settings.checks_comment, settings.submission_bot)
    index = build_index(issue, everything, settings, readers.submitted)
    title = one_line(clean_title(submitted.title), TITLE_LIMIT)
    gate = assemble_gate(prompts, title, parsed, new_token)
    panel = assemble_panel(prompts, settings, context, cited, evidence, index, checks, title, parsed, new_token)
    if gate.collisions or panel.collisions:
        flags.append("marker_token_collision")
    return Prepared(number, standing, submitted, parsed, title, context, cited, evidence, checks, index, gate, panel, flags)
