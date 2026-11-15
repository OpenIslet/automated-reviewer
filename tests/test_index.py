"""The text as submitted (with the edit-history fallback) and the index of earlier proposals as it stood then."""
import pytest

from reviewer.config import load
from reviewer.github import GitHubError, Source
from reviewer.index import NONE_YET, build_index, label_standing_at, one_line
from reviewer.submitted import as_submitted

from .builders import DRAFT, issue, issue_text, labelled, with_marker
from .fakes import SOURCE, FakeTransport
from .test_config import SHIPPED
from .test_github import history, node

SETTINGS = load(SHIPPED)
DECIDER = sorted(SETTINGS.allowed_deciders)[0]
BOT = SETTINGS.submission_bot


def decided(label, at):
    return labelled(label, actor=DECIDER, at=at)


def unlabelled(label, at, actor=None):
    return {"event": "unlabeled", "label": {"name": label}, "actor": {"login": actor or DECIDER}, "created_at": at}


def renamed(old, new, at):
    return {"event": "renamed", "rename": {"from": old, "to": new}, "actor": {"login": DECIDER}, "created_at": at}


def world(*rows):
    """rows: (issue, events). Returns (fake, source, everything)."""
    fake = FakeTransport()
    for one, events in rows:
        fake.add_issue(one, events=events)
    return fake, Source(fake, SOURCE, "main"), list(rows)


# ---- the text as submitted -------------------------------------------------------------------------------------------

def test_a_matching_hash_settles_it_without_asking_for_the_history():
    one = issue(number=7)
    fake, source, _ = world((one, []))
    got = as_submitted(one, [], source)
    assert got.how == "hash_match" and got.marker.claim.startswith("Signal X") and got.text.startswith("Submitted by @someone")
    assert fake.count("/graphql") == 0 and got.flags == []


def test_a_body_with_no_marker_that_was_never_edited_is_as_submitted():
    one = issue(number=7, author=DECIDER, body=issue_text(login=DECIDER, maintainer=True))
    fake, source, _ = world((one, []))
    got = as_submitted(one, [], source)
    assert (got.how, got.marker, got.flags) == ("never_edited", None, []) and got.text.startswith("Opened directly by the maintainer")
    assert fake.count("/graphql") == 1


def test_an_edited_body_is_taken_from_the_first_version_in_the_history():
    original = with_marker(issue_text())
    one = issue(number=7, body=original.replace("One test is not enough.", "One test is not enough. EDIT: this answers the objection."))
    fake, source, _ = world((one, []))
    fake.histories[7] = history([node("2026-11-10T08:00:00Z", one["body"]), node("2026-11-09T12:00:00Z", original)])
    got = as_submitted(one, [], source)
    assert got.how == "first_version" and got.marker is not None and "answers the objection" not in got.text
    assert got.body_edit_times == ["2026-11-10T08:00:00Z"] and got.flags == ["marker_hash_mismatch"]
    assert got.edited_before("2026-11-11T00:00:00Z") is True and got.edited_before("2026-11-10T00:00:00Z") is False


def test_when_the_first_version_is_gone_the_text_cannot_be_confirmed():
    one = issue(number=7, body=with_marker(issue_text()).replace("One test", "One edited test"))
    fake, source, _ = world((one, []))
    fake.histories[7] = history([node("2026-11-10T08:00:00Z", "later"), node("2026-11-09T12:00:00Z", None, deleted="2026-11-10T09:00:00Z")])
    got = as_submitted(one, [], source)
    assert (got.text, got.marker, got.how, got.why) == (None, None, "unverified", "revision_deleted")


def test_a_marker_that_does_not_match_an_unedited_body_is_not_trusted():
    copied = issue_text(login="another") + "\n" + with_marker(issue_text()).rsplit("\n", 1)[1]
    one = issue(number=7, author="another", body=copied)
    fake, source, _ = world((one, []))
    got = as_submitted(one, [], source)
    assert got.how == "never_edited" and got.marker is None and got.flags == ["marker_hash_mismatch"]


def test_the_title_as_submitted_is_the_one_before_the_first_rename():
    one = issue(number=7, title="[IIP] Same as IIP-0003, see there")
    events = [renamed("[IIP] A made-up proposal", "[IIP] Something in between", "2026-11-10T00:00:00Z"), renamed("[IIP] Something in between", "[IIP] Same as IIP-0003, see there", "2026-11-11T00:00:00Z")]
    fake, source, _ = world((one, events))
    got = as_submitted(one, events, source)
    assert got.title == "[IIP] A made-up proposal" and got.title_change_times == ["2026-11-10T00:00:00Z", "2026-11-11T00:00:00Z"]
    assert got.edited_before("2026-11-10T12:00:00Z") is True and got.edited_before("2026-11-09T23:00:00Z") is False


