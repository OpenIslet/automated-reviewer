"""Loads the trial's settings and stops on anything it does not recognise.

Two files, in one folder: config.yml (settings) and outcomes.yml (the decision labels and what they mean).

The mode is checked first. Any value other than "shadow" stops the script before outcomes.yml is opened and before
anything else in config.yml is looked at.
"""
import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

import yaml

from .decisions import Rules

MODE = "shadow"
AUTHORITY = "shadow"
PROVIDERS = ("anthropic", "google", "openai")
GATE_KINDS = ("text", "probability")
EFFORTS = ("lowest", "low", "medium", "high", "xhigh")
CONTEXT_FILES = ("blueprint", "playbook", "idea_graveyard")
SLOTS_PER_STAGE = 3

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_LABEL = re.compile(r"^[a-z]+:[a-z0-9-]+$")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_SLOT = re.compile(r"^[a-z][a-z0-9-]*$")
_LOGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}(\[bot\])?$")
_PATH = re.compile(r"^[A-Za-z0-9_.*-]+(/[A-Za-z0-9_.*-]+)*$")


class ConfigError(Exception):
    """A setting is missing, unknown or not allowed. The message names the setting, never its value."""


class ModeRefused(ConfigError):
    """The mode is not "shadow"."""


@dataclass(frozen=True)
class Outcome:
    key: str
    label: str
    name: str
    meaning: str
    source: str  # "published" or "trial"


@dataclass(frozen=True)
class ModelSlot:
    slot: str
    stage: str  # "gate" or "panel"
    provider: str
    model: Optional[str]  # None until the adapter for it is written
    kind: str  # gate: "text" or "probability"; panel: "panel"
    effort: str
    max_output_tokens: int
    threshold: Optional[float] = None  # gate only
    max_input_tokens: int = 0  # by the script's own estimate


@dataclass(frozen=True)
class Caps:
    proposals_per_run: int
    proposals_per_day: int
    monthly_calls: int
    monthly_input_tokens: int
    monthly_output_tokens: int
    runs_before_error: int


@dataclass(frozen=True)
class EvidencePlace:
    name: str
    file: str
    per_study_folder: Optional[str] = None


@dataclass(frozen=True)
class ChecksComment:
    starts_with: str
    left_out_heading: str
    included_heading: str
    within_minutes: int
    included_rows: Tuple[str, ...]


@dataclass(frozen=True)
class Config:
    mode: str
    source_repo: str
    home_repo: str
    default_branch: str
    log_issue: Optional[int]
    scoreboard_issue: Optional[int]
    allowed_deciders: FrozenSet[str]
    submission_bot: str
    record_author: str
    skip_issues: FrozenSet[int]
    reconcile_self_decided: bool
    caps: Caps
    context_files: Tuple[Tuple[str, str], ...]  # (name, pattern), in the order they are sent
    excerpt_lines_either_side: int
    excerpt_lines_when_no_line_is_given: int
    excerpt_max_tokens: int
    evidence_places: Tuple[EvidencePlace, ...]
    corpus_list: Optional[str]
    checks_comment: ChecksComment
    gate: Tuple[ModelSlot, ...]
    panel: Tuple[ModelSlot, ...]
    self_consistency_every: int
    reasons_kept: int
    reason_max_characters: int
    authority_gate: str
    authority_outcomes: Tuple[Tuple[str, str], ...]
    outcomes: Tuple[Outcome, ...]
    administrative_closure_label: str
    freeze_policy_version: Optional[str] = None
    freeze_at: Optional[str] = None
    gate_threshold: float = 0.5
    earlier_log_issues: Tuple[int, ...] = ()  # log issues that are full or closed. Their records are still read.

    @property
    def decision_labels(self) -> FrozenSet[str]:
        return frozenset(o.label for o in self.outcomes)

    @property
    def outcome_keys(self) -> Tuple[str, ...]:
        return tuple(o.key for o in self.outcomes)

    @property
    def slots(self) -> Tuple[ModelSlot, ...]:
        return self.gate + self.panel

    def outcome_for_label(self, label: str) -> Optional[Outcome]:
        return next((o for o in self.outcomes if o.label == label), None)

    def outcome(self, key: str) -> Optional[Outcome]:
        return next((o for o in self.outcomes if o.key == key), None)

    def rules(self) -> Rules:
        return Rules(
            decision_labels=self.decision_labels,
            triage_label=self.administrative_closure_label,
            allowed_deciders=self.allowed_deciders,
            skip_issues=self.skip_issues,
            reconcile_self_decided=self.reconcile_self_decided,
        )

    def slots_without_a_model(self) -> List[str]:
        """Slots whose model ID is not set yet. A dry run does not need them; a real run does."""
        return [s.slot for s in self.slots if not s.model]

    def can_record(self) -> bool:
        """True once the maintainer has created the log and scoreboard issues and put their numbers here."""
        return self.log_issue is not None and self.scoreboard_issue is not None


