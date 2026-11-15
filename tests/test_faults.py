"""What a run does when something goes wrong or arrives late: faults, limits, decisions, labels and a second log issue."""
import dataclasses
import http.client
import json
import shutil
import sys

import pytest
import yaml

from reviewer import record as records, review as review_module, scoreboard
from reviewer.config import PROVIDERS
from reviewer.github import GitHubError, Home, HttpTransport, Source
from reviewer.http import Http
from reviewer.models import FakeAdapter, ProviderError, Transient
from reviewer.run import _only_waiting, pending_work, publish, publish_scoreboard, read_log, reconcile

from .builders import issue, issue_text, labelled
from .fakes import HOME, SOURCE
from .test_config import SHIPPED
from .test_run import BOARD, LOG, ON, PANEL, READY, Trial, by_slot, with_caps
from .test_scoreboard import POLICY
from .test_submission import decide, log_comment, with_a_new_proposal
from .world import DECIDER, PROMPTS, World, bot_events, proposal


def calls_in(record):
    return sum(len(slot["calls"]) for stage in ("gate", "panel") for slot in record[stage]["slots"])


def renamed(to, at, was):
    return {"event": "renamed", "rename": {"from": was, "to": to}, "actor": {"login": DECIDER}, "created_at": at}


def quick_look(trial, now, numbers=None):
    return pending_work(trial.settings, PROMPTS, trial.world.source(), trial.home.comments(LOG), ON, now, numbers)


# ---- a provider turns a request down -------------------------------------------------------------------------------------

def test_a_request_a_provider_will_not_accept_does_not_hold_up_the_next_proposal():
    trial = Trial(answers={"panel-2": [ProviderError("openai", 400, "invalid_request_error")]})
    summary = trial.run()
    assert summary.reviewed == [2, 10] and summary.left_for_later == {}
    slot = by_slot(trial.logged()[2], "panel")["panel-2"]
    assert (slot["status"], slot["reason"], slot["error_code"], slot["runs"]) == ("pending", "rejected", "400:invalid_request_error", 1)
    assert [(call["status"], call["input_tokens"]) for call in slot["calls"]] == [("rejected", 0)]  # a request was made, so it is counted
    assert trial.logged()[2]["complete"] is False and _only_waiting(trial.logged()[2], "2026-11-20T07:00:00Z") is False  # it is work for the next run
    assert by_slot(trial.logged()[10], "panel")["panel-2"]["status"] == "verdict"  # the next proposal was not held up


@pytest.mark.parametrize(
    "fault",
    [ProviderError("anthropic", 401, "authentication_error"), ProviderError("anthropic", 402, "billing_error"), ProviderError("google", 403, "permission_denied"), ProviderError("openai", 429, "insufficient_quota")],
)
def test_a_refused_key_or_no_credit_stops_the_run_and_keeps_what_was_done(fault):
    slot = {"anthropic": "panel-3", "google": "panel-1", "openai": "panel-2"}[fault.provider]
    trial = Trial(answers={slot: [PANEL, fault]})  # the first answer is for proposal 2; the fault comes at proposal 10
    with pytest.raises(ProviderError):
        trial.review()
    assert [r["proposal"] for r in trial.results] == [2, 10] and trial.results[0]["complete"] is True
    assert all(s["status"] == "verdict" and s["calls"] for s in trial.results[1]["gate"]["slots"])  # what was paid for is kept
    held = by_slot(trial.results[1], "panel")[slot]
    assert (held["status"], held["calls"]) == ("pending", [])  # nothing is recorded against the model: it is asked again once a person has put it right


def test_a_real_run_waits_a_little_before_trying_again_and_a_dry_run_does_not(monkeypatch):
    waited = []
    monkeypatch.setattr(review_module, "wait", None)
    trial = Trial(answers={"gate-1": [Transient(), Transient(), {"spam": False, "probability": 0.1, "reason": None}]})
    trial.review(numbers=[2])
    assert waited == [] and by_slot(trial.results[0], "gate")["gate-1"]["status"] == "verdict"
    monkeypatch.setattr(review_module, "wait", waited.append)
    trial = Trial(answers={"gate-1": [Transient(), Transient(), Transient()]})
    trial.review(numbers=[2])
    assert waited == [2, 8] and by_slot(trial.results[0], "gate")["gate-1"]["status"] == "pending"


# ---- faults that are nobody's answer -------------------------------------------------------------------------------------

def test_a_fault_in_one_proposal_leaves_that_one_for_later_and_the_run_goes_on():
    trial = Trial(answers={"panel-1": [RuntimeError("a fault nobody planned for")]})
    summary = trial.review()
    assert summary.reviewed == [10] and summary.left_for_later == {2: "internal_error:RuntimeError"}
    assert [r["proposal"] for r in trial.results] == [2, 10] and trial.results[1]["complete"] is True
    assert all(slot["status"] == "verdict" and slot["calls"] for slot in trial.results[0]["gate"]["slots"])  # the gate answers for 2 were not lost
    assert trial.publish().posted == 2 and trial.logged()[2]["complete"] is False
    trial.forget_asked()
    trial.run(now="2026-11-20T12:17:00Z")
    assert trial.asked() == ["panel-1", "panel-2", "panel-3"] and trial.logged()[2]["complete"] is True  # only what was missing


def test_results_survive_a_run_that_is_killed_part_way():
    class Killed(FakeAdapter):
        def call(self, slot, assembled, schema, question=None):
            if slot.slot == "panel-2":
                raise KeyboardInterrupt()
            return super().call(slot, assembled, schema, question)

    trial = Trial()
    trial.adapters["openai"] = Killed("openai")
    saved = []
    with pytest.raises(KeyboardInterrupt):
        trial.review(checkpoint=lambda: saved.append(len(trial.results)))
    assert [r["proposal"] for r in trial.results] == [2] and by_slot(trial.results[0], "panel")["panel-1"]["status"] == "verdict"
    assert trial.publish().posted == 1 and calls_in(trial.logged()[2]) == 4  # three gate calls and one panel call are in the log


