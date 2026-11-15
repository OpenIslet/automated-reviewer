"""The guards that are checked before any model call. All of them count from the log, so they hold across runs.

- The kill switch: a repository variable. Unless it says "true", a run does nothing at all.
- A limit on proposals per run and per UTC day. Fixture runs do not count towards the daily one.
- Monthly limits for each provider: calls, input tokens and output tokens. Fixture runs do count.

Only an explicit override on a manual run lifts the limits. Nothing lifts the kill switch.
Sizes are in calls and tokens.
"""
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional

KILL_SWITCH = "AUTOMATED_REVIEWER_ENABLED"


def enabled(env) -> bool:
    return str((env or {}).get(KILL_SWITCH, "")).strip().lower() == "true"


@dataclass
class ProviderUse:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Usage:
    """What has been used so far, read from the log's records and added to as this run goes."""

    day: str  # the UTC day of this run, YYYY-MM-DD
    proposals_today: int = 0
    proposals_this_run: int = 0
    month: Dict[str, ProviderUse] = field(default_factory=dict)  # by provider, for the UTC month of this run
    turned_down_this_run: Dict[str, int] = field(default_factory=dict)  # by model slot: requests the provider would not take

    def add_turned_down(self, slot: str) -> int:
        self.turned_down_this_run[slot] = self.turned_down_this_run.get(slot, 0) + 1
        return self.turned_down_this_run[slot]

    def provider(self, name: str) -> ProviderUse:
        return self.month.setdefault(name, ProviderUse())

    def add_call(self, provider: str, input_tokens: int, output_tokens: int) -> None:
        use = self.provider(provider)
        use.calls += 1
        use.input_tokens += int(input_tokens or 0)
        use.output_tokens += int(output_tokens or 0)


def usage_from(records: Iterable[dict], now: str) -> Usage:
    """`now` is an ISO time in UTC. Each slot result in a record carries the times and sizes of its own calls."""
    usage = Usage(day=now[:10])
    month = now[:7]
    for record in records:
        first_reviewed = record.get("first_reviewed_at") or ""
        if record.get("kind") == "proposal" and record.get("calls_made") and first_reviewed[:10] == usage.day:
            usage.proposals_today += 1
        for stage in ("gate", "panel"):
            for slot in ((record.get(stage) or {}).get("slots") or []):
                for call in slot.get("calls") or []:
                    if str(call.get("at") or "")[:7] == month:
                        usage.add_call(slot.get("provider") or "", call.get("input_tokens"), call.get("output_tokens"))
    return usage


class Limits:
    def __init__(self, caps, override: bool = False):
        self._caps = caps
        self._override = bool(override)

    def may_start_proposal(self, usage: Usage, counts_towards_day: bool = True) -> Optional[str]:
        """None if another proposal may be reviewed now; otherwise the name of the limit that says no."""
        if self._override:
            return None
        if usage.proposals_this_run >= self._caps.proposals_per_run:
            return "per_run_limit"
        if counts_towards_day and usage.proposals_today >= self._caps.proposals_per_day:
            return "daily_cap"
        return None

    def may_call(self, usage: Usage, provider: str, input_tokens: int, max_output_tokens: int) -> Optional[str]:
        """None if one more call to this provider fits inside its monthly limits; otherwise the limit that says no."""
        if self._override:
            return None
        use = usage.provider(provider)
        if use.calls + 1 > self._caps.monthly_calls:
            return "monthly_calls"
        if use.input_tokens + input_tokens > self._caps.monthly_input_tokens:
            return "monthly_input_tokens"
        if use.output_tokens + max_output_tokens > self._caps.monthly_output_tokens:
            return "monthly_output_tokens"
        return None


TEMPLATE_FOLDER = ".github/ISSUE_TEMPLATE/"


def template_that_applies_a_decision_label(source, settings, now: str) -> Optional[str]:
    """The path of an issue template in the source repository that names a decision label, or None.

    Outside contributors can only apply labels through an issue template. If a template ever named a decision label
    or the closure label, anyone could open an issue that carries one. The run stops rather than read such labels.
    Any mention of a label in a template counts, wherever it is in the file.
    """
    head = source.commit_before(now)
    if head is None:
        return None
    watched = sorted(settings.decision_labels | {settings.administrative_closure_label})
    for path in sorted(source.tree(head["sha"])):
        if path.startswith(TEMPLATE_FOLDER):
            text = source.file_at(head["sha"], path).decode("utf-8", errors="replace")
            if any(label in text for label in watched):
                return path
    return None