# ---- reading ---------------------------------------------------------------------------------------------------------

class _Section:
    """One mapping in a settings file. Every key must be asked for, and no key may be left over."""

    def __init__(self, data, where: str):
        if not isinstance(data, dict):
            raise ConfigError(f"{where}: must be a list of settings")
        self._data = dict(data)
        self._where = where

    def _name(self, key: str) -> str:
        return f"{self._where}.{key}" if self._where else key

    def take(self, key: str, kind, optional: bool = False):
        if key not in self._data:
            raise ConfigError(f"{self._name(key)}: missing")
        value = self._data.pop(key)
        if value is None:
            if optional:
                return None
            raise ConfigError(f"{self._name(key)}: missing")
        wrong = isinstance(value, bool) != (kind is bool) if kind in (int, bool, float) else False
        if kind is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if wrong or not isinstance(value, kind):
            raise ConfigError(f"{self._name(key)}: wrong kind of value")
        return value

    def text(self, key: str, pattern=None, optional: bool = False) -> Optional[str]:
        value = self.take(key, str, optional)
        if value is None:
            return None
        if not value.strip() or "\n" in value or value != value.strip():
            raise ConfigError(f"{self._name(key)}: must be one line of text")
        if pattern is not None and not pattern.match(value):
            raise ConfigError(f"{self._name(key)}: not in the expected form")
        return value

    def count(self, key: str, optional: bool = False, least: int = 1) -> Optional[int]:
        value = self.take(key, int, optional)
        if value is not None and value < least:
            raise ConfigError(f"{self._name(key)}: too small")
        return value

    def choice(self, key: str, allowed) -> str:
        value = self.take(key, str)
        if value not in allowed:
            raise ConfigError(f"{self._name(key)}: not one of the allowed values")
        return value

    def section(self, key: str) -> "_Section":
        return _Section(self.take(key, dict), self._name(key))

    def items(self, key: str) -> list:
        value = self.take(key, list)
        if not value:
            raise ConfigError(f"{self._name(key)}: empty")
        return value

    def done(self) -> None:
        if self._data:
            raise ConfigError(f"{self._name(sorted(self._data)[0])}: not a known setting")