def test_the_results_are_handed_over_for_saving_after_each_proposal():
    trial = Trial(world=with_a_new_proposal())
    saved = []
    trial.review(checkpoint=lambda: saved.append([r["proposal"] for r in trial.results]))
    assert saved == [[12], [12, 2], [12, 2, 10]]


def test_an_answer_cut_short_is_a_passing_fault_not_a_crash(monkeypatch):
    import urllib.request

    def cut_short(*args, **kwargs):
        raise http.client.IncompleteRead(b"half an answer")

    monkeypatch.setattr(urllib.request, "urlopen", cut_short)
    assert Http().post("https://example.org/x", {}, {}) == (0, None)
    assert HttpTransport().send("GET", "https://api.github.com/x") == (0, b"")


def test_an_answer_from_github_that_is_not_json_is_a_failed_request():
    class Garbled:
        def send(self, method, url, body=None, accept="application/vnd.github+json"):
            return 200, b"<html>Bad gateway</html>"

    source = Source(Garbled(), SOURCE, "main")
    for read in (source.list_issues, lambda: source.issue(2), lambda: source.edit_history(2), lambda: source.commit_before("2026-11-20T00:00:00Z")):
        with pytest.raises(GitHubError):
            read()
    with pytest.raises(GitHubError):
        Home(Garbled(), HOME, SOURCE).create_comment(1, "text")


# ---- the title and the labels, for a proposal that is not decided yet -----------------------------------------------------

class Listening(FakeAdapter):
    """A stand-in model that keeps what it was sent."""

    def __init__(self, provider):
        super().__init__(provider)
        self.heard = []

    def call(self, slot, assembled, schema, question=None):
        self.heard.append(assembled.text())
        return super().call(slot, assembled, schema, question)


def test_an_undecided_proposal_is_sent_with_the_title_it_was_submitted_with():
    world = World()
    one = proposal(12, "2026-11-18T00:00:00Z", title="[IIP] RENAMED: duplicate of IIP-0002, closing")
    world.add(one, bot_events("2026-11-18T00:00:00Z") + [renamed(one["title"], "2026-11-19T00:00:00Z", was="[IIP] The title as it was submitted")])
    world.add(proposal(14, "2026-11-19T12:00:00Z"), bot_events("2026-11-19T12:00:00Z"))
    trial = Trial(world=world)
    trial.adapters = {provider: Listening(provider) for provider in PROVIDERS}
    assert trial.review(numbers=[12, 14]).reviewed == [12, 14]
    heard = "\n".join(text for adapter in trial.adapters.values() for text in adapter.heard)
    assert "Title: The title as it was submitted" in heard and "RENAMED" not in heard  # not in its own input, nor in its line among the earlier proposals shown for 14
    assert "#12 | Improvement | not yet decided | The title as it was submitted |" in heard


def test_a_retitle_between_the_review_and_the_decision_is_flagged_when_the_decision_is_recorded():
    trial = Trial(world=with_a_new_proposal())
    trial.run(now="2026-11-20T06:17:00Z")
    trial.world.fake.events[12].append(renamed("[IIP] A new title", "2026-11-20T10:00:00Z", was="[IIP] Proposal 12"))
    decide(trial.world, 12, "outcome:evidence", "2026-11-21T00:00:00Z")
    assert trial.run(now="2026-11-21T06:17:00Z").decisions_recorded == [12]
    assert "edited_before_decision" in trial.logged()[12]["text"]["flags"]
    found = scoreboard.numbers(list(trial.logged().values()), READY, POLICY, {}, "2026-11-22T00:00:00Z")
    assert found["reported_apart"]["edited_before_the_decision"]["proposals"] == [12] and 12 not in found["retrospective"]["proposals"]


def test_a_second_decision_after_the_first_was_recorded_is_added_and_the_quick_look_sees_it():
    trial = Trial(world=with_a_new_proposal())
    trial.run(now="2026-11-20T06:17:00Z")
    decide(trial.world, 12, "outcome:evidence", "2026-11-21T00:00:00Z")
    trial.run(now="2026-11-21T06:17:00Z")
    assert quick_look(trial, "2026-11-22T06:00:00Z") == 0
    decide(trial.world, 12, "outcome:changed-blueprint", "2026-11-22T00:00:00Z")
    assert quick_look(trial, "2026-11-22T06:00:00Z") == 1
    trial.forget_asked()
    summary = trial.run(now="2026-11-22T06:17:00Z")
    record = trial.logged()[12]
    assert summary.labels_updated == [12] and summary.decisions_recorded == [] and trial.asked() == []
    assert record["decision"]["label"] == "outcome:evidence" and "later_decision_round" in record["text"]["flags"]  # the first decision stays the decision
    assert record["labels_seen"] == ["outcome:changed-blueprint", "outcome:evidence"] and quick_look(trial, "2026-11-22T07:00:00Z") == 0
    found = scoreboard.numbers(list(trial.logged().values()), READY, POLICY, {}, "2026-11-23T00:00:00Z")
    assert found["reported_apart"]["decided_more_than_once"]["proposals"] == [12]