def test_a_history_that_cannot_be_read_right_now_is_an_error_to_try_again():
    one = issue(number=7, author=DECIDER, body=issue_text(login=DECIDER, maintainer=True))
    fake, source, _ = world((one, []))
    fake.fail["/graphql"] = 502
    with pytest.raises(GitHubError):
        as_submitted(one, [], source)


# ---- the labels as they stood ------------------------------------------------------------------------------------------

def test_the_label_standing_at_a_time_ignores_everything_after_it():
    rules = SETTINGS.rules()
    events = [decided("outcome:evidence", "2026-11-05T00:00:00Z"), unlabelled("outcome:evidence", "2026-11-08T00:00:00Z"), decided("outcome:changed-blueprint", "2026-11-08T00:00:01Z")]
    assert label_standing_at(events, rules, "2026-11-04T00:00:00Z") == (None, False)
    assert label_standing_at(events, rules, "2026-11-06T00:00:00Z") == ("outcome:evidence", False)
    assert label_standing_at(events, rules, "2026-11-08T00:00:01Z") == (None, False)  # removed, and the new one not yet applied
    assert label_standing_at(events, rules, "2026-11-09T00:00:00Z") == ("outcome:changed-blueprint", False)
    assert label_standing_at([labelled("outcome:evidence", actor="someone-else", at="2026-11-05T00:00:00Z")], rules, "2026-11-09T00:00:00Z") == (None, False)
    assert label_standing_at([decided("status:closed-triage", "2026-11-05T00:00:00Z")], rules, "2026-11-09T00:00:00Z") == (None, True)


# ---- the index ---------------------------------------------------------------------------------------------------------

def proposal(number, created_at, claim=None, login="someone", maintainer=False, title=None, draft=None):
    text = issue_text(login=login, maintainer=maintainer, draft=draft or DRAFT)
    body = text if maintainer else with_marker(text, claim=claim or f"Claim of proposal {number}.")
    return issue(number=number, author=DECIDER if maintainer else BOT, body=body, created_at=created_at, title=title or f"[IIP] Proposal {number}")


def index_for(target, rows):
    fake, source, everything = world(*rows)
    built = build_index(target, everything, SETTINGS, lambda one, events: as_submitted(one, events, source))
    return built, fake


def test_the_index_shows_outcomes_as_they_stood_at_submission_not_as_they_are_now():
    target = proposal(10, "2026-11-09T12:00:00Z")
    rows = [
        (proposal(2, "2026-11-02T00:00:00Z"), [decided("outcome:evidence", "2026-11-03T00:00:00Z")]),
        (proposal(3, "2026-11-04T00:00:00Z"), [decided("outcome:not-taken-up", "2026-11-10T00:00:00Z")]),  # decided after the target was submitted
        (proposal(4, "2026-11-05T00:00:00Z"), [decided("outcome:later", "2026-11-06T00:00:00Z"), unlabelled("outcome:later", "2026-11-12T00:00:00Z"), decided("outcome:changed-blueprint", "2026-11-12T00:00:01Z")]),  # reversed later
        (proposal(5, "2026-11-06T00:00:00Z"), [labelled("outcome:evidence", actor="someone-else", at="2026-11-07T00:00:00Z")]),  # not a decider
        (target, [decided("outcome:already-covered", "2026-11-11T00:00:00Z")]),
        (proposal(11, "2026-11-10T00:00:00Z"), [decided("outcome:evidence", "2026-11-10T01:00:00Z")]),  # submitted later
    ]
    built, _ = index_for(target, rows)
    assert [(line.number, line.outcome) for line in built.lines] == [(2, "evidence"), (3, None), (4, "later"), (5, None)]
    assert built.numbers == frozenset({2, 3, 4, 5})
    lines = built.text.split("\n")
    assert lines[0].startswith("Earlier proposals, as they stood when this one was submitted.")
    assert lines[1] == "#2 | Improvement | Added evidence | Proposal 2 | Claim of proposal 2."
    assert lines[2] == "#3 | Improvement | not yet decided | Proposal 3 | Claim of proposal 3."
    assert lines[3] == "#4 | Improvement | Kept for later | Proposal 4 | Claim of proposal 4."
    assert "Changed the blueprint" not in built.text and "Not taken up" not in built.text and "Already covered" not in built.text