def _yaml(path: Path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _path_pattern(section: _Section, key: str, optional: bool = False) -> Optional[str]:
    value = section.text(key, _PATH, optional)
    if value is None:
        return None
    folder, _, name = value.rpartition("/")
    if ".." in value.split("/") or "*" in folder or name.count("*") > 1:
        raise ConfigError(f"{section._name(key)}: not in the expected form")
    return value


def _outcomes(raw) -> Tuple[Tuple[Outcome, ...], str]:
    top = _Section(raw, "outcomes.yml")
    found: List[Outcome] = []
    for i, item in enumerate(top.items("outcomes")):
        entry = _Section(item, f"outcomes.yml.outcomes[{i}]")
        found.append(
            Outcome(
                key=entry.text("key", _KEY),
                label=entry.text("label", _LABEL),
                name=entry.text("name"),
                meaning=entry.text("meaning"),
                source=entry.choice("source", ("published", "trial")),
            )
        )
        entry.done()
    closure = top.text("administrative_closure_label", _LABEL)
    top.done()
    if len({o.key for o in found}) != len(found) or len({o.label for o in found}) != len(found):
        raise ConfigError("outcomes.yml.outcomes: a key or a label is repeated")
    if len(found) < 2:
        raise ConfigError("outcomes.yml.outcomes: too few")
    if closure in {o.label for o in found}:
        raise ConfigError("outcomes.yml.administrative_closure_label: is also a decision label")
    return tuple(found), closure


def _slots(section: _Section, stage: str, effort: str, max_output_tokens: int, threshold: Optional[float], max_input_tokens: int) -> Tuple[ModelSlot, ...]:
    slots: List[ModelSlot] = []
    for i, item in enumerate(section.items("models")):
        entry = _Section(item, f"{stage}.models[{i}]")
        slot_effort = entry.choice("effort", EFFORTS) if "effort" in entry._data else effort
        slots.append(
            ModelSlot(
                slot=entry.text("slot", _SLOT),
                stage=stage,
                provider=entry.choice("provider", PROVIDERS),
                model=entry.text("model", optional=True),
                kind=entry.choice("kind", GATE_KINDS) if stage == "gate" else "panel",
                effort=slot_effort,
                max_output_tokens=max_output_tokens,
                threshold=None,
                max_input_tokens=max_input_tokens,
            )
        )
        entry.done()
    # The threshold belongs only to a slot that answers with a probability and no text.
    slots = [dataclasses.replace(s, threshold=threshold) if s.kind == "probability" else s for s in slots]
    if len(slots) != SLOTS_PER_STAGE:
        raise ConfigError(f"{stage}.models: there must be exactly {SLOTS_PER_STAGE}")
    return tuple(slots)


def _config(raw, outcomes: Tuple[Outcome, ...], closure: str) -> Config:
    top = _Section(raw, "")
    mode = top.take("mode", str)

    source_repo = top.text("source_repo", _REPO)
    home_repo = top.text("home_repo", _REPO)
    if source_repo.lower() == home_repo.lower():
        raise ConfigError("home_repo: must not be the source repository")
    default_branch = top.text("default_branch")
    log_issue = top.count("log_issue", optional=True)
    scoreboard_issue = top.count("scoreboard_issue", optional=True)
    if log_issue is not None and log_issue == scoreboard_issue:
        raise ConfigError("scoreboard_issue: must not be the log issue")
    earlier_logs = top.take("earlier_log_issues", list)
    if not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in earlier_logs) or len(set(earlier_logs)) != len(earlier_logs):
        raise ConfigError("earlier_log_issues: must be issue numbers, each once")
    if earlier_logs and (log_issue is None or log_issue in earlier_logs or scoreboard_issue in earlier_logs):
        raise ConfigError("earlier_log_issues: must not hold the current log issue or the scoreboard issue")

    freeze_policy = freeze_at = None
    if top.take("freeze", dict, optional=True) is not None:
        frozen = _Section(raw["freeze"], "freeze")
        freeze_policy = frozen.text("policy_version", re.compile(r"^[0-9a-f]{64}$"))
        freeze_at = frozen.text("at", re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"))
        frozen.done()

    deciders = top.items("allowed_deciders")
    if not all(isinstance(d, str) and _LOGIN.match(d) for d in deciders):
        raise ConfigError("allowed_deciders: not in the expected form")
    submission_bot = top.text("submission_bot", _LOGIN)
    record_author = top.text("record_author", _LOGIN)
    if submission_bot in deciders or record_author in deciders:
        raise ConfigError("allowed_deciders: a bot account must not be a decider")
    skip = top.take("skip_issues", list)
    if not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in skip):
        raise ConfigError("skip_issues: must be issue numbers")
    reconcile_self_decided = top.take("reconcile_self_decided", bool)

    caps_section = top.section("caps")
    monthly = caps_section.section("monthly_per_provider")
    caps = Caps(
        proposals_per_run=caps_section.count("proposals_per_run"),
        proposals_per_day=caps_section.count("proposals_per_day"),
        monthly_calls=monthly.count("calls"),
        monthly_input_tokens=monthly.count("input_tokens"),
        monthly_output_tokens=monthly.count("output_tokens"),
        runs_before_error=caps_section.count("runs_before_error"),
    )
    monthly.done()
    caps_section.done()

    context = top.section("context")
    files = context.section("files")
    context_files = tuple((name, _path_pattern(files, name)) for name in CONTEXT_FILES)
    files.done()
    excerpts = context.section("excerpts")
    either_side = excerpts.count("lines_either_side", least=0)
    no_line = excerpts.count("lines_when_no_line_is_given")
    excerpt_max = excerpts.count("max_tokens")
    excerpts.done()
    places: List[EvidencePlace] = []
    for i, item in enumerate(context.items("evidence_places")):
        entry = _Section(item, f"context.evidence_places[{i}]")
        name = entry.text("name", _KEY)
        file = _path_pattern(entry, "file")
        folder = _path_pattern(entry, "per_study_folder") if "per_study_folder" in entry._data else None
        entry.done()
        places.append(EvidencePlace(name, file, folder))
    if len({p.name for p in places}) != len(places):
        raise ConfigError("context.evidence_places: a name is repeated")
    corpus_list = _path_pattern(context, "corpus_list", optional=True)
    context.done()

    checks = top.section("checks_comment")
    checks_comment = ChecksComment(
        starts_with=checks.text("starts_with"),
        left_out_heading=checks.text("left_out_heading"),
        included_heading=checks.text("included_heading"),
        within_minutes=checks.count("within_minutes"),
        included_rows=tuple(checks.items("included_rows")),
    )
    checks.done()
    if not all(isinstance(row, str) and row.strip() == row and row and "|" not in row for row in checks_comment.included_rows):
        raise ConfigError("checks_comment.included_rows: must be row names")
    if any(re.search(r"review|AI tools|personal", row, re.IGNORECASE) for row in checks_comment.included_rows):
        raise ConfigError("checks_comment.included_rows: a row about the review, the AI tools or personal details can never be included")

    gate = top.section("gate")
    threshold = gate.take("threshold", float)
    if not 0.0 < threshold < 1.0:
        raise ConfigError("gate.threshold: must be between 0 and 1")
    gate_slots = _slots(gate, "gate", gate.choice("effort", EFFORTS), gate.count("max_output_tokens"), threshold, gate.count("max_input_tokens"))
    gate.done()

    panel = top.section("panel")
    panel_effort = panel.choice("effort", EFFORTS)
    panel_max = panel.count("max_output_tokens")
    every = panel.count("self_consistency_every")
    reasons_kept = panel.count("reasons_kept")
    reason_max = panel.count("reason_max_characters")
    panel_slots = _slots(panel, "panel", panel_effort, panel_max, None, panel.count("max_input_tokens"))
    panel.done()
    names = [s.slot for s in gate_slots + panel_slots]
    if len(set(names)) != len(names):
        raise ConfigError("models: a slot name is repeated")

    authority = top.section("authority")
    authority_gate = authority.take("gate", str)
    per_outcome = authority.section("outcomes")
    authority_outcomes = tuple((o.key, per_outcome.take(o.key, str)) for o in outcomes)
    per_outcome.done()
    authority.done()
    if authority_gate != AUTHORITY:
        raise ConfigError("authority.gate: only \"shadow\" is accepted")
    for key, value in authority_outcomes:
        if value != AUTHORITY:
            raise ConfigError(f"authority.outcomes.{key}: only \"shadow\" is accepted")

    top.done()
    return Config(
        mode=mode,
        source_repo=source_repo,
        home_repo=home_repo,
        default_branch=default_branch,
        log_issue=log_issue,
        scoreboard_issue=scoreboard_issue,
        allowed_deciders=frozenset(deciders),
        submission_bot=submission_bot,
        record_author=record_author,
        skip_issues=frozenset(skip),
        reconcile_self_decided=reconcile_self_decided,
        caps=caps,
        context_files=context_files,
        excerpt_lines_either_side=either_side,
        excerpt_lines_when_no_line_is_given=no_line,
        excerpt_max_tokens=excerpt_max,
        evidence_places=tuple(places),
        corpus_list=corpus_list,
        checks_comment=checks_comment,
        gate=gate_slots,
        panel=panel_slots,
        self_consistency_every=every,
        reasons_kept=reasons_kept,
        reason_max_characters=reason_max,
        authority_gate=authority_gate,
        authority_outcomes=authority_outcomes,
        outcomes=outcomes,
        administrative_closure_label=closure,
        freeze_policy_version=freeze_policy,
        freeze_at=freeze_at,
        earlier_log_issues=tuple(earlier_logs),
        gate_threshold=threshold,
    )


def check_mode(raw) -> None:
    """Stops unless the settings say mode: shadow. Looks at nothing else."""
    if not isinstance(raw, dict) or raw.get("mode") != MODE or isinstance(raw.get("mode"), bool):
        raise ModeRefused("mode: only \"shadow\" is accepted")


def load(folder) -> Config:
    """Reads config.yml and outcomes.yml from one folder."""
    folder = Path(folder)
    raw = _yaml(folder / "config.yml")
    check_mode(raw)  # before outcomes.yml is opened and before any other setting is looked at
    outcomes, closure = _outcomes(_yaml(folder / "outcomes.yml"))
    return _config(raw, outcomes, closure)
