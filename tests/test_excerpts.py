"""Passages from cited files: found by the file list, taken from the context commit, inside a cap, with no model call."""
from reviewer.context import ContextReader
from reviewer.excerpts import Citation, build, find_citations
from reviewer.github import Source

from .fakes import SOURCE, FakeTransport

PROTOCOL = "\n".join(f"protocol line {n}" for n in range(1, 301)) + "\n"
PATHS = {
    "ISLET-1-BLUEPRINT.md",
    "docs/STAGE-0-EXECUTION-PLAYBOOK.md",
    "data/graveyard/idea_graveyard_v0.5.json",
    "data/protocols/stage0_decision_tree_v1.json",
    "data/protocols/notes.md",
    "docs/notes.md",
    "data/extracted/12345678.json",
    "README.md",
}


def cited(draft):
    return find_citations(draft, PATHS)


def test_a_path_is_recognised_with_each_way_of_giving_lines():
    tree = "data/protocols/stage0_decision_tree_v1.json"
    assert cited(f"See `{tree}:120-135` for the rule.") == [Citation(tree, 120, 135, True)]
    assert cited(f"See {tree}:42.") == [Citation(tree, 42, 42, True)]
    assert cited(f"See {tree}#L10-L20 and more.") == [Citation(tree, 10, 20, True)]
    assert cited(f"See `{tree}` (lines 200–210).") == [Citation(tree, 200, 210, True)]
    assert cited(f"See **{tree}**, line 7, where it says so.") == [Citation(tree, 7, 7, True)]
    assert cited(f"See {tree} lines 30 to 12.") == [Citation(tree, 12, 30, True)]
    assert cited(f"The file {tree} sets this.") == [Citation(tree, None, None, True)]
    assert cited(f"https://github.com/OpenIslet/openislet/blob/main/{tree}#L5") == [Citation(tree, 5, 5, True)]


def test_a_number_that_is_not_a_line_reference_is_not_taken_for_one():
    tree = "data/protocols/stage0_decision_tree_v1.json"
    assert cited(f"{tree} has 14 branches, and line counts vary.") == [Citation(tree, None, None, True)]
    assert cited(f"{tree}:0") == [Citation(tree, None, None, True)]
    assert cited(f"{tree} lines 1 to 90000") == [Citation(tree, None, None, True)]


def test_a_bare_file_name_counts_only_when_one_file_has_it():
    assert cited("The decision tree, stage0_decision_tree_v1.json, says otherwise.") == [Citation("data/protocols/stage0_decision_tree_v1.json", None, None, True)]
    assert cited("As notes.md says.") == []  # two files are called notes.md
    assert cited("As README.md says.") == [Citation("README.md", None, None, True)]
    assert cited("A study file, 12345678.json, holds it.") == [Citation("data/extracted/12345678.json", None, None, True)]


def test_a_longer_name_that_only_starts_like_a_file_is_not_that_file():
    assert cited("See README.md.bak and README.mdx and myREADME.md.") == []
    assert cited("See other/README.md here.") == []
    assert cited("See README.md. Then stop.") == [Citation("README.md", None, None, True)]


def test_a_path_that_is_not_there_is_reported_and_other_addresses_are_ignored():
    assert cited("See data/extracted/context_evidence_graph_v1.json for it.") == [Citation("data/extracted/context_evidence_graph_v1.json", None, None, False)]
    assert cited("See https://example.org/data/file.json and https://doi.org/10.1000/abc.def and src/main.py.") == []
    assert cited("and/or 3.5/4.0 either/or.txt") == []


def test_citations_come_back_in_the_order_first_cited():
    draft = "First docs/notes.md:3, then data/protocols/notes.md, then docs/notes.md:250 again."
    assert [(c.path, c.first) for c in cited(draft)] == [("docs/notes.md", 3), ("data/protocols/notes.md", None), ("docs/notes.md", 250)]


# ---- building the block ------------------------------------------------------------------------------------------------

def setup(extra=None):
    fake = FakeTransport()
    files = {
        "ISLET-1-BLUEPRINT.md": "Blueprint.",
        "docs/STAGE-0-EXECUTION-PLAYBOOK.md": "Playbook.",
        "data/graveyard/idea_graveyard_v0.5.json": "{}",
        "data/protocols/protocol.md": PROTOCOL,
        "data/protocols/other.md": "\n".join(f"other line {n}" for n in range(1, 21)),
        "data/structures/shape.bin": b"\xff\xfe\x00\x01",
    }
    files.update(extra or {})
    fake.add_commit("aaa1", "2026-11-01T05:00:00Z", files)
    fake.add_commit("bbb2", "2026-11-20T05:00:00Z", {"data/protocols/protocol.md": "CHANGED AFTER THE PROPOSAL\n", "data/protocols/later.md": "added later"})
    patterns = (("blueprint", "ISLET-1-BLUEPRINT.md"), ("playbook", "docs/STAGE-0-EXECUTION-PLAYBOOK.md"), ("idea_graveyard", "data/graveyard/idea_graveyard_v*.json"))
    reader = ContextReader(Source(fake, SOURCE, "main"), patterns)
    return fake, reader, reader.context_for("2026-11-09T12:00:00Z")


