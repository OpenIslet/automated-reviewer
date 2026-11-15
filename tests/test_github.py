"""Reading the source repository and writing to the home repository, against a stand-in for GitHub."""
import pytest

from reviewer import github
from reviewer.github import API, RAW, GitHubError, Home, Source, request_headers

from .builders import issue, labelled
from .fakes import HOME, SOURCE, FakeTransport


def source_with(fake):
    return Source(fake, SOURCE, "main")


# ---- reading the source repository -----------------------------------------------------------------------------------

def test_issues_come_back_oldest_first_across_pages():
    fake = FakeTransport()
    for n in range(1, 231):
        fake.add_issue(issue(number=n, created_at=f"2026-11-{1 + n // 20:02d}T00:00:{n % 60:02d}Z"))
    listed = source_with(fake).list_issues()
    assert len(listed) == 230
    assert [i["created_at"] for i in listed] == sorted(i["created_at"] for i in listed)
    assert fake.count("/issues?") == 3  # 100, 100, 30


def test_label_events_and_comments_are_read_in_full():
    fake = FakeTransport()
    events = [labelled("status:queued", at=f"2026-11-09T12:{n // 60:02d}:{n % 60:02d}Z") for n in range(150)]
    fake.add_issue(issue(number=7), events=events, comments=[{"id": 1, "body": "x", "user": {"login": "someone", "type": "User"}}])
    source = source_with(fake)
    assert len(source.events(7)) == 150 and len(source.comments(7)) == 1
    assert source.issue(7)["number"] == 7


def test_the_context_commit_is_the_last_one_strictly_before_the_proposal():
    fake = FakeTransport()
    fake.add_commit("aaa1", "2026-11-01T05:00:00Z", {"ISLET-1-BLUEPRINT.md": "first"})
    fake.add_commit("bbb2", "2026-11-05T10:00:00Z", {"ISLET-1-BLUEPRINT.md": "second"})
    fake.add_commit("ccc3", "2026-11-09T12:00:00Z", {"ISLET-1-BLUEPRINT.md": "third"})
    source = source_with(fake)
    assert source.commit_before("2026-11-09T11:59:59Z") == {"sha": "bbb2", "date": "2026-11-05T10:00:00Z"}
    # A commit dated at the very second the proposal was created is not "before" it.
    assert source.commit_before("2026-11-09T12:00:00Z")["sha"] == "bbb2"
    assert source.commit_before("2026-11-09T12:00:01Z")["sha"] == "ccc3"
    assert source.commit_before("2026-11-01T05:00:00Z") is None
    assert source.commit_before("2026-10-01T00:00:00Z") is None


def test_files_are_listed_and_read_as_they_were_at_a_commit():
    fake = FakeTransport()
    fake.add_commit("aaa1", "2026-11-01T05:00:00Z", {"ISLET-1-BLUEPRINT.md": "first", "data/graveyard/idea_graveyard_v0.5.json": "{}"})
    fake.add_commit("bbb2", "2026-11-05T10:00:00Z", {"ISLET-1-BLUEPRINT.md": "second", "data/graveyard/idea_graveyard_v0.6.json": "{}"}, remove=["data/graveyard/idea_graveyard_v0.5.json"])
    source = source_with(fake)
    assert sorted(source.tree("aaa1")) == ["ISLET-1-BLUEPRINT.md", "data/graveyard/idea_graveyard_v0.5.json"]
    assert sorted(source.tree("bbb2")) == ["ISLET-1-BLUEPRINT.md", "data/graveyard/idea_graveyard_v0.6.json"]
    assert source.tree("aaa1")["ISLET-1-BLUEPRINT.md"]["size"] == 5
    assert source.file_at("aaa1", "ISLET-1-BLUEPRINT.md") == b"first"
    assert source.file_at("bbb2", "ISLET-1-BLUEPRINT.md") == b"second"
    # Files come from the raw address, by commit, so a big file is no different from a small one.
    assert fake.count(f"{RAW}/{SOURCE}/aaa1/") == 1 and fake.count("/contents/") == 0


def test_a_file_list_that_was_cut_short_is_an_error_not_a_shorter_list():
    fake = FakeTransport()
    fake.add_commit("aaa1", "2026-11-01T05:00:00Z", {"a.md": "x"})
    fake.truncated_trees = True
    with pytest.raises(GitHubError):
        source_with(fake).tree("aaa1")


def test_a_failed_request_reports_its_kind_and_status_only():
    fake = FakeTransport()
    fake.add_issue(issue(number=7))
    fake.fail["/issues/7"] = 502
    with pytest.raises(GitHubError) as error:
        source_with(fake).issue(7)
    assert str(error.value) == "read issue: status 502" and error.value.status == 502
    with pytest.raises(GitHubError):
        source_with(fake).file_at("aaa1", "missing.md")
    with pytest.raises(GitHubError):
        source_with(fake).issue(99)


# ---- the edit history --------------------------------------------------------------------------------------------------

def history(nodes, last_edited="2026-11-10T00:00:00Z", includes_created=True, more=False):
    return {"createdAt": "2026-11-09T12:00:00Z", "lastEditedAt": last_edited, "includesCreatedEdit": includes_created, "userContentEdits": {"totalCount": len(nodes), "pageInfo": {"hasNextPage": more}, "nodes": nodes}}


def node(at, text, deleted=None):
    return {"createdAt": at, "deletedAt": deleted, "diff": text}


