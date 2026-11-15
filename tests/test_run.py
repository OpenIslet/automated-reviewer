"""The two jobs end to end, with stand-in models: what is called, what is recorded, what is posted, and what never happens."""
import copy
import dataclasses
import json
import re

import pytest

from reviewer import record as records
from reviewer.config import PROVIDERS, ConfigError
from reviewer.github import Home
from reviewer.models import Call, FakeAdapter, Transient, fake_adapters
from reviewer.run import publish, reconcile

from .builders import DRAFT, issue, issue_text, person_comment
from .fakes import HOME, RECORD_AUTHOR, SOURCE, FakeTransport
from .world import BOT, DECIDER, PROMPTS, SETTINGS, World, bot_events, decided, proposal

LOG, BOARD = 1, 2
ON = {"AUTOMATED_REVIEWER_ENABLED": "true"}
READY = dataclasses.replace(SETTINGS, log_issue=LOG, scoreboard_issue=BOARD)
PANEL = {"outcome": "already_covered", "confidence": 0.8, "covered_by": 2, "reasons": ["It repeats proposal 2."]}


def with_caps(**changes):
    return dataclasses.replace(READY, caps=dataclasses.replace(READY.caps, **changes))


class Trial:
    """A world, a home repository with its log, and stand-in models."""

    def __init__(self, world=None, answers=None, settings=READY):
        self.world = world or World()
        self.fake = self.world.fake
        self.fake.add_home_issue(LOG)
        self.fake.add_home_issue(BOARD)
        self.settings = settings
        self.adapters = fake_adapters(PROVIDERS, answers)
        self.home = Home(self.fake, HOME, SOURCE)
        self.results = []

    def asked(self):
        return sorted(slot for adapter in self.adapters.values() for slot in adapter.asked)

    def forget_asked(self):
        for adapter in self.adapters.values():
            del adapter.asked[:]

    def review(self, now="2026-11-20T06:17:00Z", env=ON, settings=None, **options):
        self.results = []
        return reconcile(settings or self.settings, PROMPTS, self.world.source(), self.home.comments(LOG), self.adapters, env, now, self.results, **options)

    def publish(self, results=None, settings=None, now="2026-11-20T06:30:00Z"):
        return publish(self.results if results is None else results, settings or self.settings, self.home, now)

    def run(self, now="2026-11-20T06:17:00Z", **options):
        summary = self.review(now, **options)
        self.publish(now=now[:14] + "30:00Z")  # the publish job runs some minutes after the review began
        return summary

    def logged(self):
        return {r["proposal"]: r for _, r in records.trusted_records(self.home.comments(LOG), self.settings)}


def by_slot(record, stage):
    return {slot["slot"]: slot for slot in record[stage]["slots"]}


# ---- a plain run -------------------------------------------------------------------------------------------------------

def test_the_kill_switch_stops_everything_before_anything_is_read():
    for env in ({}, {"AUTOMATED_REVIEWER_ENABLED": "false"}, {"AUTOMATED_REVIEWER_ENABLED": "yes"}, None):
        trial = Trial()
        before = len(trial.fake.requests)
        summary = reconcile(READY, PROMPTS, trial.world.source(), [], trial.adapters, env, "2026-11-20T06:17:00Z", trial.results)
        assert summary.status == "disabled" and trial.results == [] and trial.asked() == [] and len(trial.fake.requests) == before


