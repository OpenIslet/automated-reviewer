"""The two jobs.

`reconcile` is the review job: find every submitted proposal that has no complete result for the current policy
version and review them up to the limits, undecided ones first. It does not wait for the maintainer's decision. When
a decision comes later, the same job adds it to the record, without asking any model again. It reads GitHub and calls
models. It writes nothing to GitHub: its results go into a list that the caller saves to a file as the run goes.

`publish` is the other job: it holds no provider key, treats the results file as untrusted, and writes each record to
the log in the home repository. Nothing is ever written to the source repository.

A missed or failed run is harmless, because the next one picks up everything outstanding. A fault in one proposal
leaves that proposal for a later run and does not stop the others. Only a fault that a person has to fix, such as a
provider refusing the key, stops a run.
"""
import copy
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import caps, identity, record as records, scoreboard
from . import excerpts as excerpts_module
from .assemble import assemble_gate, assemble_panel
from .checks import OtherChecks
from .config import ConfigError
from .context import ContextUnavailable
from .decisions import proposal_kind, standing_of
from .github import GitHubError
from .index import build_index, label_standing_at, one_line
from .issuebody import Unparsed, clean_title, parse_body
from .models import ProviderError
from .pipeline import Excluded, Readers, prepare, same_as_named
from .review import review_stage


@dataclass
class RunSummary:
    status: str = "ran"  # "disabled" when the kill switch is off
    policy_version: Optional[str] = None
    reviewed: List[int] = field(default_factory=list)
    excluded: List[int] = field(default_factory=list)
    decisions_recorded: List[int] = field(default_factory=list)  # reviewed earlier; the maintainer's decision came since
    labels_updated: List[int] = field(default_factory=list)  # decided earlier; its labels changed since
    already_done: int = 0
    left_for_later: Dict[int, str] = field(default_factory=dict)
    stopped_by: Optional[str] = None


# ---- the log -----------------------------------------------------------------------------------------------------------

def log_issues(settings) -> List[int]:
    """Every log issue, oldest first. Records are read from all of them and new ones are written to the last."""
    return list(settings.earlier_log_issues) + ([settings.log_issue] if settings.log_issue is not None else [])


def read_log(home, settings) -> List[dict]:
    """Every comment in every log issue. Each is given the number of the issue it is in, as "log_issue"."""
    found: List[dict] = []
    for number in log_issues(settings):
        found.extend(dict(comment, log_issue=number) for comment in home.comments(number))
    return found


# ---- reading the source repository -------------------------------------------------------------------------------------

def carried_labels(issue: dict, settings) -> List[str]:
    """The decision and closure labels an issue carries now, in a fixed order."""
    watched = settings.decision_labels | {settings.administrative_closure_label}
    return sorted({label.get("name") for label in issue.get("labels") or [] if isinstance(label, dict)} & watched)


def _proposal_rows(source, settings) -> List:
    """Every proposal the reviewer recognises, oldest first, each with its events.

    The events are read for every one, decided or not: the title as submitted is the one before the first rename,
    and a rename can happen at any time.
    """
    rules = settings.rules()
    rows = []
    for issue in source.list_issues():
        if proposal_kind(issue, rules, settings.submission_bot) is not None:
            rows.append((issue, source.events(issue["number"])))
    rows.sort(key=lambda row: (row[0].get("created_at") or "", row[0]["number"]))
    return rows


def _record_decision(current: dict, issue: dict, standing, comments: List[dict], rows: List, settings, submitted=None) -> None:
    """Writes the maintainer's decision into a record, with what is known about it at that moment."""
    same_as = how = decided_then = None
    outcome = settings.outcome_for_label(standing.label)
    if outcome is not None and outcome.key == "already_covered":
        same_as, how = same_as_named(comments, standing)
        if same_as is not None:  # was the earlier proposal the maintainer named already decided when this one was submitted?
            named_events = next((events for other, events in rows if other["number"] == same_as), [])
            decided_then = label_standing_at(named_events, settings.rules(), issue.get("created_at") or "")[0] is not None
    current["decision"] = records.decision_of(standing, same_as, how, decided_then)
    current["reviewed_before_decision"] = records.posted_before_decision(current)  # the publish job works this out again
    flags = set(current["text"]["flags"]) | set(standing.flags)
    if submitted is not None and standing.at and submitted.edited_before(standing.at):
        flags.add("edited_before_decision")
    current["text"]["flags"] = sorted(flags)


