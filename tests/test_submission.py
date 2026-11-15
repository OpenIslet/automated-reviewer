"""The reviewer runs soon after a proposal is submitted. The maintainer's decision is added to the record when it comes."""
from reviewer import record as records, scoreboard
from reviewer.run import pending_work, publish_scoreboard

from .builders import issue, issue_text, with_marker
from .test_run import LOG, BOARD, ON, READY, Trial
from .test_scoreboard import POLICY
from .world import BOT, DECIDER, PROMPTS, World, bot_events, decided, proposal


def with_a_new_proposal(number=12, at="2026-11-18T00:00:00Z"):
    world = World()
    world.add(proposal(number, at), bot_events(at))
    return world


def decide(world, number, label, at):
    """The maintainer applies a label, as GitHub would then show it."""
    event = decided(label, at)
    world.fake.events[number].append(event)
    world.fake.issues[number]["labels"].append({"name": label})
    one, events, comments = world.rows[number]
    world.rows[number] = (one, world.fake.events[number], comments)


def log_comment(trial, number):
    return next(c for c in trial.home.comments(LOG) if f"`#{number}`" in c["body"])


def test_a_new_proposal_is_reviewed_before_the_maintainer_has_decided():
    trial = Trial(world=with_a_new_proposal())
    summary = trial.run(now="2026-11-20T06:17:00Z")
    assert summary.reviewed == [12, 2, 10] and summary.decisions_recorded == []  # the one the maintainer is waiting for comes first
    record = trial.logged()[12]
    assert record["decision"] is None and record["reviewed_before_decision"] is None and record["complete"] is True
    assert record["panel"]["recommendation"] == "not_taken_up" and record["gate"]["recommendation"] == "not_spam" and record["actions"] == []
    body = log_comment(trial, 12)["body"]
    assert "The maintainer has not decided this proposal yet." in body and "Outcome the maintainer recorded" not in body


def test_when_the_decision_comes_it_is_added_without_asking_any_model_again():
    trial = Trial(world=with_a_new_proposal())
    trial.run(now="2026-11-20T06:17:00Z")
    comment_id = log_comment(trial, 12)["id"]
    trial.forget_asked()
    assert trial.run(now="2026-11-20T12:17:00Z").already_done == 3  # still undecided: nothing to do

    decide(trial.world, 12, "outcome:evidence", "2026-11-21T00:00:00Z")
    summary = trial.run(now="2026-11-21T06:17:00Z")
    assert summary.decisions_recorded == [12] and summary.reviewed == [] and trial.asked() == []
    record = trial.logged()[12]
    assert record["decision"]["label"] == "outcome:evidence" and record["decision"]["at"] == "2026-11-21T00:00:00Z"
    assert record["reviewed_before_decision"] is True and record["first_reviewed_at"] == "2026-11-20T06:17:00Z"
    body = log_comment(trial, 12)["body"]
    assert log_comment(trial, 12)["id"] == comment_id  # the same comment, edited in place
    assert "Outcome the maintainer recorded: Added evidence. This verdict was posted before that decision." in body
    assert trial.run(now="2026-11-21T12:17:00Z").already_done == 3 and trial.asked() == []


def test_a_proposal_decided_before_it_was_reviewed_is_marked_so():
    trial = Trial()
    trial.run()
    for number in (2, 10):
        assert trial.logged()[number]["reviewed_before_decision"] is False
        assert "That decision was made before any of this verdict was posted." in log_comment(trial, number)["body"]


def test_only_issues_the_submission_bot_opened_are_reviewed():
    world = World()
    copied = issue(number=12, author="a-passer-by", body=with_marker(issue_text()), created_at="2026-11-18T00:00:00Z")  # the whole template, typed in by hand
    world.add(copied, [decided("outcome:evidence", "2026-11-19T00:00:00Z")])
    world.add(issue(number=13, author="a-passer-by", body="The site shows the wrong date.", created_at="2026-11-18T01:00:00Z", title="Bug"))
    wrong_kind_of_account = issue(number=14, author=BOT, body=with_marker(issue_text()), created_at="2026-11-18T02:00:00Z")
    wrong_kind_of_account["user"]["type"] = "User"  # the bot's name on an ordinary account
    world.add(wrong_kind_of_account)
    trial = Trial(world=world)
    summary = trial.run()
    assert summary.reviewed == [2, 10] and summary.excluded == [] and sorted(trial.logged()) == [2, 10]
    assert all(trial.fake.count(f"/issues/{n}/comments") == 0 for n in (12, 13, 14))
    assert trial.review(now="2026-11-20T12:17:00Z", numbers=[12, 13, 14]).reviewed == []  # not even when named


def test_the_maintainers_own_proposal_is_left_out_at_submission_and_reviewed_only_when_named():
    world = World()
    own = issue(number=12, author=DECIDER, body=issue_text(login=DECIDER, maintainer=True), created_at="2026-11-18T00:00:00Z")
    world.add(own)  # not decided yet
    trial = Trial(world=world)
    assert trial.run().excluded == [12]
    record = trial.logged()[12]
    assert record["exclusion"] == {"reason": "self_decided", "detail": None} and record["decision"] is None and record["self_decided"] is True
    assert trial.fake.count("/issues/12/comments") == 0 and trial.fake.count("/graphql") == 0  # left out before its text was looked at
    named = trial.review(now="2026-11-20T12:17:00Z", numbers=[12])
    assert named.reviewed == [12] and trial.results[0]["self_decided"] is True and trial.results[0]["decision"] is None