def test_an_issue_that_was_never_edited_says_so():
    fake = FakeTransport()
    fake.add_issue(issue(number=7))
    assert source_with(fake).edit_history(7) == {"edited": False}


def test_the_first_version_is_recovered_from_the_history_whatever_order_it_arrives_in():
    fake = FakeTransport()
    fake.add_issue(issue(number=7))
    newest_first = [node("2026-11-11T00:00:00Z", "third"), node("2026-11-10T00:00:00Z", "second"), node("2026-11-09T12:00:00Z", "as submitted")]
    fake.histories[7] = history(newest_first)
    assert source_with(fake).edit_history(7) == {"edited": True, "original": "as submitted", "edit_times": ["2026-11-10T00:00:00Z", "2026-11-11T00:00:00Z"]}


def test_a_history_that_cannot_give_the_first_version_says_why():
    fake = FakeTransport()
    fake.add_issue(issue(number=7))
    source = source_with(fake)
    fake.histories[7] = history([node("2026-11-10T00:00:00Z", "second"), node("2026-11-09T12:00:00Z", None, deleted="2026-11-10T01:00:00Z")])
    assert source.edit_history(7) == {"edited": True, "original": None, "why": "revision_deleted", "edit_times": ["2026-11-10T00:00:00Z"]}
    fake.histories[7] = history([node("2026-11-10T00:00:00Z", "second")], includes_created=False)
    assert source.edit_history(7)["why"] == "history_does_not_start_at_creation"
    fake.histories[7] = history([node("2026-11-09T12:00:00Z", "first")], more=True)
    assert source.edit_history(7)["why"] == "history_too_long"
    fake.histories[7] = history([], last_edited="2026-11-10T00:00:00Z", includes_created=False)
    assert source.edit_history(7)["original"] is None


def test_an_unreadable_history_is_an_error():
    fake = FakeTransport()
    fake.add_issue(issue(number=7))
    fake.fail["/graphql"] = 502
    with pytest.raises(GitHubError):
        source_with(fake).edit_history(7)
    fake.fail.clear()
    with pytest.raises(GitHubError):
        source_with(fake).edit_history(99)  # no such issue


def test_the_edit_history_request_only_reads():
    assert github.EDIT_HISTORY_QUERY.lstrip().startswith("query") and "mutation" not in github.EDIT_HISTORY_QUERY


# ---- nothing is ever written to the source repository ------------------------------------------------------------------

def test_the_source_reader_has_no_way_to_write():
    fake = FakeTransport()
    fake.add_issue(issue(number=7), events=[labelled("outcome:evidence")])
    fake.add_commit("aaa1", "2026-11-01T05:00:00Z", {"a.md": "x"})
    source = source_with(fake)
    source.list_issues(), source.issue(7), source.events(7), source.comments(7), source.commit_before("2026-11-09T00:00:00Z"), source.tree("aaa1"), source.file_at("aaa1", "a.md"), source.edit_history(7)
    assert fake.writes() == []
    assert not [name for name in dir(Source) if name.startswith(("create", "update", "set", "delete", "post", "add", "close", "label", "_write"))]


def test_the_home_writer_only_ever_addresses_the_home_repository():
    fake = FakeTransport()
    fake.add_home_issue(1)
    fake.add_home_issue(2)
    home = Home(fake, HOME, SOURCE)
    first = home.create_comment(1, "one")
    home.update_comment(first["id"], "one, edited")
    home.set_issue_body(2, "the scoreboard")
    assert [c["body"] for c in home.comments(1)] == ["one, edited"] and home.issue(2)["body"] == "the scoreboard"
    assert len(fake.writes()) == 3
    assert all(url.startswith(f"{API}/repos/{HOME}/") for _, url in fake.writes())
    assert not any(SOURCE in url for _, url in fake.requests)


def test_the_home_repository_cannot_be_the_source_repository():
    with pytest.raises(ValueError):
        Home(FakeTransport(), SOURCE, SOURCE)
    with pytest.raises(ValueError):
        Home(FakeTransport(), SOURCE.lower(), SOURCE)


def test_text_longer_than_github_allows_is_refused_before_any_request():
    fake = FakeTransport()
    fake.add_home_issue(1)
    home = Home(fake, HOME, SOURCE)
    with pytest.raises(ValueError):
        home.create_comment(1, "x" * 65537)
    assert fake.requests == []
    assert home.create_comment(1, "x" * 65536)["id"]


def test_a_refused_write_is_an_error():
    fake = FakeTransport()
    with pytest.raises(GitHubError):
        Home(fake, HOME, SOURCE).create_comment(5, "no such issue")


# ---- the token ---------------------------------------------------------------------------------------------------------

def test_the_token_is_sent_to_the_api_and_never_to_the_file_address():
    to_api = request_headers("secret-token", f"{API}/repos/{SOURCE}/issues/7", "application/vnd.github+json", False)
    to_raw = request_headers("secret-token", f"{RAW}/{SOURCE}/aaa1/a.md", "*/*", False)
    elsewhere = request_headers("secret-token", "https://api.github.com.example.org/x", "*/*", False)
    assert to_api["Authorization"] == "Bearer secret-token"
    assert "Authorization" not in to_raw and "Authorization" not in elsewhere
    assert "Authorization" not in request_headers(None, f"{API}/graphql", "application/vnd.github+json", True)