def _nothing_happened(record: dict) -> bool:
    """True when no request reached any model and there is nothing to say about any of them.

    A record whose models are waiting for a provider key or for next month's limit is not this: it says why they are
    waiting, and the quick look reads it so as not to start a run every hour for nothing.
    """
    slots = [slot for stage in ("gate", "panel") for slot in record[stage]["slots"]]
    return not any(slot.get("calls") for slot in slots) and all(slot["status"] == "pending" and slot["reason"] in (None, "transient_error") for slot in slots)


def _only_waiting(record: dict, now: Optional[str]) -> bool:
    """True when all that is missing waits on something a run cannot change: a provider with no key, or this month's limit."""
    reasons = [slot["reason"] for stage in ("gate", "panel") for slot in record[stage]["slots"] if slot["status"] == "pending"]
    reasons += [slot["second"].get("reason") for slot in record["panel"]["slots"] if (slot.get("second") or {}).get("status") == "pending"]
    if not reasons or not record["panel"]["slots"]:  # nothing is waiting, or the review stopped before the panel was reached
        return False
    same_month = bool(now) and record["updated_at"][:7] == now[:7]
    return all(reason == "no_adapter" or (same_month and str(reason or "").startswith("monthly_")) for reason in reasons)


def pending_work(settings, prompts, source, log_comments: List[dict], env, now: Optional[str] = None, numbers: Optional[List[int]] = None) -> int:
    """How many proposals a run would have something to do for. Cheap: one list of issues, no model, no events.

    It counts what `reconcile` would act on and nothing else: a proposal with no record, one whose record is not
    complete (unless all it waits for is a provider key or next month), and one whose decision or closure labels are
    not the ones its record last saw. A manual run that names a proposal always has work for it.
    """
    if not caps.enabled(env):
        return 0
    policy = identity.policy_version(settings, prompts)
    known = {records.key(r): r for _, r in records.trusted_records(log_comments, settings)}
    rules = settings.rules()
    count = 0
    for issue in source.list_issues():
        if proposal_kind(issue, rules, settings.submission_bot) is None:
            continue
        if numbers is not None:
            count += issue["number"] in numbers
            continue
        earlier = known.get(("proposal", issue["number"], policy))
        if earlier is None:
            count += 1
        elif earlier["exclusion"] is None and carried_labels(issue, settings) != earlier["labels_seen"]:
            count += 1  # a decision, or another change to its labels, to record
        elif not earlier["complete"]:
            count += not _only_waiting(earlier, now)
    return count


# ---- the review job ----------------------------------------------------------------------------------------------------