def test_a_run_reviews_every_decided_proposal_oldest_first_and_records_what_the_spec_lists():
    trial = Trial(answers={"panel-1": [PANEL] * 4, "panel-2": [PANEL] * 4, "panel-3": [PANEL] * 4})
    summary = trial.review()
    assert summary.reviewed == [2, 10] and summary.excluded == [] and summary.stopped_by is None
    assert trial.fake.writes() == []  # the review job writes nothing, anywhere
    two, ten = trial.results
    assert (two["proposal"], ten["proposal"]) == (2, 10) and two["complete"] and ten["complete"]
    assert ten["decision"] == {"label": "outcome:already-covered", "by": DECIDER, "at": "2026-11-11T00:00:05Z", "same_as": 2, "same_as_how": "same_as_line", "same_as_decided_then": True}
    assert (two["submitted_at"], ten["submitted_at"]) == ("2026-11-02T00:00:00Z", "2026-11-09T12:00:00Z")
    assert two["decision"]["label"] == "outcome:evidence" and two["decision"]["same_as"] is None
    assert ten["text"]["confirmed"] == "hash_match" and len(ten["text"]["sha256"]) == 64 and ten["context"] == {"commit": "aaa1", "evidence_source": "study_files", "checks_included": True, "index_size": 1}
    assert ten["contributor"] == {"ai_draft": "ExampleModel 9", "ai_review": "OtherModel 3", "blind_review_verdict": "Partly supported"}
    assert ten["gate"]["recommendation"] == "not_spam" and ten["panel"]["recommendation"] == "already_covered"
    assert [s["verdict"]["covered_by"] for s in ten["panel"]["slots"]] == [2, 2, 2]
    assert [s["verdict"]["covered_by"] for s in two["panel"]["slots"]] == [None] * 3 and all("covered_by_not_in_index" in s["verdict"]["flags"] for s in two["panel"]["slots"])
    # Every fifth proposal by number gets a second, independent panel call: 10 does and 2 does not.
    assert ten["panel"]["second_opinion"] is True and all(s["second"] == {"status": "verdict", "outcome": "already_covered", "same": True} for s in ten["panel"]["slots"])
    assert two["panel"]["second_opinion"] is False and all("second" not in s for s in two["panel"]["slots"])
    assert len(trial.asked()) == 6 + 9
    assert [s["model"] for s in ten["panel"]["slots"]] == ["gemini-4-hydrogen", "gpt-6.1-sol", "claude-sonnet-5-5"]


def test_in_shadow_mode_no_record_ever_carries_an_action_and_nothing_is_written_to_the_source_repository():
    trial = Trial()
    trial.run()
    assert all(r["actions"] == [] for r in trial.results) and all(r["actions"] == [] for r in trial.logged().values())
    assert len(trial.fake.writes()) == 2 and all(f"/repos/{HOME}/" in url for _, url in trial.fake.writes())
    assert not any(SOURCE in url for method, url in trial.fake.requests if method != "GET" and not url.endswith("/graphql"))
    assert all("Action taken: none." in c["body"] for c in trial.home.comments(LOG))


def test_a_second_run_for_completed_proposals_makes_no_calls_and_posts_nothing():
    trial = Trial()
    trial.run()
    trial.forget_asked()
    writes = len(trial.fake.writes())
    summary = trial.run(now="2026-11-20T12:17:00Z")
    assert summary.reviewed == [] and summary.already_done == 2 and trial.asked() == [] and len(trial.fake.writes()) == writes


# ---- failures ----------------------------------------------------------------------------------------------------------

def test_a_run_after_a_partial_failure_calls_only_the_missing_model_and_edits_its_own_comment():
    trial = Trial(answers={"panel-2": [Transient(), Transient(), Transient()]})
    trial.run(numbers=[2])
    first = trial.logged()[2]
    assert first["complete"] is False and by_slot(first, "panel")["panel-2"]["status"] == "pending" and by_slot(first, "panel")["panel-2"]["runs"] == 1
    assert by_slot(first, "panel")["panel-2"]["calls"] == [] and first["panel"]["recommendation"] is None
    comment_ids = [c["id"] for c in trial.home.comments(LOG)]
    trial.forget_asked()
    trial.run(now="2026-11-20T12:17:00Z", numbers=[2])
    assert trial.asked() == ["panel-2"]  # nothing that succeeded was called again
    second = trial.logged()[2]
    assert second["complete"] is True and second["panel"]["recommendation"] == "not_taken_up"
    assert [c["id"] for c in trial.home.comments(LOG)] == comment_ids  # edited in place, not posted again
    assert by_slot(second, "panel")["panel-1"]["calls"][0]["at"] == "2026-11-20T06:17:00Z" and by_slot(second, "panel")["panel-2"]["calls"][0]["at"] == "2026-11-20T12:17:00Z"


