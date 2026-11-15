"""The repository context: read from the commit before the proposal, with versioned file names resolved there."""
import pytest

from reviewer.config import load
from reviewer.context import ContextReader, ContextUnavailable, resolve
from reviewer.github import Source

from .fakes import SOURCE, FakeTransport
from .test_config import SHIPPED

FILES_V1 = {
    "ISLET-1-BLUEPRINT.md": "Blueprint, first version.",
    "docs/STAGE-0-EXECUTION-PLAYBOOK.md": "Playbook, first version.",
    "data/graveyard/idea_graveyard_v0.5.json": '{"entries": []}',
    "README.md": "Read me.",
}


def world():
    fake = FakeTransport()
    fake.add_commit("aaa1", "2026-11-01T05:00:00Z", FILES_V1)
    fake.add_commit("bbb2", "2026-11-05T10:00:00Z", {"ISLET-1-BLUEPRINT.md": "Blueprint, second version, with the change the proposal asked for."})
    fake.add_commit("ccc3", "2026-11-12T10:00:00Z", {"data/graveyard/idea_graveyard_v0.6.json": '{"entries": ["newer"]}'}, remove=["data/graveyard/idea_graveyard_v0.5.json"])
    return fake


def reader(fake):
    return ContextReader(Source(fake, SOURCE, "main"), load(SHIPPED).context_files)


def test_context_comes_from_the_commit_before_the_proposal_never_the_head():
    context = reader(world()).context_for("2026-11-03T00:00:00Z")
    assert context.commit == "aaa1"
    texts = {f.name: f.text for f in context.files}
    assert texts["blueprint"] == "Blueprint, first version."
    assert "the change the proposal asked for" not in "".join(texts.values())
    assert [f.name for f in context.files] == ["blueprint", "playbook", "idea_graveyard"]  # the playbook is always there


def test_a_versioned_file_name_is_resolved_at_that_commit():
    fake = world()
    early = reader(fake).context_for("2026-11-06T00:00:00Z")
    late = reader(fake).context_for("2026-11-13T00:00:00Z")
    assert {f.name: f.path for f in early.files}["idea_graveyard"] == "data/graveyard/idea_graveyard_v0.5.json"
    assert {f.name: f.path for f in late.files}["idea_graveyard"] == "data/graveyard/idea_graveyard_v0.6.json"
    assert early.commit == "bbb2" and late.commit == "ccc3"
    assert "README.md" in early.paths and early.full_paths() == {"ISLET-1-BLUEPRINT.md", "docs/STAGE-0-EXECUTION-PLAYBOOK.md", "data/graveyard/idea_graveyard_v0.5.json"}


def test_when_two_versions_are_present_the_highest_is_used_and_noted():
    fake = world()
    fake.add_commit("ddd4", "2026-11-13T10:00:00Z", {"data/graveyard/idea_graveyard_v0.10.json": "{}"})
    context = reader(fake).context_for("2026-11-14T00:00:00Z")
    assert {f.name: f.path for f in context.files}["idea_graveyard"] == "data/graveyard/idea_graveyard_v0.10.json"  # 0.10 is after 0.6
    assert context.notes == ["several_versions:idea_graveyard"]


def test_resolve_only_accepts_a_version_number_where_the_star_is():
    paths = {"a/file_v1.json", "a/file_v1.2.json", "a/file_vfinal.json", "a/file_v2.json.bak", "b/file_v9.json"}
    assert resolve("a/file_v*.json", paths) == ("a/file_v1.2.json", True)
    assert resolve("a/file_v*.json", {"a/file_vfinal.json"}) == (None, False)
    assert resolve("a/plain.md", paths) == (None, False) and resolve("b/file_v9.json", paths) == ("b/file_v9.json", False)


def test_no_commit_before_the_proposal_means_no_context():
    with pytest.raises(ContextUnavailable) as error:
        reader(world()).context_for("2026-10-31T00:00:00Z")
    assert str(error.value) == "no_commit_before_submission"


def test_a_missing_or_unreadable_file_means_no_context():
    fake = FakeTransport()
    fake.add_commit("aaa1", "2026-11-01T05:00:00Z", {k: v for k, v in FILES_V1.items() if "PLAYBOOK" not in k})
    with pytest.raises(ContextUnavailable) as error:
        reader(fake).context_for("2026-11-03T00:00:00Z")
    assert str(error.value) == "file_missing:playbook"

    fake = world()
    fake.fail["ISLET-1-BLUEPRINT.md"] = 503
    with pytest.raises(ContextUnavailable) as error:
        reader(fake).context_for("2026-11-03T00:00:00Z")
    assert str(error.value) == "file_unreadable:blueprint"

    fake = FakeTransport()
    fake.add_commit("aaa1", "2026-11-01T05:00:00Z", dict(FILES_V1, **{"ISLET-1-BLUEPRINT.md": b"\xff\xfe\x00binary"}))
    with pytest.raises(ContextUnavailable) as error:
        reader(fake).context_for("2026-11-03T00:00:00Z")
    assert str(error.value) == "file_not_text:blueprint"

    fake = world()
    fake.fail["/commits?"] = 500
    with pytest.raises(ContextUnavailable) as error:
        reader(fake).context_for("2026-11-03T00:00:00Z")
    assert str(error.value) == "repository_unreadable"


def test_proposals_that_share_a_commit_share_its_files():
    fake = world()
    one = reader(fake)
    first = one.context_for("2026-11-02T00:00:00Z")
    second = one.context_for("2026-11-04T00:00:00Z")
    assert first.commit == second.commit == "aaa1"
    assert fake.count("/git/trees/") == 1 and fake.count("raw.githubusercontent.com") == 3
    assert first.files[0].sha256 == second.files[0].sha256 and len(first.files[0].sha256) == 64
