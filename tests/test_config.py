"""The settings: what ships loads, anything unrecognised stops the script, and nothing but shadow mode is accepted."""
import copy
import dataclasses
import re
from pathlib import Path

import pytest
import yaml

from reviewer import actions
from reviewer.config import ConfigError, ModeRefused, load

SHIPPED = Path(__file__).resolve().parent.parent / "settings"
WEBSITE_DEFINITIONS = SHIPPED.parent.parent / "OpenIslet-Website" / "src" / "data" / "contributions.ts"


def shipped(name):
    return yaml.safe_load((SHIPPED / name).read_text(encoding="utf-8"))


def folder_with(tmp_path, config=None, outcomes=None, change=None, change_outcomes=None):
    """A settings folder made from the shipped files, with one thing changed."""
    config = copy.deepcopy(shipped("config.yml")) if config is None else config
    outcomes = copy.deepcopy(shipped("outcomes.yml")) if outcomes is None else outcomes
    if change:
        change(config)
    if change_outcomes:
        change_outcomes(outcomes)
    (tmp_path / "config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (tmp_path / "outcomes.yml").write_text(yaml.safe_dump(outcomes), encoding="utf-8")
    return tmp_path


def refused(tmp_path, change=None, change_outcomes=None):
    with pytest.raises(ConfigError) as error:
        load(folder_with(tmp_path, change=change, change_outcomes=change_outcomes))
    return str(error.value)


# ---- what ships ------------------------------------------------------------------------------------------------------

def test_the_shipped_settings_load():
    config = load(SHIPPED)
    assert config.mode == "shadow"
    assert (config.source_repo, config.home_repo) == ("OpenIslet/openislet", "OpenIslet/automated-reviewer")
    assert [s.model for s in config.panel] == ["gemini-4-hydrogen", "gpt-6.1-sol", "claude-sonnet-5-5"]
    assert all(s.effort == "xhigh" and s.max_output_tokens == 128000 for s in config.panel)  # the deepest setting all three panel models share
    assert [s.kind for s in config.gate] == ["text", "text", "text"] and all(s.threshold is None for s in config.gate) and config.gate_threshold == 0.5
    assert all(s.effort == "medium" and s.max_output_tokens == 8000 for s in config.gate)
    assert sorted(s.provider for s in config.gate) == sorted(s.provider for s in config.panel) == ["anthropic", "google", "openai"]
    assert [name for name, _ in config.context_files] == ["blueprint", "playbook", "idea_graveyard"]
    assert [p.name for p in config.evidence_places] == ["evidence_graph", "study_files"]
    assert (config.caps.proposals_per_run, config.caps.proposals_per_day) == (4, 20)  # four a run: each takes three long panel calls


def test_the_rules_for_deciding_come_from_the_settings():
    rules = load(SHIPPED).rules()
    assert len(rules.decision_labels) == 9
    assert {"outcome:changed-blueprint", "decision:needs-work", "outcome:spam"} <= rules.decision_labels
    assert rules.triage_label == "status:closed-triage" and rules.triage_label not in rules.decision_labels
    assert rules.skip_issues == frozenset({1}) and rules.reconcile_self_decided is False
    assert len(rules.allowed_deciders) == 1


def test_what_is_still_to_be_set_is_reported_not_guessed():
    config = load(SHIPPED)
    assert config.slots_without_a_model() == []
    assert [s.model for s in config.gate] == ["claude-haiku-5-5", "gemini-4-hydrogen", "gpt-6-luna"]
    assert config.can_record() is True and (config.log_issue, config.scoreboard_issue) == (1, 2)  # the two issues exist in the home repository
    for unset in ("log_issue", "scoreboard_issue"):  # with either missing there is nowhere to record, and a real run is refused
        assert dataclasses.replace(config, **{unset: None}).can_record() is False


@pytest.mark.skipif(not WEBSITE_DEFINITIONS.exists(), reason="the website's source is not beside this folder")
def test_the_seven_published_outcomes_are_word_for_word():
    source = WEBSITE_DEFINITIONS.read_text(encoding="utf-8")
    published = dict()
    for key, label, meaning in re.findall(r"^\s+(\w+): \{ label: '([^']*)', meaning: '([^']*)'", source, re.MULTILINE):
        published[key] = (label, meaning)
    assert len(published) == 7
    ours = {o.key: (o.name, o.meaning) for o in load(SHIPPED).outcomes if o.source == "published"}
    assert ours == published
    assert [o.key for o in load(SHIPPED).outcomes if o.source == "trial"] == ["needs_work", "spam"]


def test_the_settings_files_hold_no_money_words():
    for name in ("config.yml", "outcomes.yml"):
        text = (SHIPPED / name).read_text(encoding="utf-8")
        assert not re.search(r"[$€£¥₹]|\b(USD|AUD|EUR|GBP|dollars?|cents?|cost|costs|price|budget|spend)\b", text, re.IGNORECASE), name


# ---- only shadow -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["act", "advise", "live", "Shadow", "shadow ", "", None, True, 1, ["shadow"]])
def test_any_other_mode_stops_before_anything_else_is_read(tmp_path, mode):
    config = shipped("config.yml")
    config["mode"] = mode
    config["source_repo"] = "not a repository"  # would also be refused, if the script got that far
    (tmp_path / "config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    # There is no outcomes.yml in this folder: the refusal comes before it is opened.
    with pytest.raises(ModeRefused):
        load(tmp_path)


def test_a_missing_mode_stops_too(tmp_path):
    config = shipped("config.yml")
    del config["mode"]
    (tmp_path / "config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ModeRefused):
        load(tmp_path)
    (tmp_path / "config.yml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ModeRefused):
        load(tmp_path)


def test_every_authority_setting_must_be_shadow(tmp_path):
    def gate(c):
        c["authority"]["gate"] = "hold"

    def one_outcome(c):
        c["authority"]["outcomes"]["already_covered"] = "act"

    def missing(c):
        del c["authority"]["outcomes"]["spam"]

    def extra(c):
        c["authority"]["outcomes"]["something_new"] = "shadow"

    assert refused(tmp_path, gate) == 'authority.gate: only "shadow" is accepted'
    assert refused(tmp_path, one_outcome) == 'authority.outcomes.already_covered: only "shadow" is accepted'
    assert refused(tmp_path, missing) == "authority.outcomes.spam: missing"
    assert refused(tmp_path, extra) == "authority.outcomes.something_new: not a known setting"


def test_no_outcome_has_any_authority_today_whatever_the_settings_say():
    config = load(SHIPPED)
    assert all(actions.authority_for(config, key) == "shadow" for key in config.outcome_keys)
    forced = dataclasses.replace(config, authority_outcomes=tuple((key, "act") for key, _ in config.authority_outcomes))
    for key in config.outcome_keys:  # every outcome, the blueprint and the Idea Graveyard included
        with pytest.raises(ModeRefused):
            actions.authority_for(forced, key)


def test_in_shadow_mode_the_list_of_actions_is_always_empty():
    config = load(SHIPPED)
    records = [
        {},
        {"gate": {"recommendation": "spam"}, "panel": {"recommendation": "already_covered", "unanimous": True}},
        {"panel": {"recommendation": "changed_blueprint", "unanimous": True}, "actions": [{"label": "outcome:evidence"}]},
    ]
    assert [actions.actions_for(record, config) for record in records] == [[], [], []]
    with pytest.raises(ModeRefused):
        actions.actions_for({}, dataclasses.replace(config, mode="act"))


# ---- anything unrecognised stops the script --------------------------------------------------------------------------

def test_an_unknown_setting_a_missing_one_and_a_wrong_kind_are_each_refused(tmp_path):
    def unknown(c):
        c["post_to_source_repo"] = True

    def missing(c):
        del c["caps"]["proposals_per_day"]

    def wrong_kind(c):
        c["caps"]["proposals_per_run"] = "ten"

    def true_for_a_number(c):
        c["caps"]["proposals_per_run"] = True

    def zero(c):
        c["caps"]["monthly_per_provider"]["calls"] = 0

    assert refused(tmp_path, unknown) == "post_to_source_repo: not a known setting"
    assert refused(tmp_path, missing) == "caps.proposals_per_day: missing"
    assert refused(tmp_path, wrong_kind) == "caps.proposals_per_run: wrong kind of value"
    assert refused(tmp_path, true_for_a_number) == "caps.proposals_per_run: wrong kind of value"
    assert refused(tmp_path, zero) == "caps.monthly_per_provider.calls: too small"


def test_the_home_repository_can_never_be_the_source_repository(tmp_path):
    def same(c):
        c["home_repo"] = "openislet/OpenIslet"

    assert refused(tmp_path, same) == "home_repo: must not be the source repository"


def test_bots_cannot_be_deciders_and_deciders_must_be_logins(tmp_path):
    def bot(c):
        c["allowed_deciders"].append("openislet-submissions[bot]")

    def not_a_login(c):
        c["allowed_deciders"] = ["anyone with write access"]

    def nobody(c):
        c["allowed_deciders"] = []

    assert refused(tmp_path, bot) == "allowed_deciders: a bot account must not be a decider"
    assert refused(tmp_path, not_a_login) == "allowed_deciders: not in the expected form"
    assert refused(tmp_path, nobody) == "allowed_deciders: empty"


def test_each_stage_has_exactly_three_slots_from_known_providers(tmp_path):
    def two(c):
        c["panel"]["models"].pop()

    def other_provider(c):
        c["gate"]["models"][0]["provider"] = "somewhere-else"

    def repeated(c):
        c["panel"]["models"][0]["slot"] = "gate-1"

    assert refused(tmp_path, two) == "panel.models: there must be exactly 3"
    assert refused(tmp_path, other_provider) == "gate.models[0].provider: not one of the allowed values"
    assert refused(tmp_path, repeated) == "models: a slot name is repeated"


def test_file_patterns_stay_inside_the_repository(tmp_path):
    def climbing(c):
        c["context"]["files"]["blueprint"] = "../elsewhere/ISLET-1-BLUEPRINT.md"

    def absolute(c):
        c["context"]["files"]["playbook"] = "/etc/hosts"

    def star_in_folder(c):
        c["context"]["files"]["idea_graveyard"] = "data/*/idea_graveyard.json"

    for change in (climbing, absolute, star_in_folder):
        assert refused(tmp_path, change).endswith("not in the expected form")


def test_outcomes_must_be_distinct_and_the_closure_label_is_not_a_decision(tmp_path):
    def repeated_label(o):
        o["outcomes"][1]["label"] = o["outcomes"][0]["label"]

    def closure_is_a_decision(o):
        o["administrative_closure_label"] = "outcome:spam"

    def unknown_source(o):
        o["outcomes"][0]["source"] = "guessed"

    assert refused(tmp_path, change_outcomes=repeated_label) == "outcomes.yml.outcomes: a key or a label is repeated"
    assert refused(tmp_path, change_outcomes=closure_is_a_decision) == "outcomes.yml.administrative_closure_label: is also a decision label"
    assert refused(tmp_path, change_outcomes=unknown_source) == "outcomes.yml.outcomes[0].source: not one of the allowed values"


def test_earlier_log_issues_are_numbers_that_are_neither_the_log_nor_the_scoreboard(tmp_path):
    def with_(log, board, earlier):
        def change(c):
            c.update(log_issue=log, scoreboard_issue=board, earlier_log_issues=earlier)

        return change

    assert load(SHIPPED).earlier_log_issues == ()
    assert load(folder_with(tmp_path, change=with_(3, 2, [1]))).earlier_log_issues == (1,)
    for bad in (with_(3, 2, [3]), with_(3, 2, [2]), with_(None, None, [1]), with_(3, 2, ["1"]), with_(3, 2, [1, 1]), with_(3, 2, [0]), with_(3, 2, [True])):
        assert refused(tmp_path, bad).startswith("earlier_log_issues:")