def test_a_model_with_no_answer_after_three_runs_is_recorded_as_an_error():
    trial = Trial(answers={"gate-1": [Transient()] * 9})
    for hour in ("06", "12", "18"):
        trial.run(now=f"2026-11-20T{hour}:17:00Z", numbers=[2])
    slot = by_slot(trial.logged()[2], "gate")["gate-1"]
    assert (slot["status"], slot["reason"], slot["runs"]) == ("no_verdict", "error", 3)
    assert trial.logged()[2]["complete"] is True and trial.logged()[2]["gate"]["recommendation"] == "not_spam"  # the other two agree


def test_each_kind_of_bad_answer_is_recorded_as_the_spec_says():
    bad = "this is not the agreed shape"
    trial = Trial(
        answers={
            "panel-1": [bad, PANEL],  # wrong shape, then right: one retry
            "panel-2": [bad, bad],  # wrong shape twice
            "panel-3": [Call("truncated")],
            "gate-1": [Call("refused", refusal_category="policy: <b>bold</b> category")],
            "gate-2": [Call("ok", 0.9, reported_model="another-model-entirely")],
        }
    )
    trial.review(numbers=[2])
    record = trial.results[0]
    panel, gate = by_slot(record, "panel"), by_slot(record, "gate")
    assert panel["panel-1"]["status"] == "verdict" and [c["status"] for c in panel["panel-1"]["calls"]] == ["invalid", "ok"]
    assert (panel["panel-2"]["status"], panel["panel-2"]["reason"], len(panel["panel-2"]["calls"])) == ("no_verdict", "invalid_output", 2)
    assert (panel["panel-3"]["reason"], len(panel["panel-3"]["calls"])) == ("truncated", 1)  # not asked again
    assert (gate["gate-1"]["reason"], gate["gate-1"]["refusal_category"]) == ("refused", "policy: <b>bold</b> category")
    assert (gate["gate-2"]["status"], gate["gate-2"]["reason"]) == ("no_verdict", "wrong_model")
    assert trial.asked() == ["gate-1", "gate-2", "gate-3", "panel-1", "panel-1", "panel-2", "panel-2", "panel-3"]  # no model stood in for another
    assert record["complete"] is True and record["panel"]["recommendation"] is None and record["gate"]["recommendation"] == "no_majority"
    cleaned = records.clean(record, READY)
    assert by_slot(cleaned, "gate")["gate-1"]["refusal_category"] == "policy: b bold b category"  # only plain characters are kept


def test_an_input_over_a_models_limit_makes_no_call_and_is_never_cut():
    small = dataclasses.replace(READY, panel=tuple(dataclasses.replace(s, max_input_tokens=500) for s in READY.panel))
    trial = Trial(settings=small)
    trial.review(numbers=[2])
    assert [(s["status"], s["reason"], s["calls"]) for s in trial.results[0]["panel"]["slots"]] == [("no_verdict", "too_large", [])] * 3
    assert trial.asked() == ["gate-1", "gate-2", "gate-3"]


def test_results_from_a_review_that_fails_part_way_are_still_published():
    trial = Trial(answers={"panel-1": [PANEL, RuntimeError("an unexpected fault here")]})
    summary = trial.review()  # the fault costs that proposal its turn, not the run: see tests/test_faults.py
    assert summary.reviewed == [2] and summary.left_for_later == {10: "internal_error:RuntimeError"}
    assert [r["proposal"] for r in trial.results] == [2, 10] and trial.results[0]["complete"] is True
    paid_for = by_slot(trial.results[1], "gate")
    assert all(slot["status"] == "verdict" and slot["calls"] for slot in paid_for.values())  # the gate answers for 10 were not lost
    summary = trial.publish()
    assert summary.posted == 2 and trial.logged()[10]["complete"] is False
    trial.forget_asked()
    trial.run(now="2026-11-20T12:17:00Z")
    assert trial.asked() == ["panel-1", "panel-1", "panel-2", "panel-2", "panel-3", "panel-3"]  # only what was missing, and the second opinions
    assert trial.logged()[10]["complete"] is True