def reconcile(
    settings,
    prompts,
    source,
    log_comments: List[dict],
    adapters: Dict,
    env,
    now: str,
    results: List[dict],
    numbers: Optional[List[int]] = None,
    override_caps: bool = False,
    dry_run: bool = False,
    new_token: Optional[Callable[[], str]] = None,
    checkpoint: Optional[Callable[[], None]] = None,
) -> RunSummary:
    """Appends each new or updated record to `results` as soon as it exists, so a failure part-way loses nothing.

    `checkpoint`, when given, is called after each proposal, so the caller can save `results` as the run goes.
    """
    if not caps.enabled(env):
        return RunSummary(status="disabled")  # the kill switch: nothing is read and nothing is called
    policy = identity.policy_version(settings, prompts)
    summary = RunSummary(policy_version=policy)
    if caps.template_that_applies_a_decision_label(source, settings, now):
        summary.status = "unsafe_issue_template"  # a decision label could be applied by anyone: stop before reading any
        return summary
    trusted = records.trusted_records(log_comments, settings)
    known = {records.key(r): r for _, r in trusted}
    usage = caps.usage_from([r for _, r in trusted], now)
    limits = caps.Limits(settings.caps, override_caps)
    rules = settings.rules()
    rows = _proposal_rows(source, settings)
    readers = Readers(source, settings)

    def one(issue: dict, events: List[dict], named: bool) -> None:
        number = issue["number"]
        kind = proposal_kind(issue, rules, settings.submission_bot)
        standing = standing_of(issue, events, rules, named)
        seen = carried_labels(issue, settings)
        earlier = known.get(("proposal", number, policy))
        was_left_out_as_own = earlier is not None and ((earlier["exclusion"] or {}).get("reason") == "self_decided" or earlier["stopped"] == "self_decided")
        own_named_again = was_left_out_as_own and named
        left_out = (standing.status == "excluded" and standing.reason == "administrative_triage") or (not named and not rules.reconcile_self_decided and (kind == "own" or standing.reason == "self_decided"))

        def note_changes() -> bool:
            """Adds to the record a decision made since it was written, and any change to its labels. Asks no model."""
            updated = copy.deepcopy(earlier)
            recorded = updated["decision"]
            # A decision is recorded once. The one case where it is recorded again: a run read a label that was a slip
            # of the hand, before the maintainer had put it right (see decisions.standing_of).
            decided_since = bool(standing.label) and (recorded is None or (recorded["label"], recorded["at"]) != (standing.label, standing.at))
            if decided_since:
                said = source.comments(number) if standing.status == "decided" else []
                _record_decision(updated, issue, standing, said, rows, settings, readers.submitted(issue, events))
            flags = sorted(set(updated["text"]["flags"]) | set(standing.flags))
            if not decided_since and set(flags) == set(updated["text"]["flags"]) and seen == updated["labels_seen"]:
                return False
            updated["text"]["flags"], updated["labels_seen"], updated["updated_at"] = flags, seen, now
            results.append(records.finish(updated, settings))
            (summary.decisions_recorded if decided_since else summary.labels_updated).append(number)
            return True

        if earlier is not None and earlier["complete"] and not own_named_again:
            # Reviewed on an earlier run. Only what has changed about the decision or the labels is added.
            if earlier["exclusion"] is not None or not note_changes():
                summary.already_done += 1
            return

        if not left_out:
            blocked = limits.may_start_proposal(usage)
            if blocked:  # the review waits; the loop goes on, because recording a decision asks no model
                if earlier is not None and earlier["exclusion"] is None:
                    note_changes()  # a decision made meanwhile is recorded on what there is so far
                summary.left_for_later[number] = blocked
                summary.stopped_by = blocked
                return

        comments = [] if left_out else source.comments(number)
        prepared = prepare(issue, events, comments, rows, readers, settings, prompts, named, new_token)
        if prepared is None:
            return

        if isinstance(prepared, Excluded):
            if earlier is not None and earlier["exclusion"] is None and earlier["calls_made"]:
                # Models were already asked about this one on an earlier run. Their answers and the calls stay in the
                # log, so the limits still count them; the review stops here.
                kept = copy.deepcopy(earlier)
                kept.update(stopped=prepared.reason, updated_at=now, labels_seen=seen)
                if prepared.standing.label and kept["decision"] is None:
                    kept["decision"] = records.decision_of(prepared.standing)
                results.append(records.finish(kept, settings))
            else:
                excluded = records.new_record("proposal", number, policy, now, dry_run)
                excluded["decision"] = records.decision_of(prepared.standing) if prepared.standing.label else None
                excluded["submitted_at"] = issue.get("created_at")
                excluded["self_decided"] = prepared.standing.self_decided
                excluded["exclusion"] = {"reason": prepared.reason, "detail": prepared.detail}
                results.append(records.finish(excluded, settings))
            summary.excluded.append(number)
            return

        fresh = earlier is None or earlier["exclusion"] is not None
        current = records.new_record("proposal", number, policy, now, dry_run) if fresh else copy.deepcopy(earlier)
        current.update(updated_at=now, dry_run=bool(dry_run), self_decided=prepared.standing.self_decided, submitted_at=issue.get("created_at"), stopped=None, labels_seen=seen)
        current["text"] = {"sha256": prepared.panel.proposal_sha256, "confirmed": prepared.submitted.how, "flags": list(prepared.flags)}
        current["context"] = {"commit": prepared.context.commit, "evidence_source": prepared.evidence.source, "checks_included": prepared.checks.text is not None, "index_size": len(prepared.index.numbers)}
        current["contributor"] = {"ai_draft": prepared.parsed.ai_draft, "ai_review": prepared.parsed.ai_review, "blind_review_verdict": prepared.parsed.review_verdict}
        current["panel"]["second_opinion"] = number % settings.self_consistency_every == 0
        calls_before = sum(len(slot.get("calls") or []) for stage in ("gate", "panel") for slot in current[stage]["slots"])
        results.append(current)
        try:
            review_stage("gate", settings.gate, prepared.gate, current["gate"]["slots"], adapters, settings, limits, usage, now, question=prompts.gate_question)
            review_stage("panel", settings.panel, prepared.panel, current["panel"]["slots"], adapters, settings, limits, usage, now, index_numbers=prepared.index.numbers, second_opinion=current["panel"]["second_opinion"])
        finally:
            calls_now = sum(len(slot.get("calls") or []) for stage in ("gate", "panel") for slot in current[stage]["slots"])
            if calls_now > calls_before:
                usage.proposals_this_run += 1
                if current["first_reviewed_at"] is None:
                    current["first_reviewed_at"] = now
                    usage.proposals_today += 1
            if prepared.standing.label:  # already decided when it was reviewed, as the whole back catalogue is
                _record_decision(current, issue, prepared.standing, comments, rows, settings, prepared.submitted)
            records.finish(current, settings)
            nothing_happened = fresh and _nothing_happened(current)
            if nothing_happened:
                results[:] = [record for record in results if record is not current]  # no model was reached and nothing is known: a comment saying so would be noise
        if nothing_happened:
            summary.left_for_later[number] = "no_model_reached"
            return
        summary.reviewed.append(number)

    # Undecided proposals first, because the maintainer is waiting to read those verdicts. Oldest first within each.
    for issue, events in sorted(rows, key=lambda row: bool(carried_labels(row[0], settings))):
        number = issue["number"]
        named = numbers is not None and number in numbers
        if numbers is not None and not named:
            continue  # numbers given to a manual run only ever narrow the set
        try:
            one(issue, events, named)
        except ProviderError:
            raise  # a bad key or no credit: nothing more can be done until a person puts it right
        except (GitHubError, ContextUnavailable):
            summary.left_for_later[number] = "github_unreadable"
        except Exception as fault:  # one proposal's fault must not stop the others. Its kind is kept; its text never is.
            summary.left_for_later[number] = f"internal_error:{type(fault).__name__}"
        if checkpoint:
            checkpoint()
    return summary