def excerpts(draft, extra=None, cap=15000):
    fake, reader, context = setup(extra)
    before = len(fake.requests)
    result = build(draft, context, reader, either_side=40, top_lines=80, max_tokens=cap)
    return result, fake, before


def test_cited_lines_come_with_forty_either_side_from_the_context_commit():
    result, _, _ = excerpts("See data/protocols/protocol.md:100-102.")
    assert result.included == [("data/protocols/protocol.md", 60, 142)]
    assert result.text.startswith("File: data/protocols/protocol.md, lines 60 to 142 of 300\n60\tprotocol line 60\n")
    assert "142\tprotocol line 142" in result.text and "protocol line 143" not in result.text and "protocol line 59\n" not in result.text
    assert "CHANGED AFTER THE PROPOSAL" not in result.text


def test_with_no_line_given_the_first_eighty_lines_are_sent():
    result, _, _ = excerpts("See data/protocols/protocol.md and data/protocols/other.md.")
    assert result.included == [("data/protocols/protocol.md", 1, 80), ("data/protocols/other.md", 1, 20)]
    assert "80\tprotocol line 80" in result.text and "protocol line 81" not in result.text


def test_passages_from_one_file_are_merged_where_they_touch():
    result, _, _ = excerpts("See data/protocols/protocol.md:100, data/protocols/protocol.md:150 and data/protocols/protocol.md:290.")
    assert result.included == [("data/protocols/protocol.md", 60, 190), ("data/protocols/protocol.md", 250, 300)]


def test_files_already_sent_in_full_are_skipped_and_said_so():
    result, fake, before = excerpts("See ISLET-1-BLUEPRINT.md:10 and the STAGE-0-EXECUTION-PLAYBOOK.md.")
    assert result.included == [] and result.already_in_full == ["ISLET-1-BLUEPRINT.md", "docs/STAGE-0-EXECUTION-PLAYBOOK.md"]
    assert result.text == "Cited, and included in full above: ISLET-1-BLUEPRINT.md, docs/STAGE-0-EXECUTION-PLAYBOOK.md"
    assert len(fake.requests) == before  # nothing more was fetched


def test_what_does_not_fit_under_the_cap_is_listed_by_name_and_never_cut():
    result, _, _ = excerpts("See data/protocols/protocol.md:100 and data/protocols/other.md.", cap=200)
    assert result.included == [("data/protocols/other.md", 1, 20)]
    assert result.by_name_only == [("data/protocols/protocol.md", "over_the_cap")]
    assert result.tokens <= 200
    assert "Cited, but not included here (too long, or not text): data/protocols/protocol.md" in result.text
    assert "protocol line" not in result.text


def test_the_total_stays_inside_the_cap_however_much_is_cited():
    many = {f"data/protocols/p{n}.md": PROTOCOL for n in range(30)}
    draft = " ".join(f"data/protocols/p{n}.md:150" for n in range(30))
    result, _, _ = excerpts(draft, extra=many, cap=3000)
    assert 0 < result.tokens <= 3000 and len(result.included) < 30
    assert len(result.included) + len(result.by_name_only) == 30


def test_a_file_that_is_missing_too_large_or_not_text_is_reported_not_guessed():
    big = {"data/protocols/big.json": b"x" * 2_000_001}
    draft = "See data/protocols/later.md, data/protocols/big.json:5, data/structures/shape.bin and data/protocols/other.md:900."
    result, fake, _ = excerpts(draft, extra=big)
    assert result.missing == ["data/protocols/later.md"]  # it was added after the proposal
    assert result.by_name_only == [("data/protocols/big.json", "file_too_large"), ("data/structures/shape.bin", "not_text"), ("data/protocols/other.md", "cited_lines_past_the_end")]
    assert fake.count("big.json") == 0  # too large to fetch at all
    # Its name is the contributor's writing, so it is kept out of the trusted text and sent inside an untrusted part.
    assert "later.md" not in result.text
    assert result.missing_text == "Files the proposal names that were not in the project's repository at that commit: data/protocols/later.md"
    assert result.included == []


def test_a_draft_that_cites_nothing_gets_an_empty_block_and_no_requests():
    result, fake, before = excerpts("Signal X should be checked in a second assay. One test is not enough.")
    assert result.text == "" and result.tokens == 0 and len(fake.requests) == before


def test_the_list_of_files_that_are_not_there_is_short_however_many_are_named():
    draft = " ".join(f"data/protocols/not-there-{n}.md" for n in range(60)) + " data/" + "x" * 200 + ".md"
    result, _, _ = excerpts(draft)
    assert len(result.missing) == 20 and all(len(path) <= 120 for path in result.missing) and result.text == ""