# ---- limits ------------------------------------------------------------------------------------------------------------

def ten_at_once():
    world = World()
    for n in range(20, 30):
        at = f"2026-11-15T10:00:{n:02d}Z"
        world.add(proposal(n, f"2026-11-14T00:00:{n:02d}Z", draft=DRAFT), bot_events(f"2026-11-14T00:00:{n:02d}Z") + [decided("outcome:not-taken-up", at)])
    return world


def test_ten_proposals_decided_within_one_minute_are_all_processed_across_runs():
    trial = Trial(world=ten_at_once(), settings=with_caps(proposals_per_run=5))
    first = trial.run(now="2026-11-20T06:17:00Z")
    assert first.reviewed == [2, 10, 20, 21, 22] and first.stopped_by == "per_run_limit"
    second = trial.run(now="2026-11-20T12:17:00Z")
    third = trial.run(now="2026-11-20T18:17:00Z")
    assert second.reviewed == [23, 24, 25, 26, 27] and third.reviewed == [28, 29] and third.stopped_by is None
    assert sorted(trial.logged()) == [2, 10] + list(range(20, 30)) and all(r["complete"] for r in trial.logged().values())


def test_the_daily_cap_counts_from_the_log_and_lifts_the_next_day():
    trial = Trial(world=ten_at_once(), settings=with_caps(proposals_per_run=4, proposals_per_day=6))
    assert trial.run(now="2026-11-20T06:17:00Z").reviewed == [2, 10, 20, 21]
    second = trial.run(now="2026-11-20T12:17:00Z")
    assert second.reviewed == [22, 23] and second.stopped_by == "daily_cap"
    assert trial.run(now="2026-11-20T18:17:00Z").reviewed == []
    assert trial.run(now="2026-11-21T00:17:00Z").reviewed == [24, 25, 26, 27]


def test_a_monthly_limit_leaves_slots_waiting_without_counting_against_them():
    trial = Trial(settings=with_caps(monthly_calls=3))  # three calls a provider: #2 uses two each (gate and panel)
    trial.run()
    two, ten = trial.logged()[2], trial.logged()[10]
    assert two["complete"] is True
    assert [(s["status"], s["reason"]) for s in ten["gate"]["slots"]] == [("verdict", None)] * 3
    assert [(s["status"], s["reason"], s["runs"]) for s in ten["panel"]["slots"]] == [("pending", "monthly_calls", 0)] * 3
    trial.forget_asked()
    assert trial.run(now="2026-11-25T06:17:00Z").reviewed == [10] and trial.asked() == []  # still November: counted from the log
    trial.run(now="2026-12-01T06:17:00Z")
    # A new month. Proposal 10 is a fifth one, so each panel model is asked twice: its answer and its second opinion.
    assert len(trial.asked()) == 6 and all(s["status"] == "verdict" and "second" in s for s in trial.logged()[10]["panel"]["slots"])


def test_token_limits_are_counted_too_and_only_an_explicit_override_lifts_a_limit():
    trial = Trial(settings=with_caps(monthly_input_tokens=1000))
    trial.review()
    assert all(s["reason"] == "monthly_input_tokens" for r in trial.results for s in r["panel"]["slots"])
    trial = Trial(settings=with_caps(monthly_output_tokens=2500))
    trial.review(numbers=[2])
    assert [s["reason"] for s in trial.results[0]["panel"]["slots"]] == ["monthly_output_tokens"] * 3
    trial = Trial(world=ten_at_once(), settings=with_caps(proposals_per_run=1, proposals_per_day=1, monthly_calls=1))
    assert len(trial.review(override_caps=True).reviewed) == 12 and all(r["complete"] for r in trial.results)