def test_a_proposal_set_aside_after_review_is_kept_out_of_the_agreement_figures():
    trial = Trial(world=with_a_new_proposal())
    trial.run(now="2026-11-20T06:17:00Z")
    decide(trial.world, 12, "status:closed-triage", "2026-11-21T00:00:00Z")
    assert trial.run(now="2026-11-21T06:17:00Z").decisions_recorded == [12]
    assert trial.logged()[12]["decision"]["label"] == "status:closed-triage"
    assert "The maintainer set this proposal aside." in log_comment(trial, 12)["body"]
    found = scoreboard.numbers(list(trial.logged().values()), READY, POLICY, {2: {"label": "outcome:evidence"}, 10: {"label": "outcome:already-covered"}, 12: {"label": None}}, "2026-11-22T00:00:00Z")
    assert found["coverage"]["set_aside_after_review"] == [12] and found["retrospective"]["proposals"] == [2, 10] and found["coverage"]["waiting_for_a_decision"] == []


def test_the_cheap_check_says_whether_a_run_has_anything_to_do():
    trial = Trial(world=with_a_new_proposal())

    def waiting(env=ON):
        return pending_work(READY, PROMPTS, trial.world.source(), trial.home.comments(LOG), env)

    assert waiting() == 3 and waiting({}) == 0  # three proposals not reviewed yet; and nothing at all with the switch off
    trial.run(now="2026-11-20T06:17:00Z")
    before, events_before = len(trial.fake.requests), trial.fake.count("/events")
    assert waiting() == 0
    assert trial.fake.count("/events") == events_before and len(trial.fake.requests) - before == 2  # the log and one list of issues: no label events
    decide(trial.world, 12, "outcome:evidence", "2026-11-21T00:00:00Z")
    assert waiting() == 1  # a decision to record
    trial.run(now="2026-11-21T06:17:00Z")
    assert waiting() == 0


def test_the_scoreboard_lists_what_is_waiting_for_a_decision_with_a_link_to_each_verdict():
    trial = Trial(world=with_a_new_proposal())
    trial.run(now="2026-11-20T06:17:00Z")
    found = publish_scoreboard(READY, PROMPTS, trial.world.source(), trial.home, "2026-11-20T06:30:00Z")
    waiting = found["coverage"]["waiting_for_a_decision"]
    comment_id = log_comment(trial, 12)["id"]
    assert [(w["proposal"], w["gate"], w["panel"]) for w in waiting] == [(12, "not_spam", "not_taken_up")]
    assert waiting[0]["link"] == f"https://github.com/OpenIslet/automated-reviewer/issues/{LOG}#issuecomment-{comment_id}"
    page = trial.home.issue(BOARD)["body"]
    assert "## Waiting for the maintainer's decision" in page
    assert f"- `#12`: gate says not spam; panel all three say Not taken up. [The full verdict]({waiting[0]['link']})." in page
    assert "They are not a blind test of the reviewer." in page
    assert found["retrospective"]["proposals"] == [2, 10] and found["retrospective"]["decided_after_the_verdict"] == 0

    decide(trial.world, 12, "outcome:not-taken-up", "2026-11-21T00:00:00Z")
    trial.run(now="2026-11-21T06:17:00Z")
    found = publish_scoreboard(READY, PROMPTS, trial.world.source(), trial.home, "2026-11-21T06:30:00Z")
    assert found["coverage"]["waiting_for_a_decision"] == [] and found["retrospective"]["proposals"] == [2, 10, 12]
    assert found["retrospective"]["decided_after_the_verdict"] == 1
    assert "- Cases: 3. Decided after the reviewer's verdict was posted: 1." in trial.home.issue(BOARD)["body"]


def test_the_quick_look_tells_the_next_job_whether_to_run(tmp_path, monkeypatch):
    import sys

    from .test_config import SHIPPED

    sys.path.insert(0, str(SHIPPED.parent / "scripts"))
    import automated_reviewer as entry

    trial = Trial(world=with_a_new_proposal())
    monkeypatch.setattr(entry, "HttpTransport", lambda token=None: trial.fake)
    output = tmp_path / "output.txt"
    said = []
    assert entry.main(["pending"], env=dict(ON, GITHUB_OUTPUT=str(output)), out=said.append) == 0
    assert said[-1] == "Proposals with something to do: 3." and output.read_text() == "work=true\n"
    assert entry.main(["pending"], env={"GITHUB_OUTPUT": str(output)}, out=said.append) == 0  # the switch is off
    assert said[-1] == "Proposals with something to do: 0." and output.read_text() == "work=true\nwork=false\n"
    assert trial.fake.writes() == []


def test_one_setting_has_the_maintainers_own_proposals_reviewed_too_and_they_stay_apart():
    import dataclasses

    world = World()
    own = issue(number=12, author=DECIDER, body=issue_text(login=DECIDER, maintainer=True), created_at="2026-11-18T00:00:00Z")
    world.add(own)
    included = dataclasses.replace(READY, reconcile_self_decided=True)
    trial = Trial(world=world, settings=included)
    summary = trial.run()
    assert summary.reviewed == [12, 2, 10] and summary.excluded == []
    record = trial.logged()[12]
    assert record["exclusion"] is None and record["self_decided"] is True and "self_decided" in record["text"]["flags"] and record["complete"] is True
    decide(trial.world, 12, "outcome:evidence", "2026-11-21T00:00:00Z")
    trial.run(now="2026-11-21T06:17:00Z")
    found = scoreboard.numbers(list(trial.logged().values()), included, POLICY, {2: {"label": "outcome:evidence"}, 10: {"label": "outcome:already-covered"}, 12: {"label": "outcome:evidence"}}, "2026-11-22T00:00:00Z")
    assert found["retrospective"]["proposals"] == [2, 10]  # his own are in no agreement figure
    assert found["reported_apart"]["the_maintainers_own"]["proposals"] == [12]