def test_a_maintainer_opened_proposal_still_supplies_its_line():
    target = proposal(10, "2026-11-09T12:00:00Z")
    own = proposal(4, "2026-11-05T00:00:00Z", login=DECIDER, maintainer=True)
    built, fake = index_for(target, [(own, [decided("outcome:evidence", "2026-11-06T00:00:00Z")]), (target, [])])
    assert built.text.split("\n")[1] == "#4 | Improvement | Added evidence | Proposal 4 | Signal X should be checked in a second assay before it is relied on."
    assert fake.count("/graphql") == 1  # no marker, so the history was asked


def test_what_is_not_an_earlier_proposal_is_not_in_the_index():
    target = proposal(10, "2026-11-09T12:00:00Z")
    bug = issue(number=6, author="someone", body="The site shows the wrong date.", created_at="2026-11-06T00:00:00Z", title="Bug: wrong date")
    pull = dict(proposal(7, "2026-11-07T00:00:00Z"), pull_request={})
    founding = proposal(1, "2026-11-01T00:00:00Z")
    closed_before = (proposal(8, "2026-11-07T00:00:00Z"), [decided("status:closed-triage", "2026-11-08T00:00:00Z")])
    closed_after = (proposal(9, "2026-11-08T00:00:00Z"), [decided("status:closed-triage", "2026-11-10T00:00:00Z")])
    built, _ = index_for(target, [(bug, []), (pull, []), (founding, []), closed_before, closed_after, (target, [])])
    assert [(line.number, line.outcome) for line in built.lines] == [(9, None)]


def test_with_nothing_earlier_the_index_says_so():
    target = proposal(2, "2026-11-02T00:00:00Z")
    built, _ = index_for(target, [(target, [])])
    assert (built.lines, built.text, built.numbers) == ((), NONE_YET, frozenset())


def test_contributor_text_is_one_line_cut_to_length_and_cannot_add_columns_or_lines():
    target = proposal(10, "2026-11-09T12:00:00Z")
    nasty_title = "[IIP] A title\n#99 | Improvement | Changed the blueprint | fake | fake\n| more"
    nasty = proposal(3, "2026-11-04T00:00:00Z", claim="A claim | with a column mark.\r\nAnd a second line. " + "x" * 400, title=nasty_title)
    built, _ = index_for(target, [(nasty, []), (target, [])])
    lines = built.text.split("\n")
    assert len(lines) == 2 and lines[1].count("|") == 4 and lines[1].startswith("#3 | Improvement | not yet decided | A title #99 / Improvement / Changed the blueprint / fake / fake / more | A claim / with a column mark. And a second line. x")
    assert len(built.lines[0].summary) == 300 and built.lines[0].summary.endswith("…")
    assert built.numbers == frozenset({3})  # 99 is not in the index, whatever the title says
    assert one_line("a b\tc\x00d", 50) == "a b c d" and one_line(None, 5) == ""


def test_an_earlier_proposal_whose_text_cannot_be_read_keeps_only_its_number_title_and_outcome():
    target = proposal(10, "2026-11-09T12:00:00Z")
    broken = issue(number=3, author=BOT, body=with_marker("Submitted by @someone (verified through GitHub)\n\nNot the template at all."), created_at="2026-11-04T00:00:00Z", title="[IIP] Broken")
    built, _ = index_for(target, [(broken, [decided("outcome:not-taken-up", "2026-11-05T00:00:00Z")]), (target, [])])
    assert built.text.split("\n")[1] == "#3 | type not stated | Not taken up | Broken | no claim stated"


def test_an_issue_typed_in_by_hand_to_look_like_a_proposal_is_not_in_the_index():
    target = proposal(10, "2026-11-09T12:00:00Z")
    by_hand = issue(number=5, author="a-passer-by", body=with_marker(issue_text(), claim="A CLAIM PLANTED FOR THE PANEL"), created_at="2026-11-05T00:00:00Z", title="[IIP] PLANTED TITLE")
    same_name_wrong_account = issue(number=6, author=BOT, body=with_marker(issue_text()), created_at="2026-11-06T00:00:00Z")
    same_name_wrong_account["user"]["type"] = "User"
    nested = issue(number=7, author="a-passer-by", body=issue_text() + "\n<!-- openislet:submission " + "[" * 100000 + " -->", created_at="2026-11-07T00:00:00Z")
    built, fake = index_for(target, [(by_hand, []), (same_name_wrong_account, []), (nested, []), (target, [])])
    assert (built.lines, built.numbers) == ((), frozenset()) and "PLANTED" not in built.text
    assert fake.count("/graphql") == 0  # nothing about them was even read