# ---- which proposals -----------------------------------------------------------------------------------------------------

def test_numbers_given_to_a_manual_run_only_narrow_the_set():
    world = World()
    world.add(proposal(12, "2026-11-12T00:00:00Z"), bot_events("2026-11-12T00:00:00Z"))  # not decided
    own = issue(number=13, author=DECIDER, body=issue_text(login=DECIDER, maintainer=True), created_at="2026-11-12T01:00:00Z")
    world.add(own, [decided("outcome:evidence", "2026-11-12T02:00:00Z")])
    trial = Trial(world=world)
    summary = trial.review(numbers=[10, 12, 999])
    assert summary.reviewed == [12, 10] and [r["proposal"] for r in trial.results] == [12, 10]  # the unknown number is ignored
    assert trial.results[0]["decision"] is None  # 12 is reviewed though it is not decided yet, and before the decided ones
    trial = Trial(world=world)
    scheduled = trial.review()
    assert scheduled.reviewed == [12, 2, 10] and scheduled.excluded == [13] and trial.results[-1]["exclusion"] == {"reason": "self_decided", "detail": None}
    trial.publish()
    named = trial.review(now="2026-11-20T12:17:00Z", numbers=[13])
    assert named.reviewed == [13] and trial.results[0]["self_decided"] is True and "self_decided" in trial.results[0]["text"]["flags"]


def test_an_exclusion_is_recorded_once_with_no_call():
    world = World()
    world.add(proposal(13, "2026-11-12T02:00:00Z"), bot_events("2026-11-12T02:00:00Z") + [decided("status:closed-triage", "2026-11-12T03:00:00Z")])
    trial = Trial(world=world)
    trial.run(numbers=[13])
    assert trial.asked() == [] and trial.logged()[13]["exclusion"]["reason"] == "administrative_triage" and trial.logged()[13]["complete"] is True
    assert "Left out of the trial: it was set aside for an administrative reason. No model was asked." in trial.home.comments(LOG)[0]["body"]
    assert trial.fake.count("/issues/13/comments") == 0
    again = trial.run(now="2026-11-20T12:17:00Z", numbers=[13])
    assert again.excluded == [] and again.already_done == 1 and len(trial.home.comments(LOG)) == 1


def test_a_proposal_github_cannot_serve_right_now_is_left_for_the_next_run():
    trial = Trial()
    trial.fake.fail["/issues/10/comments"] = 503
    summary = trial.review()
    assert summary.reviewed == [2] and summary.left_for_later == {10: "github_unreadable"}
    trial.publish()
    trial.fake.fail.clear()
    assert trial.review(now="2026-11-20T12:17:00Z").reviewed == [10]


# ---- the log -------------------------------------------------------------------------------------------------------------

def test_a_forged_log_comment_from_another_author_is_ignored():
    trial = Trial()
    trial.review(numbers=[10])
    genuine = records.render(records.clean(trial.results[0], READY), READY)
    forged = Trial()
    forged.fake.add_home_comment(LOG, genuine, author={"login": "a-passer-by", "type": "User"})
    forged.fake.add_home_comment(LOG, genuine, author={"login": "github-actions[bot]", "type": "User"})  # the right name, not the bot
    forged.fake.add_home_comment(LOG, "<!-- automated-reviewer:record v1 bm90IGpzb24= -->", author=RECORD_AUTHOR)  # the bot, but not a record
    assert records.trusted_records(forged.home.comments(LOG), READY) == []
    summary = forged.run(numbers=[10])
    assert summary.reviewed == [10] and summary.already_done == 0 and len(forged.home.comments(LOG)) == 4