def test_a_label_from_an_account_that_may_not_decide_is_noted_once_and_is_not_work_for_ever():
    trial = Trial(world=with_a_new_proposal())
    trial.run(now="2026-11-20T06:17:00Z")
    trial.world.fake.events[12].append(labelled("outcome:evidence", actor="someone-else", at="2026-11-21T00:00:00Z"))
    trial.world.fake.issues[12]["labels"].append({"name": "outcome:evidence"})
    assert quick_look(trial, "2026-11-21T06:00:00Z") == 1
    summary = trial.run(now="2026-11-21T06:17:00Z")
    record = trial.logged()[12]
    assert summary.labels_updated == [12] and record["decision"] is None and "decision_label_from_other_account_ignored" in record["text"]["flags"]
    assert quick_look(trial, "2026-11-21T07:00:00Z") == 0  # the quick look and the run agree: there is nothing more to do
    assert trial.run(now="2026-11-21T12:17:00Z").already_done == 3


# ---- when the verdict was posted -----------------------------------------------------------------------------------------

def test_a_decision_made_before_the_verdict_was_posted_is_not_counted_as_made_with_it_in_view():
    trial = Trial(world=with_a_new_proposal())
    trial.review(now="2026-11-20T06:17:00Z")  # the review begins
    decide(trial.world, 12, "outcome:evidence", "2026-11-20T06:25:00Z")  # the maintainer decides while it runs
    trial.publish(now="2026-11-20T06:30:00Z")  # and only then is the verdict posted
    assert trial.logged()[12]["posted_at"] == "2026-11-20T06:30:00Z" and trial.logged()[12]["decision"] is None
    trial.run(now="2026-11-20T07:17:00Z")
    record = trial.logged()[12]
    assert record["decision"]["at"] == "2026-11-20T06:25:00Z" and record["reviewed_before_decision"] is False
    assert "That decision was made before any of this verdict was posted." in log_comment(trial, 12)["body"]


def test_a_verdict_still_waiting_for_one_model_when_the_maintainer_decided_was_not_posted_before_the_decision():
    trial = Trial(world=with_a_new_proposal(), answers={"panel-2": [Transient()] * 3})
    trial.run(now="2026-11-20T06:17:00Z", numbers=[12])
    assert trial.logged()[12]["complete"] is False and trial.logged()[12]["posted_at"] is None
    decide(trial.world, 12, "outcome:evidence", "2026-11-20T09:00:00Z")
    trial.run(now="2026-11-21T06:17:00Z", numbers=[12])  # the missing model answers after the decision
    record = trial.logged()[12]
    assert record["complete"] is True and record["posted_at"] == "2026-11-21T06:30:00Z" and record["reviewed_before_decision"] is False


def test_only_the_publish_job_says_when_a_verdict_was_posted():
    trial = Trial(world=with_a_new_proposal())
    trial.review(now="2026-11-20T06:17:00Z", numbers=[12])
    claimed = dict(trial.results[0], posted_at="2020-01-01T00:00:00Z", reviewed_before_decision=True)
    trial.publish([claimed], now="2026-11-20T06:30:00Z")
    assert trial.logged()[12]["posted_at"] == "2026-11-20T06:30:00Z" and trial.logged()[12]["reviewed_before_decision"] is None
    trial.publish([claimed], now="2026-11-25T00:00:00Z")  # once posted, a later results file cannot move it either
    assert trial.logged()[12]["posted_at"] == "2026-11-20T06:30:00Z"
    decide(trial.world, 12, "outcome:evidence", "2026-11-26T00:00:00Z")
    trial.run(now="2026-11-26T06:17:00Z", numbers=[12])
    assert trial.logged()[12]["reviewed_before_decision"] is True


# ---- calls already made are never dropped from the log -------------------------------------------------------------------

def test_a_review_that_had_begun_is_stopped_not_erased_when_the_proposal_is_then_set_aside():
    trial = Trial(world=with_a_new_proposal(), answers={"panel-2": [Transient()] * 3})
    trial.run(now="2026-11-20T06:17:00Z", numbers=[12])
    assert calls_in(trial.logged()[12]) == 5 and trial.logged()[12]["complete"] is False
    decide(trial.world, 12, "status:closed-triage", "2026-11-20T09:00:00Z")
    trial.forget_asked()
    summary = trial.run(now="2026-11-21T06:17:00Z", numbers=[12])
    record = trial.logged()[12]
    assert summary.excluded == [12] and trial.asked() == []
    assert (record["stopped"], record["exclusion"], record["complete"]) == ("administrative_triage", None, True)
    assert calls_in(record) == 5 and record["decision"]["label"] == "status:closed-triage"  # still in the log, so the limits still count them
    body = log_comment(trial, 12)["body"]
    assert "The review was stopped part-way: it was set aside for an administrative reason. The answers already given are kept." in body
    assert "| Panel | `gpt-6.1-sol` | not asked |  |" in body and "The maintainer set this proposal aside." in body
    found = scoreboard.numbers(list(trial.logged().values()), READY, POLICY, {12: {"label": None}}, "2026-11-22T00:00:00Z")
    assert found["coverage"]["stopped_part_way"] == {"administrative_triage": [12]} and found["coverage"]["reviewed"] == 0
    assert "- Review stopped part-way and left out of every figure, set aside for an administrative reason: `#12`." in scoreboard.render(found, READY)
    assert trial.run(now="2026-11-21T12:17:00Z", numbers=[12]).already_done == 1


def test_one_comment_github_will_not_take_does_not_stop_the_rest_being_written():
    trial = Trial()
    trial.run()
    first, _second = [comment["id"] for comment in trial.home.comments(LOG)]
    changed = [dict(record, updated_at="2026-11-21T00:00:00Z") for record in trial.results]
    trial.fake.fail[f"/issues/comments/{first}"] = 502
    summary = trial.publish(changed)
    assert (summary.failed, summary.edited, summary.posted) == (1, 1, 0)
    assert trial.logged()[10]["updated_at"] == "2026-11-21T00:00:00Z" and trial.logged()[2]["updated_at"] == "2026-11-20T06:17:00Z"