def reconcile_fixtures(
    settings,
    prompts,
    source,
    log_comments: List[dict],
    adapters: Dict,
    env,
    now: str,
    results: List[dict],
    fixtures: List,
    override_caps: bool = False,
    dry_run: bool = False,
    new_token: Optional[Callable[[], str]] = None,
    checkpoint: Optional[Callable[[], None]] = None,
) -> RunSummary:
    """Fixture mode: runs every fixture that has no complete result for the current policy version.

    Fixtures count against the monthly limits and the per-run limit, and not against the daily one. Their results are
    records of kind "fixture" and are reported apart from real proposals.
    """
    if not caps.enabled(env):
        return RunSummary(status="disabled")
    policy = identity.policy_version(settings, prompts)
    summary = RunSummary(policy_version=policy)
    trusted = records.trusted_records(log_comments, settings)
    known = {records.key(r): r for _, r in trusted}
    usage = caps.usage_from([r for _, r in trusted], now)
    limits = caps.Limits(settings.caps, override_caps)
    readers = Readers(source, settings)
    everything: List = []  # the real proposals, read once and only if a fixture needs the index of earlier proposals

    def one(fixture) -> None:
        earlier = known.get(("fixture", fixture.id, policy))
        if earlier is not None and earlier["complete"]:
            summary.already_done += 1
            return
        current = copy.deepcopy(earlier) if earlier is not None else records.new_record("fixture", fixture.id, policy, now, dry_run)
        current.update(updated_at=now, dry_run=bool(dry_run), submitted_at=fixture.submitted_at)
        current["fixture"] = {"kind": fixture.kind, "expect_spam": fixture.expect_spam, "steers_towards": fixture.steers_towards}
        try:
            parsed = parse_body(fixture.text())
        except Unparsed as refused:
            current["exclusion"] = {"reason": "unparsed", "detail": str(refused)}
            results.append(records.finish(current, settings))
            summary.excluded.append(fixture.id)
            return
        blocked = limits.may_start_proposal(usage, counts_towards_day=False)
        if blocked:
            summary.left_for_later[fixture.id] = blocked
            summary.stopped_by = blocked
            return
        title = one_line(clean_title(fixture.title), 300)
        gate = assemble_gate(prompts, title, parsed, new_token)
        panel = index = None
        if fixture.kind == "adversarial":
            if not everything:
                everything.extend(_proposal_rows(source, settings))
            context = readers.contexts.context_at(fixture.context_commit)
            cited = excerpts_module.build(parsed.draft_for_models, context, readers.contexts, settings.excerpt_lines_either_side, settings.excerpt_lines_when_no_line_is_given, settings.excerpt_max_tokens)
            evidence = readers.evidence.lookup((), parsed.draft_for_models, context)
            index = build_index({"number": 0, "created_at": fixture.submitted_at}, everything, settings, readers.submitted)
            panel = assemble_panel(prompts, settings, context, cited, evidence, index, OtherChecks(None, "no_comment"), title, parsed, new_token)
            current["context"] = {"commit": context.commit, "evidence_source": evidence.source, "checks_included": False, "index_size": len(index.numbers)}
        current["text"] = {"sha256": gate.proposal_sha256, "confirmed": None, "flags": ["marker_token_collision"] if gate.collisions or (panel and panel.collisions) else []}
        calls_before = sum(len(slot.get("calls") or []) for stage in ("gate", "panel") for slot in current[stage]["slots"])
        results.append(current)
        try:
            review_stage("gate", settings.gate, gate, current["gate"]["slots"], adapters, settings, limits, usage, now, question=prompts.gate_question)
            if panel is not None:
                review_stage("panel", settings.panel, panel, current["panel"]["slots"], adapters, settings, limits, usage, now, index_numbers=index.numbers)
        finally:
            records.finish(current, settings)
            if sum(len(slot.get("calls") or []) for stage in ("gate", "panel") for slot in current[stage]["slots"]) > calls_before:
                usage.proposals_this_run += 1
                current["first_reviewed_at"] = current["first_reviewed_at"] or now
            nothing_happened = earlier is None and _nothing_happened(current)
            if nothing_happened:
                results[:] = [record for record in results if record is not current]
        if nothing_happened:
            summary.left_for_later[fixture.id] = "no_model_reached"
            return
        summary.reviewed.append(fixture.id)

    for fixture in fixtures:
        try:
            one(fixture)
        except ProviderError:
            raise
        except (GitHubError, ContextUnavailable):
            summary.left_for_later[fixture.id] = "github_unreadable"
        except Exception as fault:
            summary.left_for_later[fixture.id] = f"internal_error:{type(fault).__name__}"
        if checkpoint:
            checkpoint()
    return summary