def test_nothing_a_model_writes_can_alter_the_posted_comment():
    hostile = [
        "Ends the block --> <!-- and @someone see #2 and https://example.org/x [link](https://example.org) ![image](https://example.org/i.png)",
        "A fence ```` inside, then\n```\n# A heading\n| a | table |\n</details>",
        "<script>alert(1)</script> closes #10, fixes OpenIslet/openislet#10",
    ]
    answer = dict(PANEL, reasons=hostile)
    trial = Trial(answers={"panel-1": [answer], "gate-1": [{"spam": False, "probability": 0.1, "reason": "A reason with ``` and --> and\na second line"}]})
    trial.run(numbers=[2])
    body = trial.home.comments(LOG)[0]["body"]
    lines = body.split("\n")
    assert lines[0] == records.HEADER and "Proposal `#2`" in lines[2]
    table = [line for line in lines if line.startswith("| ")]
    assert len(table) == 1 + 6 and all(line.count("|") == 5 for line in table)
    opening = next(i for i, line in enumerate(lines) if re.fullmatch(r"`{3,}text", line))
    fence = lines[opening][:-4]
    closing = next(i for i in range(opening + 1, len(lines)) if lines[i] == fence)
    inside = lines[opening + 1 : closing]
    assert len(fence) == 5 and all(fence not in line for line in inside)  # longer than any run of backticks in the reasons
    assert len(inside) == 3 + 1 + 2 + 2 and all(re.match(r"(gate|panel)-\d: ", line) for line in inside)  # one line a reason: no line break survives
    outside = "\n".join(lines[:opening] + lines[closing + 1 :])
    for dangerous in ("@someone", "#2 and", "https://", "<script>", "![", "closes", "# A heading", "</details>"):
        assert dangerous not in outside, dangerous
    assert outside.count("<!--") == 1 and outside.count("-->") == 1 and lines[-1].startswith("<!-- automated-reviewer:record v1 ") and lines[-1].endswith(" -->")
    back = trial.logged()[2]
    assert by_slot(back, "panel")["panel-1"]["verdict"]["reasons"][0].startswith("Ends the block --> <!-- and @someone")
    assert records.clean(records.parse(body), READY) == back


def test_the_comment_shows_no_text_written_by_a_contributor():
    trial = Trial()
    trial.run()
    for comment in trial.home.comments(LOG):
        visible = comment["body"].rsplit("\n", 1)[0]
        for hidden in ("Proposal 10", "Proposal 2 ", "Signal X", "some-one", "ExampleModel", "OtherModel", "A Person", "Claim of proposal"):
            assert hidden not in visible, hidden
        assert "#10" not in visible.replace("`#10`", "") and "#2" not in visible.replace("`#2`", "")  # only in code formatting: no cross-reference


def test_the_publish_job_refuses_what_is_not_a_clean_record():
    trial = Trial()
    trial.review(numbers=[2])
    good = trial.results[0]
    with_action = dict(copy.deepcopy(good), actions=[{"label": "outcome:evidence"}])
    stand_in = dict(copy.deepcopy(good), dry_run=True)
    strange_label = copy.deepcopy(good)
    strange_label["decision"]["label"] = "outcome:approved-automatically"
    not_an_outcome = copy.deepcopy(good)
    not_an_outcome["panel"]["slots"][0]["verdict"]["outcome"] = "approved"
    summary = trial.publish([with_action, stand_in, strange_label, not_an_outcome, "not a record", {"v": 99}, good])
    assert summary.posted == 1 and summary.refused == {"actions_not_empty": 1, "dry_run_result": 1, "decision_label_not_known": 1, "verdict_not_in_the_expected_form": 1, "not_a_record": 2}
    assert len(trial.fake.writes()) == 1
    with pytest.raises(ConfigError):
        publish([good], dataclasses.replace(SETTINGS, log_issue=None), trial.home, "2026-11-20T06:30:00Z")  # with no log issue number in the settings