# ---- limits ----------------------------------------------------------------------------------------------------------------

def test_a_limit_stops_reviews_and_not_the_recording_of_decisions():
    trial = Trial(world=with_a_new_proposal(), settings=with_caps(proposals_per_run=1))
    first = trial.run(now="2026-11-20T06:17:00Z")
    assert first.reviewed == [12] and first.left_for_later == {2: "per_run_limit", 10: "per_run_limit"}  # the undecided one comes first
    decide(trial.world, 12, "outcome:evidence", "2026-11-20T09:00:00Z")
    second = trial.run(now="2026-11-20T12:17:00Z")
    assert second.reviewed == [2] and second.stopped_by == "per_run_limit" and second.decisions_recorded == [12]


def test_the_quick_look_counts_only_what_a_run_would_act_on():
    world = World()
    world.add(issue(number=13, author=DECIDER, body=issue_text(login=DECIDER, maintainer=True), created_at="2026-11-12T01:00:00Z"))
    trial = Trial(world=world, settings=with_caps(monthly_calls=3))
    trial.run(now="2026-11-20T06:17:00Z")
    # 13 is the maintainer's own and is recorded as left out. 10 waits for next month's limit. Neither is work for a run on the clock.
    assert trial.logged()[13]["exclusion"]["reason"] == "self_decided" and trial.logged()[10]["complete"] is False
    assert quick_look(trial, "2026-11-25T06:00:00Z") == 0
    assert quick_look(trial, "2026-12-01T06:00:00Z") == 1  # a new month: the limit no longer holds 10 back
    assert quick_look(trial, "2026-11-25T06:00:00Z", numbers=[13]) == 1  # a run that names a proposal always has work for it
    assert quick_look(trial, "2026-11-25T06:00:00Z", numbers=[999]) == 0


def test_a_provider_with_no_key_does_not_make_work_every_hour():
    trial = Trial()
    google = trial.adapters.pop("google")
    trial.run()
    assert all(not record["complete"] for record in trial.logged().values()) and by_slot(trial.logged()[2], "panel")["panel-1"]["reason"] == "no_adapter"
    assert quick_look(trial, "2026-11-20T12:00:00Z") == 0
    trial.adapters["google"] = google  # the key now exists, and a run is started by hand
    trial.forget_asked()
    trial.run(now="2026-11-20T12:17:00Z")
    assert all(record["complete"] for record in trial.logged().values()) and set(trial.asked()) == {"gate-2", "panel-1"}


def test_a_second_opinion_held_back_by_a_monthly_limit_waits_for_the_new_month():
    trial = Trial(settings=with_caps(monthly_calls=2))  # room for each model's gate answer and panel answer, and no more
    trial.run(numbers=[10])
    record = trial.logged()[10]
    assert all(slot["status"] == "verdict" and slot["second"] == {"status": "pending", "reason": "monthly_calls"} for slot in record["panel"]["slots"])
    assert record["complete"] is False and record["posted_at"] == "2026-11-20T06:30:00Z"  # every first answer is in: the verdict is posted
    assert _only_waiting(record, "2026-11-25T00:00:00Z") is True and _only_waiting(record, "2026-12-01T00:00:00Z") is False
    trial.forget_asked()
    trial.run(now="2026-12-01T06:17:00Z", numbers=[10])
    assert trial.asked() == ["panel-1", "panel-2", "panel-3"] and trial.logged()[10]["complete"] is True
    assert all(slot["second"] == {"status": "verdict", "outcome": "not_taken_up", "same": True} for slot in trial.logged()[10]["panel"]["slots"])


# ---- a second log issue ----------------------------------------------------------------------------------------------------

def test_when_a_new_log_issue_is_started_the_old_ones_records_are_still_read():
    NEW = 3
    trial = Trial(world=with_a_new_proposal())
    trial.run(numbers=[2, 12])
    trial.fake.add_home_issue(NEW)
    moved = dataclasses.replace(READY, log_issue=NEW, earlier_log_issues=(LOG,))
    trial.world.add(proposal(14, "2026-11-20T08:00:00Z"), bot_events("2026-11-20T08:00:00Z"))
    decide(trial.world, 12, "outcome:evidence", "2026-11-21T00:00:00Z")
    trial.forget_asked()
    results = []
    summary = reconcile(moved, PROMPTS, trial.world.source(), read_log(trial.home, moved), trial.adapters, ON, "2026-11-21T06:17:00Z", results)
    assert summary.reviewed == [14, 10] and summary.already_done == 1 and summary.decisions_recorded == [12]  # 2 is not reviewed a second time
    written = publish(results, moved, trial.home, "2026-11-21T06:30:00Z")
    assert (written.posted, written.edited) == (2, 1)
    assert len(trial.home.comments(LOG)) == 2 and len(trial.home.comments(NEW)) == 2  # 12's record was edited where it sits; the new ones went to the new issue
    logged = {record["proposal"]: record for _, record in records.trusted_records(read_log(trial.home, moved), moved)}
    assert sorted(logged) == [2, 10, 12, 14] and logged[12]["decision"]["label"] == "outcome:evidence"
    found = publish_scoreboard(moved, PROMPTS, trial.world.source(), trial.home, "2026-11-21T07:00:00Z")
    assert found["coverage"]["reviewed"] == 4
    link = found["coverage"]["waiting_for_a_decision"][0]["link"]
    assert found["coverage"]["waiting_for_a_decision"][0]["proposal"] == 14 and f"/issues/{NEW}#issuecomment-" in link
    # Forgetting to list the old issue would lose every record in it. This is why the setting exists.
    forgotten = dataclasses.replace(READY, log_issue=NEW)
    assert sorted(record["proposal"] for _, record in records.trusted_records(read_log(trial.home, forgotten), forgotten)) == [10, 14]