# ---- the publish job ---------------------------------------------------------------------------------------------------

@dataclass
class PublishSummary:
    posted: int = 0
    edited: int = 0
    failed: int = 0  # GitHub would not take the comment, twice
    refused: Dict[str, int] = field(default_factory=dict)


def publish(raw_records: List, settings, home, now: str, pause: Optional[Callable[[], None]] = None) -> PublishSummary:
    """Writes each record to the log, as a new comment or over its own earlier one. Stand-in results are never posted.

    This job, and not the results file, says when a verdict was first posted: the first time any model's answer was
    in the comment, and the first time every model's first answer was. From those it follows whether the maintainer
    decided with the verdict, or part of it, in front of him. A record GitHub will not take is counted and the rest
    are still written.
    """
    if settings.log_issue is None:
        raise ConfigError("log_issue: missing")
    summary = PublishSummary()
    existing = {records.key(r): (comment_id, r) for comment_id, r in records.trusted_records(read_log(home, settings), settings)}
    for raw in raw_records if isinstance(raw_records, list) else []:
        try:
            clean = records.clean(raw, settings)
            if clean["dry_run"]:
                raise records.BadRecord("dry_run_result")
            before = existing.get(records.key(clean))
            was = before[1] if before else {}
            clean["first_verdict_posted_at"] = was.get("first_verdict_posted_at") or (now if records.shows_a_verdict(clean) else None)
            clean["posted_at"] = was.get("posted_at") or (now if records.first_answers_settled(clean) else None)
            clean["reviewed_before_decision"] = records.posted_before_decision(clean) if clean["decision"] else None
            text = records.render(clean, settings)
        except records.BadRecord as refused:
            summary.refused[str(refused)] = summary.refused.get(str(refused), 0) + 1
            continue
        written = None
        for attempt in (1, 2):
            try:
                written = home.update_comment(before[0], text) if before else home.create_comment(settings.log_issue, text)
                break
            except (GitHubError, ValueError):
                if attempt == 2:
                    break
                if pause:
                    pause()
                if not before:  # GitHub may have taken the comment and lost the answer: look before writing it again
                    try:
                        landed = {records.key(r): (comment_id, r) for comment_id, r in records.trusted_records(read_log(home, settings), settings)}
                        before = landed.get(records.key(clean))
                    except GitHubError:
                        pass
        if written is None:
            summary.failed += 1
            continue
        existing[records.key(clean)] = (before[0] if before else written["id"], clean)
        if before:
            summary.edited += 1
        else:
            summary.posted += 1
        if pause:
            pause()
    return summary