def test_a_record_rebuilt_by_the_publish_job_keeps_only_values_of_the_expected_kind():
    trial = Trial()
    trial.review(numbers=[10])
    raw = copy.deepcopy(trial.results[0])
    raw["surprise"] = "<!-- anything -->"
    raw["text"]["flags"] += ["a flag with spaces and <b>markup</b>", "fine_flag"]
    raw["gate"]["recommendation"] = "spam"  # never taken on trust: worked out again from the slots
    raw["panel"]["slots"][0]["verdict"]["reasons"] = ["x" * 5000, 7, "ok", "three", "four"]
    raw["contributor"]["ai_draft"] = "A model\nwith a line break " + "y" * 500
    cleaned = records.clean(raw, READY)
    assert "surprise" not in cleaned and "fine_flag" in cleaned["text"]["flags"] and not any(" " in flag for flag in cleaned["text"]["flags"])
    assert cleaned["gate"]["recommendation"] == "not_spam"
    assert [len(r) for r in cleaned["panel"]["slots"][0]["verdict"]["reasons"]] == [300, 2] and len(cleaned["contributor"]["ai_draft"]) == 120
    assert records.clean(cleaned, READY) == cleaned


def test_nothing_the_script_produces_mentions_money():
    trial = Trial()
    trial.run()
    produced = json.dumps(trial.results) + "\n".join(c["body"] for c in trial.home.comments(LOG))
    assert not re.search(r"[$€£¥₹]|\b(USD|AUD|EUR|GBP|dollars?|cents?|price|budget)\b|\bcosts?\b", produced, re.IGNORECASE)


# ---- two more guards ---------------------------------------------------------------------------------------------------

def test_a_run_stops_if_an_issue_template_could_apply_a_decision_label():
    safe = Trial()
    safe.fake.add_commit("ccc3", "2026-11-12T00:00:00Z", {".github/ISSUE_TEMPLATE/iip.yml": "name: IIP\nlabels: [\"submission\", \"status:queued\"]\n", ".github/ISSUE_TEMPLATE/config.yml": "blank_issues_enabled: false\n"})
    assert safe.review().reviewed == [2, 10]
    for label in ("outcome:evidence", "decision:needs-work", "status:closed-triage"):
        trial = Trial()
        trial.fake.add_commit("ccc3", "2026-11-12T00:00:00Z", {".github/ISSUE_TEMPLATE/iip.yml": f"name: IIP\nlabels: [\"submission\", \"{label}\"]\n"})
        summary = trial.review()
        assert summary.status == "unsafe_issue_template" and trial.results == [] and trial.asked() == []
        assert trial.fake.count(f"/repos/{SOURCE}/issues") == 0  # no issue in the source repository was read at all


def test_the_entry_point_refuses_any_mode_but_shadow_and_will_not_make_a_real_call_yet(tmp_path):
    import shutil
    import sys

    import yaml

    from .test_config import SHIPPED

    sys.path.insert(0, str(SHIPPED.parent / "scripts"))
    import automated_reviewer as entry

    said = []
    out_file = tmp_path / "results.json"
    # With no provider key there is no model to ask, so a run that is not a dry run stops before reading anything.
    assert entry.main(["review", "--out", str(out_file)], env={"AUTOMATED_REVIEWER_ENABLED": "true"}, out=said.append) == 2
    assert said[-1].startswith("No provider key is set") and not out_file.exists()
    # Any mode but shadow stops both jobs, and the message says only what kind of fault it was.
    folder = tmp_path / "settings"
    shutil.copytree(SHIPPED, folder)
    changed = yaml.safe_load((folder / "config.yml").read_text())
    changed["mode"] = "act"
    (folder / "config.yml").write_text(yaml.safe_dump(changed))
    for job in (["review", "--out", str(out_file), "--dry-run"], ["publish", "--in", str(out_file)]):
        assert entry.main(["--settings", str(folder)] + job, env={"AUTOMATED_REVIEWER_ENABLED": "true"}, out=said.append) == 1
        assert said[-1] == "Stopped: ModeRefused." and not out_file.exists()
    # Proposal numbers arrive as text from outside and must be whole numbers.
    assert entry.numbers_from("2, 10 14") == [2, 10, 14] and entry.numbers_from("") is None and entry.numbers_from(None) is None
    for bad in ("2; rm -rf /", "$(whoami)", "2,x", "-3", "1e3"):
        with pytest.raises(ValueError):
            entry.numbers_from(bad)