# ---- the entry script ------------------------------------------------------------------------------------------------------

@pytest.fixture
def entry_with(tmp_path, monkeypatch):
    """The entry script, a settings folder with the two issues set, a made-up GitHub and stand-in models as if keys were set."""
    sys.path.insert(0, str(SHIPPED.parent / "scripts"))
    import automated_reviewer as entry

    folder = tmp_path / "settings"
    shutil.copytree(SHIPPED, folder)
    config = yaml.safe_load((folder / "config.yml").read_text(encoding="utf-8"))
    config.update(log_issue=LOG, scoreboard_issue=BOARD)
    (folder / "config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    trial = Trial(world=with_a_new_proposal())
    monkeypatch.setattr(entry, "HttpTransport", lambda token=None: trial.fake)
    monkeypatch.setattr(entry, "real_adapters", lambda env: trial.adapters)
    monkeypatch.setattr(entry.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(review_module, "wait", None)
    said = []

    def go(*job, env=ON):
        return entry.main(["--settings", str(folder)] + list(job), env=env, out=said.append)

    return trial, go, said, tmp_path


def test_the_commands_work_end_to_end_as_they_would_with_keys(entry_with):
    trial, go, said, tmp = entry_with
    out_file, board_file, flag = tmp / "results.json", tmp / "scoreboard.json", tmp / "output.txt"
    assert go("pending", env=dict(ON, GITHUB_OUTPUT=str(flag))) == 0 and said[-1] == "Proposals with something to do: 3." and flag.read_text() == "work=true\n"
    assert go("review", "--out", str(out_file)) == 0
    assert said[-3] == f"Models asked: anthropic, google, openai. Policy version {POLICY[:12]}."
    assert said[-2] == "Reviewed: [12, 2, 10]. Left out: []. Decisions recorded: []. Labels updated: []. Already done: 0."
    saved = json.loads(out_file.read_text())
    assert [record["proposal"] for record in saved] == [12, 2, 10] and not any(record["dry_run"] for record in saved)
    assert trial.fake.writes() == []  # the review wrote nothing to GitHub
    assert go("publish", "--in", str(out_file)) == 0 and said[-1] == "Posted: 3. Edited: 0. Refused: {}. Not written, because GitHub would not take them: 0."
    assert go("scoreboard", "--out", str(board_file)) == 0 and json.loads(board_file.read_text())["coverage"]["reviewed"] == 3
    assert go("pending") == 0 and said[-1] == "Proposals with something to do: 0."
    assert go("pending", env=dict(ON, REVIEWER_NUMBERS="12")) == 0 and said[-1] == "Proposals with something to do: 1."


def test_with_the_switch_off_no_command_reads_asks_or_writes(entry_with):
    trial, go, said, tmp = entry_with
    (tmp / "results.json").write_text("[]")
    before = len(trial.fake.requests)
    for job in (["pending"], ["review", "--out", str(tmp / "new.json")], ["publish", "--in", str(tmp / "results.json")], ["scoreboard", "--out", str(tmp / "scoreboard.json")]):
        assert go(*job, env={}) == 0, job
    assert len(trial.fake.requests) == before and trial.asked() == []
    assert not (tmp / "scoreboard.json").exists() and not (tmp / "new.json").exists()
    assert said[-1] == "The kill switch is off. Nothing was read and nothing was written."


def test_the_run_is_marked_as_failed_when_a_provider_turned_a_request_down_and_the_results_are_still_saved(entry_with):
    trial, go, said, tmp = entry_with
    trial.adapters["openai"].answers["panel-2"] = [ProviderError("openai", 400, "invalid_request_error")]
    assert go("review", "--out", str(tmp / "one.json")) == 1
    assert said[-1] == "A provider turned down 1 request(s). Each is asked again on later runs before it is recorded as an error for that model."
    assert len(json.loads((tmp / "one.json").read_text())) == 3
    trial.adapters["anthropic"].answers["gate-1"] = [ProviderError("anthropic", 401, "authentication_error")]
    assert go("review", "--out", str(tmp / "two.json")) == 1
    assert said[-1] == "Stopped: a provider turned the request down (anthropic: status 401, authentication_error). This needs a person: a key, a permission, credit or the model's name."
    assert json.loads((tmp / "two.json").read_text()) == []  # the first request of the run was refused: there is nothing to save, and nothing is posted


def test_a_real_run_is_refused_until_the_log_issue_is_set(tmp_path, monkeypatch):
    sys.path.insert(0, str(SHIPPED.parent / "scripts"))
    import automated_reviewer as entry

    trial = Trial()
    folder = tmp_path / "settings"
    shutil.copytree(SHIPPED, folder)
    config = yaml.safe_load((folder / "config.yml").read_text(encoding="utf-8"))
    config.update(log_issue=None, scoreboard_issue=None)  # as the settings were before the two issues existed
    (folder / "config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")

    def no_network(token=None):
        raise AssertionError("nothing may be read")

    monkeypatch.setattr(entry, "HttpTransport", no_network)
    monkeypatch.setattr(entry, "real_adapters", lambda env: trial.adapters)
    said = []
    out_file = tmp_path / "results.json"
    assert entry.main(["--settings", str(folder), "review", "--out", str(out_file)], env=ON, out=said.append) == 2
    assert said[-1].startswith("The log and scoreboard issues are not set in the settings.") and not out_file.exists() and trial.asked() == []


def test_the_maintainers_own_proposal_begun_by_name_is_stopped_on_the_clock_and_finished_when_named_again():
    world = World()
    world.add(issue(number=13, author=DECIDER, body=issue_text(login=DECIDER, maintainer=True), created_at="2026-11-12T01:00:00Z"))
    trial = Trial(world=world, answers={"panel-2": [Transient()] * 3})
    trial.run(now="2026-11-20T06:17:00Z", numbers=[13])  # named by hand; one model does not answer
    assert calls_in(trial.logged()[13]) == 5 and trial.logged()[13]["complete"] is False
    trial.forget_asked()
    on_the_clock = trial.run(now="2026-11-20T07:17:00Z", numbers=None)
    assert on_the_clock.excluded == [13] and on_the_clock.reviewed == [2, 10]  # it is not carried on without being named
    record = trial.logged()[13]
    assert (record["stopped"], record["complete"], calls_in(record)) == ("self_decided", True, 5)
    trial.forget_asked()
    named = trial.run(now="2026-11-20T08:17:00Z", numbers=[13])
    record = trial.logged()[13]
    assert named.reviewed == [13] and trial.asked() == ["panel-2"]  # only the missing answer is asked for
    assert (record["stopped"], record["complete"], calls_in(record)) == (None, True, 6)


def test_flags_stored_in_another_order_are_not_a_change_to_write():
    trial = Trial(world=with_a_new_proposal())
    trial.review(now="2026-11-20T06:17:00Z", numbers=[12])
    shuffled = dict(trial.results[0], text=dict(trial.results[0]["text"], flags=["zz_made_up", "aa_made_up"]))
    trial.publish([shuffled])
    again = trial.run(now="2026-11-20T12:17:00Z", numbers=[12])
    assert again.already_done == 1 and again.labels_updated == []


# ---- revision 5.2: from the spec reviewer's review of the build -----------------------------------------------------------

def test_a_request_turned_down_is_asked_again_on_later_runs_before_it_becomes_an_error():
    refusal = [ProviderError("openai", 400, "invalid_request_error") for _ in range(3)]
    trial = Trial(answers={"panel-2": refusal})
    for hour in ("06", "07"):
        trial.run(now=f"2026-11-20T{hour}:17:00Z", numbers=[2])
    slot = by_slot(trial.logged()[2], "panel")["panel-2"]
    assert (slot["status"], slot["reason"], slot["error_code"], slot["runs"]) == ("pending", "rejected", "400:invalid_request_error", 2)
    assert "| Panel | `gpt-6.1-sol` | turned down by the provider (`400:invalid_request_error`), to be asked again |  |" in log_comment(trial, 2)["body"]
    assert _only_waiting(trial.logged()[2], "2026-11-20T07:40:00Z") is False  # it is work for the next run on the clock
    trial.run(now="2026-11-20T08:17:00Z", numbers=[2])
    slot = by_slot(trial.logged()[2], "panel")["panel-2"]
    assert (slot["status"], slot["reason"], slot["error_code"], slot["runs"]) == ("no_verdict", "error", "400:invalid_request_error", 3)
    assert trial.logged()[2]["complete"] is True and [call["status"] for call in slot["calls"]] == ["rejected"] * 3


def test_a_fault_put_right_between_runs_leaves_no_trace_but_the_calls():
    trial = Trial(answers={"panel-2": [ProviderError("openai", 400, "invalid_request_error")]})
    trial.run(now="2026-11-20T06:17:00Z", numbers=[2])
    assert by_slot(trial.logged()[2], "panel")["panel-2"]["status"] == "pending"
    trial.run(now="2026-11-20T07:17:00Z", numbers=[2])  # whatever was wrong has been put right: the model answers
    slot = by_slot(trial.logged()[2], "panel")["panel-2"]
    assert (slot["status"], slot["reason"], slot["error_code"]) == ("verdict", None, None) and trial.logged()[2]["complete"] is True
    assert [call["status"] for call in slot["calls"]] == ["rejected", "ok"]


def test_the_same_model_turning_down_two_proposals_in_one_run_stops_the_run():
    trial = Trial(answers={"panel-2": [ProviderError("openai", 400, "invalid_request_error"), ProviderError("openai", 400, "invalid_request_error")]})
    with pytest.raises(ProviderError) as stopped:
        trial.review()
    assert stopped.value.repeated is True and stopped.value.stops_the_run is False  # it is the repeat that stops the run
    assert [r["proposal"] for r in trial.results] == [2, 10]
    for record in trial.results:
        slot = by_slot(record, "panel")["panel-2"]
        assert (slot["status"], slot["reason"], slot["runs"]) == ("pending", "rejected", 1)  # neither is recorded as an error
    assert by_slot(trial.results[0], "panel")["panel-3"]["status"] == "verdict" and "panel-3" not in by_slot(trial.results[1], "panel")  # the run stopped there
    # A different model turning one down each is not a repeat.
    other = Trial(answers={"panel-2": [ProviderError("openai", 400, "x")], "panel-1": [PANEL, ProviderError("google", 400, "x")]})
    assert other.review().reviewed == [2, 10]


@pytest.mark.parametrize(
    "fault",
    [
        ProviderError("openai", 404, "model_not_found"),  # a model the provider does not have
        ProviderError("google", 400, "API_KEY_INVALID"),  # a refused key, from Google's older endpoints
        ProviderError("google", 400, "failed_precondition"),  # billing switched off
        ProviderError("anthropic", 400, "credit_balance_too_low"),  # no credit, which arrives as a bad request
    ],
)
def test_set_up_faults_that_arrive_looking_like_a_bad_request_stop_the_run_and_record_nothing(fault):
    slot = {"anthropic": "panel-3", "google": "panel-1", "openai": "panel-2"}[fault.provider]
    trial = Trial(answers={slot: [fault]})
    with pytest.raises(ProviderError):
        trial.review()
    held = by_slot(trial.results[0], "panel")[slot]
    assert fault.stops_the_run and (held["status"], held["reason"], held["calls"], held["runs"]) == ("pending", None, [], 0)


def test_a_run_that_reached_no_model_posts_nothing():
    refused_key = Trial(answers={"gate-1": [ProviderError("anthropic", 401, "authentication_error")]})
    with pytest.raises(ProviderError):
        refused_key.review()
    assert refused_key.results == [] and refused_key.publish().posted == 0  # no comment that says only "waiting"
    all_down = Trial(answers={slot.slot: [Transient()] * 3 for slot in READY.slots})
    summary = all_down.review(numbers=[2])
    assert all_down.results == [] and summary.reviewed == [] and summary.left_for_later == {2: "no_model_reached"}
    # A record that is waiting for something a run cannot change is kept: the quick look needs it.
    waiting = Trial(settings=with_caps(monthly_output_tokens=2500))
    waiting.review(numbers=[2])
    assert [slot["reason"] for slot in waiting.results[0]["gate"]["slots"]] == ["monthly_output_tokens"] * 3


def test_the_kind_of_an_unexpected_fault_is_kept_and_its_words_are_not(entry_with):
    trial, go, said, tmp = entry_with
    trial.adapters["google"].answers["panel-1"] = [KeyError("words that could be a proposal's own")]
    assert go("review", "--out", str(tmp / "one.json")) == 1
    assert said[-1] == "An unexpected fault left some proposals for a later run. Kind of fault: KeyError."
    assert "internal_error:KeyError" in said[-2] and "proposal's own" not in "\n".join(said)


def test_the_run_says_so_when_it_stopped_because_one_model_turned_down_two_proposals(entry_with):
    trial, go, said, tmp = entry_with
    trial.adapters["openai"].answers["panel-2"] = [ProviderError("openai", 400, "invalid_request_error") for _ in range(2)]
    assert go("review", "--out", str(tmp / "one.json")) == 1
    assert said[-1] == "Stopped: the same model turned down two proposals in one run (openai: status 400, invalid_request_error). That points to the set-up and not to a proposal, so it needs a person."
    assert len(json.loads((tmp / "one.json").read_text())) == 2  # both are saved, each with its calls


# ---- when a verdict was posted: all of it, part of it, none of it ----------------------------------------------------------

def test_a_second_opinion_still_to_come_does_not_make_a_posted_verdict_look_unposted():
    trial = Trial(world=with_a_new_proposal(number=15), settings=with_caps(monthly_calls=2))  # room for the first answers only
    trial.run(now="2026-11-20T06:17:00Z", numbers=[15])
    record = trial.logged()[15]
    assert record["complete"] is False and record["panel"]["recommendation"] == "not_taken_up"  # all three have answered; the second opinions wait
    assert record["posted_at"] == "2026-11-20T06:30:00Z" and record["first_verdict_posted_at"] == "2026-11-20T06:30:00Z"
    found = publish_scoreboard(trial.settings, PROMPTS, trial.world.source(), trial.home, "2026-11-20T06:40:00Z")
    assert [(w["proposal"], w["panel"]) for w in found["coverage"]["waiting_for_a_decision"]] == [(15, "not_taken_up")]  # the maintainer can read it
    assert quick_look(trial, "2026-11-20T08:50:00Z") == 2  # 2 and 10 have no record yet; 15 only waits for next month
    decide(trial.world, 15, "outcome:not-taken-up", "2026-11-20T09:00:00Z")
    assert quick_look(trial, "2026-11-20T09:10:00Z") == 3  # the decision is work, though the record still waits for next month
    trial.run(now="2026-11-20T09:17:00Z", numbers=[15])
    trial.run(now="2026-12-01T06:17:00Z", numbers=[15])  # the second opinions arrive, long after the decision
    record = trial.logged()[15]
    assert record["complete"] is True and record["reviewed_before_decision"] is True and records.decision_timing(record) == "after_the_verdict"
    assert "This verdict was posted before that decision." in log_comment(trial, 15)["body"]


def test_a_decision_made_with_part_of_the_verdict_posted_is_counted_as_that():
    trial = Trial(world=with_a_new_proposal(), answers={"panel-2": [Transient()] * 3})
    trial.run(now="2026-11-20T06:17:00Z", numbers=[12])
    record = trial.logged()[12]
    assert record["posted_at"] is None and record["first_verdict_posted_at"] == "2026-11-20T06:30:00Z"  # two panel answers are public
    decide(trial.world, 12, "outcome:not-taken-up", "2026-11-20T09:00:00Z")
    trial.run(now="2026-11-21T06:17:00Z", numbers=[12])  # the third model answers after the decision
    record = trial.logged()[12]
    assert record["reviewed_before_decision"] is False and records.decision_timing(record) == "after_part_of_the_verdict"
    assert "Part of this verdict was posted before that decision." in log_comment(trial, 12)["body"]
    found = scoreboard.numbers(list(trial.logged().values()), READY, POLICY, {}, "2026-11-22T00:00:00Z")
    timing = found["retrospective"]["by_timing"]
    assert (timing["after_the_verdict"]["cases"], timing["after_part_of_the_verdict"]["proposals"], timing["before_any_verdict"]["cases"]) == (0, [12], 0)


def test_only_the_publish_job_says_when_any_answer_was_first_posted():
    trial = Trial(world=with_a_new_proposal())
    trial.review(now="2026-11-20T06:17:00Z", numbers=[12])
    claimed = dict(trial.results[0], first_verdict_posted_at="2020-01-01T00:00:00Z")
    trial.publish([claimed], now="2026-11-20T06:30:00Z")
    assert trial.logged()[12]["first_verdict_posted_at"] == "2026-11-20T06:30:00Z"


# ---- one record for each proposal, whatever is in the log -------------------------------------------------------------------

def test_a_comment_written_twice_is_counted_once():
    trial = Trial(world=with_a_new_proposal())
    trial.run(now="2026-11-20T06:17:00Z")
    first = log_comment(trial, 12)
    decide(trial.world, 12, "outcome:not-taken-up", "2026-11-21T00:00:00Z")
    trial.review(now="2026-11-21T06:17:00Z")
    later = records.render(dict(records.clean(trial.results[0], READY), posted_at="2026-11-20T06:30:00Z", first_verdict_posted_at="2026-11-20T06:30:00Z"), READY)
    trial.fake.add_home_comment(LOG, later)  # a second genuine comment for 12, as a write tried twice would leave
    comments = trial.home.comments(LOG)
    assert len(comments) == 4 and len([c for c in comments if "`#12`" in c["body"]]) == 2
    found = records.trusted_records(comments, READY)
    assert sorted(record["proposal"] for _, record in found) == [2, 10, 12]
    kept = next((comment_id, record) for comment_id, record in found if record["proposal"] == 12)
    assert kept[0] != first["id"] and kept[1]["decision"]["label"] == "outcome:not-taken-up"  # the later one
    numbers = scoreboard.numbers([record for _, record in found], READY, POLICY, {}, "2026-11-22T00:00:00Z")
    assert numbers["coverage"]["reviewed"] == 3 and numbers["retrospective"]["proposals"] == [2, 10, 12] and numbers["coverage"]["waiting_for_a_decision"] == []
    from reviewer import caps

    assert caps.usage_from([record for _, record in found], "2026-11-21T07:00:00Z").provider("google").calls == 7  # 2 + 3 + 2, each once


def test_a_comment_github_took_but_did_not_confirm_is_not_written_a_second_time():
    trial = Trial()
    trial.review(numbers=[2])
    real_send, lost = trial.fake.send, []

    def send(method, url, body=None, accept="application/vnd.github+json"):
        answer = real_send(method, url, body, accept)
        if method == "POST" and url.endswith(f"/issues/{LOG}/comments") and not lost:
            lost.append(url)
            return 0, b""  # GitHub wrote the comment, and the answer never arrived
        return answer

    trial.fake.send = send
    summary = trial.publish()
    assert (summary.posted, summary.edited, summary.failed) == (0, 1, 0) and len(trial.home.comments(LOG)) == 1


# ---- a label applied by a slip of the hand ----------------------------------------------------------------------------------

def test_a_decision_read_before_the_maintainer_corrected_a_slip_is_replaced_by_the_corrected_one():
    trial = Trial(world=with_a_new_proposal())
    trial.run(now="2026-11-20T06:17:00Z")
    decide(trial.world, 12, "outcome:changed-blueprint", "2026-11-21T06:16:30Z")  # the wrong label, seconds before a run
    trial.run(now="2026-11-21T06:17:00Z")
    assert trial.logged()[12]["decision"]["label"] == "outcome:changed-blueprint"  # the run could not know it was a slip
    trial.world.fake.events[12].append({"event": "unlabeled", "label": {"name": "outcome:changed-blueprint"}, "actor": {"login": DECIDER}, "created_at": "2026-11-21T06:17:40Z"})
    trial.world.fake.issues[12]["labels"] = [label for label in trial.world.fake.issues[12]["labels"] if label["name"] != "outcome:changed-blueprint"]
    decide(trial.world, 12, "outcome:not-taken-up", "2026-11-21T06:17:41Z")
    assert quick_look(trial, "2026-11-21T07:00:00Z") == 1
    trial.forget_asked()
    summary = trial.run(now="2026-11-21T07:17:00Z")
    record = trial.logged()[12]
    assert summary.decisions_recorded == [12] and trial.asked() == []
    assert (record["decision"]["label"], record["decision"]["at"]) == ("outcome:not-taken-up", "2026-11-21T06:17:41Z")
    assert "decision_label_corrected_at_once" in record["text"]["flags"] and "later_decision_round" not in record["text"]["flags"]
    found = scoreboard.numbers(list(trial.logged().values()), READY, POLICY, {}, "2026-11-22T00:00:00Z")
    assert found["reported_apart"]["decided_more_than_once"]["proposals"] == [] and 12 in found["retrospective"]["proposals"]
    assert trial.run(now="2026-11-21T08:17:00Z").already_done == 3


def test_a_decision_on_a_record_that_is_still_waiting_is_recorded_even_when_a_limit_holds_the_review_back():
    trial = Trial(world=with_a_new_proposal(), answers={"panel-2": [Transient()] * 3})
    trial.run(now="2026-11-20T06:17:00Z", numbers=[12])
    decide(trial.world, 12, "outcome:not-taken-up", "2026-11-20T09:00:00Z")
    held = dataclasses.replace(trial.settings, caps=dataclasses.replace(trial.settings.caps, proposals_per_day=1))  # today's one review is used up
    trial.results = []
    summary = reconcile(held, PROMPTS, trial.world.source(), trial.home.comments(LOG), trial.adapters, ON, "2026-11-20T10:17:00Z", trial.results, numbers=[12])
    assert summary.left_for_later == {12: "daily_cap"} and summary.decisions_recorded == [12]
    assert trial.results[0]["decision"]["label"] == "outcome:not-taken-up" and trial.results[0]["complete"] is False