def standing_now(source, settings) -> Dict[int, dict]:
    """Every proposal that is decided today: its number -> {"label": the decision label it carries now, or None}.

    Read for the scoreboard only: for coverage, and to list decisions that changed after the review.
    """
    rules = settings.rules()
    found: Dict[int, dict] = {}
    for issue in source.list_issues():
        if proposal_kind(issue, rules, settings.submission_bot) is None or not carried_labels(issue, settings):
            continue
        events = source.events(issue["number"])
        if standing_of(issue, events, rules, named_in_manual_run=True).status in ("not_a_proposal", "undecided"):
            continue
        label, _closed = label_standing_at(events, rules, "9999-12-31T23:59:59Z")
        found[issue["number"]] = {"label": label}
    return found


def publish_scoreboard(settings, prompts, source, home, now: str, pause: Optional[Callable[[], None]] = None) -> dict:
    """Rebuilds the scoreboard from the log's trusted records and writes it. Returns the numbers, for the saved file.

    The issue body holds the current policy version. Each retired version gets one comment, written once.
    """
    if settings.log_issue is None or settings.scoreboard_issue is None:
        raise ConfigError("scoreboard_issue: missing")
    policy = identity.policy_version(settings, prompts)
    comments = read_log(home, settings)
    where = {comment["id"]: comment["log_issue"] for comment in comments}
    found_records = records.trusted_records(comments, settings)
    trusted = [r for _, r in found_records]
    decided = standing_now(source, settings)
    links = {r["proposal"]: f"https://github.com/{settings.home_repo}/issues/{where[comment_id]}#issuecomment-{comment_id}"
             for comment_id, r in found_records if r["kind"] == "proposal" and r["policy_version"] == policy}
    found = scoreboard.numbers(trusted, settings, policy, decided, now, links)
    home.set_issue_body(settings.scoreboard_issue, scoreboard.render(found, settings))
    written = "\n".join(
        c.get("body") or "" for c in home.comments(settings.scoreboard_issue) if (c.get("user") or {}).get("login") == settings.record_author and (c.get("user") or {}).get("type") == "Bot"
    )
    for retired in found["retired_versions"]:
        if scoreboard.RETIRED_MARK.format(policy=retired) in written:
            continue
        if pause:
            pause()
        home.create_comment(settings.scoreboard_issue, scoreboard.retired_comment(scoreboard.numbers(trusted, settings, retired, decided, now), settings))
    return found