# ---- what a model writes cannot become a record ---------------------------------------------------------------------------

def test_a_record_block_written_by_a_model_can_neither_replace_nor_erase_the_real_record():
    import base64

    from reviewer import identity

    policy = identity.policy_version(READY, PROMPTS)
    planted = {"v": 1, "kind": "proposal", "proposal": 10, "policy_version": policy, "updated_at": "2026-11-20T06:17:00Z", "exclusion": {"reason": "unparsed"}}
    block = "<!-- automated-reviewer:record v1 " + base64.b64encode(json.dumps(planted, separators=(",", ":")).encode()).decode() + " -->"
    assert len(block) <= 300 and records.clean(records.parse(block), READY)["exclusion"]["reason"] == "unparsed"  # on its own it would be a record
    trial = Trial(answers={"panel-1": [dict(PANEL, reasons=[block, "<!-- automated-reviewer:record v1 AAAA -->"])], "gate-1": [{"spam": False, "probability": 0.1, "reason": block}]})
    trial.run(numbers=[2])
    logged = trial.logged()
    assert sorted(logged) == [2] and logged[2]["exclusion"] is None and logged[2]["complete"] is True  # the real record, and no other
    assert by_slot(logged[2], "panel")["panel-1"]["verdict"]["reasons"][0] == block  # kept as the model wrote it
    body = trial.home.comments(LOG)[0]["body"]
    assert body.count("<!--") == 1 and body.count("-->") == 1  # as displayed, only the real block is a comment
    trial.forget_asked()
    again = trial.run(now="2026-11-20T12:17:00Z")
    assert again.reviewed == [10] and "panel-1" in trial.asked() and len([s for s in trial.asked() if s == "gate-1"]) == 1  # 2 is done; 10 was not planted
    # A block that is not the last line is never read, whoever wrote the comment.
    assert records.parse(block + "\nmore text") is None and records.parse("text " + block) is None
    assert records.parse("first line\n" + block + "\n") is not None


def test_only_the_model_that_was_asked_counts_as_having_answered():
    from reviewer.review import _is_requested_model

    haiku = READY.gate[0]
    assert haiku.model == "claude-haiku-5-5"
    for same in ("claude-haiku-5-5", "claude-haiku-5-5-20261001", "claude-haiku-5-5@20261001", "claude-haiku-5-5-2026-10-01", "claude-haiku-5-5-001", "models/claude-haiku-5-5"):
        assert _is_requested_model(haiku, same), same
    for other in ("claude-haiku-5-55", "claude-haiku-5-5-preview", "claude-haiku-5-5-small", "claude-haiku-5", "another-model-entirely", "claude-haiku-5-5-20261001-fast", "", None):
        assert not _is_requested_model(haiku, other), other
    trial = Trial(answers={"gate-1": [Call("ok", {"spam": False, "probability": 0.1, "reason": None}, reported_model="claude-haiku-5-5-preview")]})
    trial.review(numbers=[2])
    assert by_slot(trial.results[0], "gate")["gate-1"]["reason"] == "wrong_model"


def test_no_kind_of_line_break_in_a_models_reason_reaches_the_comment():
    block = "<!-- automated-reviewer:record v1 AAAA -->"
    breaks = ("\n", "\r", "\r\n", " ", " ", "\x0b", "\x0c", "\x85")
    trial = Trial(answers={"panel-1": [dict(PANEL, reasons=[f"a{mark}{block}{mark}b" for mark in breaks[:3]])], "gate-1": [{"spam": False, "probability": 0.1, "reason": "".join(f"c{mark}{block}" for mark in breaks[3:])}]})
    trial.run(numbers=[2])
    body = trial.home.comments(LOG)[0]["body"]
    lines = body.splitlines()  # splits on every one of those marks, as a reader of the comment might
    assert [line for line in lines if line.startswith("<!--")] == [lines[-1]] and sorted(trial.logged()) == [2]
    assert not any(mark in body for mark in breaks[1:])  # only the comment's own plain line breaks are left
