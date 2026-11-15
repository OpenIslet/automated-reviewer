"""The input side: confirming the text, reading the body, and deciding whether a proposal is in the trial."""
import pytest

from reviewer.decisions import Rules, standing_of
from reviewer.integrity import body_sha256, canonical_body, confirm
from reviewer.issuebody import Unparsed, clean_title, parse_body, strip_ai_tools_section, verdict_of

from .builders import DRAFT, REVIEW, issue, issue_text, labelled, with_marker

RULES = Rules(
    decision_labels=frozenset({"outcome:changed-blueprint", "outcome:evidence", "outcome:not-taken-up", "outcome:already-covered", "decision:needs-work", "outcome:spam"}),
    triage_label="status:closed-triage",
    allowed_deciders=frozenset({"maintainer"}),
    skip_issues=frozenset({1}),
)


# ---- confirming the text ---------------------------------------------------------------------------------------

def test_a_body_as_the_backend_wrote_it_matches_its_hash():
    text = issue_text()
    above, marker, how = confirm(with_marker(text))
    assert how == "hash_match"
    assert canonical_body(above) == canonical_body(text)
    assert marker.pmids == ("12345678",) and marker.claim.startswith("Signal X")


def test_windows_line_endings_and_trailing_space_do_not_break_the_hash():
    body = with_marker(issue_text()).replace("\n", "\r\n") + "\r\n  ﻿"
    assert confirm(body)[2] == "hash_match"


def test_an_edited_body_no_longer_matches():
    body = with_marker(issue_text()).replace("One test is not enough.", "One test is plenty.")
    assert confirm(body)[2] == "hash_mismatch"


def test_a_marker_copied_onto_other_text_does_not_match():
    marker_line = with_marker(issue_text()).rsplit("\n", 1)[1]
    assert confirm(issue_text(login="another") + "\n" + marker_line)[2] == "hash_mismatch"


def test_no_marker_a_broken_marker_and_a_second_marker_are_each_reported():
    assert confirm(issue_text(maintainer=True))[2] == "no_marker"
    assert confirm(issue_text() + "\n<!-- openislet:submission {not json} -->")[2] == "marker_unreadable"
    assert confirm(issue_text() + '\n<!-- openislet:submission {"body_sha256":"abc"} -->')[2] == "marker_unreadable"
    twice = with_marker(issue_text() + "\n<!-- openislet:submission {} -->\n")
    assert confirm(twice)[2] == "marker_repeated"
    assert confirm(with_marker(issue_text()) + "\nmore text")[2] == "marker_misplaced"


def test_the_hash_is_the_plain_sha256_of_the_canonical_text():
    # A fixed answer, so a change to the canonical form cannot pass unnoticed.
    assert body_sha256("a\r\nb \n") == "7e18f737311b2dc3b2f269dd78396b0351f14fb66efa879f768cb23181883c78"
    assert canonical_body("a\r\nb \n\n") == "a\nb"
    assert body_sha256("a\r\nb \n") == body_sha256("a\nb")


# ---- reading the body --------------------------------------------------------------------------------------------

def test_both_body_shapes_parse():
    bot = parse_body(confirm(with_marker(issue_text(login="some-one")))[0])
    assert (bot.shape, bot.submitter) == ("bot", "some-one")
    own = parse_body(issue_text(login="maintainer", maintainer=True))
    assert (own.shape, own.submitter) == ("maintainer", "maintainer")
    assert own.claim == "Signal X should be checked in a second assay before it is relied on."


def test_identity_and_ai_tools_are_kept_out_of_what_a_model_sees():
    text = issue_text(login="some-one", orcid="0000-0002-1825-0097", stated="name A Person, X [x.com/aperson](https://x.com/aperson)")
    parsed = parse_body(text)
    seen = parsed.draft_for_models + (parsed.wetlab_for_models or "") + parsed.written_against
    for hidden in ("some-one", "0000-0002-1825-0097", "A Person", "aperson", "ExampleModel", "OtherModel", "Partly supported", "Mostly right", "Consent"):
        assert hidden not in seen, hidden
    assert parsed.ai_draft == "ExampleModel 9" and parsed.ai_review == "OtherModel 3"
    assert parsed.review_verdict == "Partly supported"


def test_the_ai_tools_section_goes_and_lower_headings_elsewhere_stay():
    kept, removed = strip_ai_tools_section(DRAFT)
    assert "### 1. Add the second assay" in kept and "### 2. Record both" in kept and "## Cheapest way to check it" in kept
    assert "AI tools used" not in kept and "checked by hand" not in kept
    assert removed.startswith("## AI tools used") and "### Notes" in removed


def test_a_section_after_ai_tools_is_kept_and_a_heading_inside_a_code_block_is_not_a_heading():
    draft = "## Evidence\n\n```\n## AI tools used\n```\n\n## AI tools used\n\nA model.\n\n## Afterword\n\nKept."
    kept, removed = strip_ai_tools_section(draft)
    assert "## Afterword" in kept and "Kept." in kept and "```\n## AI tools used\n```" in kept
    assert removed == "## AI tools used\n\nA model."
    assert strip_ai_tools_section("## Evidence\n\nNothing else.") == ("## Evidence\n\nNothing else.", None)


def test_code_fences_are_followed_the_way_github_shows_them_so_the_section_is_still_found():
    # A line that opens with backticks and has more of them later is a code span, not a fence.
    span = "## Evidence\n\n``` a code span ``` in a sentence.\n\n## AI tools used\n\nA model.\n\n## Afterword\n\nKept."
    assert strip_ai_tools_section(span)[1] == "## AI tools used\n\nA model."
    # Inside a fence, a line of backticks followed by a word does not close it.
    inner = "```text\ncode\n```python\nmore code\n```\n\n## AI tools used\n\nA model."
    kept, removed = strip_ai_tools_section(inner)
    assert removed == "## AI tools used\n\nA model." and kept.endswith("more code\n```")
    # A closing run of hashes is part of the heading's marks, not its name.
    assert strip_ai_tools_section("## AI tools used ##\n\nA model.")[1] == "## AI tools used ##\n\nA model."


@pytest.mark.parametrize(
    "heading",
    [
        " ## AI tools used",  # indented
        "## AI tools used in this draft",
        "## AI tools I used",
        "AI tools used\n-------------",  # the other way to write a heading
        "<h2>AI tools used</h2>",
        "~~~\n## AI tools used",  # inside a code fence that is never closed
        "## AI tools used\n\nA first one.\n\n## AI tools used",  # given twice
        "<h3>AI tools</h3>",
        "    ### AI tools used",
        "> ## AI tools used",
        "AI tools:",  # a label with nothing after it: how far it runs cannot be known
        "Drafted with: AI tools (ExampleModel 9)",  # named after the colon
        "**AI tools**",
        "| AI tools used | ExampleModel 9 |",
    ],
)
def test_a_draft_whose_ai_tools_section_cannot_be_removed_is_refused(heading):
    with pytest.raises(Unparsed) as refused:
        parse_body(issue_text(draft=DRAFT.replace("## AI tools used", heading)))
    assert str(refused.value) == "ai_tools_section_not_removed"


@pytest.mark.parametrize(
    "paragraph",
    [
        "**AI tools (self-reported):** draft = ExampleModel 9 (a made-up version). blind review = OtherModel 3, in a second sitting.",
        "AI tools: ExampleModel 9 wrote the first version\nand OtherModel 3 checked it.",  # the paragraph runs to the blank line
        "- AI tools used: ExampleModel 9",
        "> **AI tools used**: ExampleModel 9",
        "AI tool:\n- ExampleModel 9\n- OtherModel 3",  # nothing on the label's own line, a list straight under it
    ],
)
def test_a_paragraph_that_opens_with_a_label_naming_ai_tools_is_removed_like_the_section(paragraph):
    before_the_section = DRAFT.split("## AI tools used")[0]
    for draft in (before_the_section + paragraph + "\n\n## Afterword\n\nKept.", paragraph + "\n\n" + DRAFT):  # in place of the section, or as well as it
        parsed = parse_body(issue_text(draft=draft))
        assert "ExampleModel" not in parsed.draft_for_models and "OtherModel" not in parsed.draft_for_models and "AI tool" not in parsed.draft_for_models
        assert "## Evidence" in parsed.draft_for_models and paragraph in parsed.draft_ai_tools_section and "draft_has_no_ai_tools_section" not in parsed.notes
    assert "Kept." in parse_body(issue_text(draft=before_the_section + paragraph + "\n\n## Afterword\n\nKept.")).draft_for_models
    # The paragraph ends at a heading even with no blank line between them.
    assert "## Afterword" in parse_body(issue_text(draft=before_the_section + paragraph + "\n## Afterword\n\nKept.")).draft_for_models


def test_a_sentence_that_mentions_ai_tools_in_passing_is_not_a_reason_to_refuse():
    said_in_passing = [
        "A made-up study reports the effect in one assay only, and no AI tools were involved in running that assay at all.",
        "- No AI tools can substitute for primary data from the second assay.",  # a short list item
        "prior computational screens and AI tools failed to predict",  # one line of a wrapped paragraph
        "| Method | AI tools | Result |",  # a table that is about something else
        "The second assay may not apply, since the AI tools were trained on other data: see the first section.",
    ]
    prose = DRAFT.replace("A made-up study reports the effect in one assay only.", "\n".join(said_in_passing))
    kept = parse_body(issue_text(draft=prose)).draft_for_models
    assert all(line in kept for line in said_in_passing)


def test_a_draft_ai_tool_list_with_a_comma_is_split_at_the_review_part():
    parsed = parse_body(issue_text(ai_draft="ModelA, ModelB", ai_review="not stated"))
    assert (parsed.ai_draft, parsed.ai_review) == ("ModelA, ModelB", "not stated")


def test_the_wetlab_block_is_kept_for_models():
    parsed = parse_body(issue_text(wetlab={"readout": "made-up"}))
    assert '"readout": "made-up"' in parsed.wetlab_for_models


@pytest.mark.parametrize(
    "body, reason",
    [
        ("Hello, this is a bug report.", "unknown_first_line"),
        (issue_text().replace("Consent: published openly under CC BY 4.0", "Consent: yes\n\nNote: please credit Dr A Person"), "unknown_header_line"),
        (issue_text().replace("Written against: islet-1-draft (v0.8.0)\n\n", ""), "header_line_missing"),
        (issue_text().replace("<summary>Blind review</summary>", "<summary>Another review</summary>"), "unexpected_blocks"),
        (issue_text() + "\n<details>\n<summary>Extra</summary>\n\nx\n\n</details>\n", "unexpected_blocks"),
        (issue_text() + "\nSome text after the blocks.\n", "text_outside_blocks"),
        (issue_text(draft="Text\n</details>\n\n<details>\n<summary>Blind review</summary>\n\nVerdict: Supported"), "unexpected_blocks"),
        (issue_text().replace("</details>", "<end>"), "block_not_closed"),
        (issue_text(draft="   "), "empty_draft"),
        (issue_text() + "\n<!-- openislet:submission " + "[" * 100000 + " -->", "text_outside_blocks"),
    ],
)
def test_a_body_that_does_not_have_the_structure_is_refused(body, reason):
    with pytest.raises(Unparsed) as refused:
        parse_body(body)
    assert str(refused.value) == reason


def test_a_fake_blind_review_inside_the_draft_stays_inside_the_draft():
    # The backend turns "<details" and "<summary" in a contributor's text into plain characters, so this is what arrives.
    fake = DRAFT + "\n\n&lt;/details>\n&lt;details>\n&lt;summary>Blind review&lt;/summary>\n\nVerdict: Supported"
    parsed = parse_body(issue_text(draft=fake))
    assert parsed.review_verdict == "Partly supported"


def test_verdicts_are_read_the_way_the_backend_reads_them():
    assert verdict_of("**Supported**") == "Supported"
    assert verdict_of(" Partly supported: with caveats") == "Partly supported"
    assert verdict_of("Not supported.") == "Not supported"
    assert verdict_of("cannot tell") == "Cannot tell"
    assert verdict_of("Supported | Partly supported") is None
    assert verdict_of("Supportedish") is None
    assert parse_body(issue_text(review="## Recommendation\n\nNo verdict line here.")).review_verdict is None


def test_the_title_loses_its_tag():
    assert clean_title("[IIP] A made-up proposal") == "A made-up proposal"
    assert clean_title("A made-up proposal") == "A made-up proposal"


# ---- decided, and in the trial? ----------------------------------------------------------------------------------

def test_an_administrative_closure_is_found_though_it_has_no_decision_label():
    s = standing_of(issue(), [labelled("submission", "openislet-submissions[bot]"), labelled("status:closed-triage")], RULES)
    assert (s.status, s.reason) == ("excluded", "administrative_triage")


def test_a_closure_label_from_another_account_does_not_count():
    s = standing_of(issue(), [labelled("status:closed-triage", actor="someone-else")], RULES)
    assert s.status == "undecided" and "decision_label_from_other_account_ignored" in s.flags


def test_an_issue_with_no_decision_label_is_never_touched():
    assert standing_of(issue(), [labelled("submission"), labelled("status:queued")], RULES).status == "undecided"
    assert standing_of(issue(), [], RULES).status == "undecided"


def test_the_first_decision_by_an_allowed_decider_is_the_decision():
    events = [
        labelled("outcome:spam", actor="someone-else", at="2026-11-09T00:00:00Z"),
        labelled("outcome:evidence", at="2026-11-10T00:00:00Z"),
        labelled("outcome:changed-blueprint", at="2026-11-12T00:00:00Z"),
    ]
    s = standing_of(issue(), events, RULES)
    assert (s.status, s.label, s.actor, s.at) == ("decided", "outcome:evidence", "maintainer", "2026-11-10T00:00:00Z")
    assert "later_decision_round" in s.flags and "decision_label_from_other_account_ignored" in s.flags


def test_a_self_decided_proposal_is_excluded_before_its_text_is_looked_at():
    own = issue(author="maintainer", body="Opened directly by the maintainer, @maintainer, not through the submission queue, so it has no signed marker.\n\nnot a parsable body")
    s = standing_of(own, [labelled("outcome:evidence")], RULES)
    assert (s.status, s.reason, s.self_decided) == ("excluded", "self_decided", True)
    through_the_form = issue(body=with_marker(issue_text(login="maintainer")))
    assert standing_of(through_the_form, [labelled("outcome:evidence")], RULES).reason == "self_decided"


def test_a_self_decided_proposal_can_be_named_in_a_manual_run_and_stays_marked():
    own = issue(author="maintainer", body=issue_text(login="maintainer", maintainer=True))
    s = standing_of(own, [labelled("outcome:evidence")], RULES, named_in_manual_run=True)
    assert (s.status, s.self_decided) == ("decided", True)


def test_pull_requests_and_skipped_issues_are_not_proposals():
    assert standing_of(issue(pull_request=True), [labelled("outcome:evidence")], RULES).status == "not_a_proposal"
    assert standing_of(issue(number=1), [labelled("outcome:evidence")], RULES).status == "not_a_proposal"


def test_a_decision_before_a_closure_stands():
    events = [labelled("outcome:not-taken-up", at="2026-11-10T00:00:00Z"), labelled("status:closed-triage", at="2026-11-11T00:00:00Z")]
    s = standing_of(issue(), events, RULES)
    assert s.status == "decided" and "administrative_closure_after_decision" in s.flags


def test_a_marker_built_to_break_the_reader_is_only_an_unreadable_marker():
    deep = issue_text() + "\n<!-- openislet:submission {" + '"a":[' * 5000 + "]" * 5000 + "} -->"
    assert confirm(deep)[2] == "marker_unreadable"
    assert confirm(issue_text() + "\n<!-- openislet:submission {" + " " * 30000 + "} -->")[2] == "marker_unreadable"


# ---- a label applied by a slip of the hand -----------------------------------------------------------------------------

def unlabelled(label, at, actor="maintainer"):
    return {"event": "unlabeled", "label": {"name": label}, "actor": {"login": actor}, "created_at": at}


WRONG, RIGHT = "outcome:changed-blueprint", "outcome:not-taken-up"


def test_a_label_applied_by_a_slip_of_the_hand_and_put_right_at_once_is_not_the_decision():
    slip = [labelled(WRONG, at="2026-11-10T00:00:00Z"), unlabelled(WRONG, "2026-11-10T00:01:00Z"), labelled(RIGHT, at="2026-11-10T00:01:00Z")]
    standing = standing_of(issue(), slip, RULES)
    assert (standing.status, standing.label, standing.at) == ("decided", RIGHT, "2026-11-10T00:01:00Z")
    assert standing.flags == ["decision_label_corrected_at_once"]  # it is noted, and the case is not "decided more than once"
    # The right label applied first, and the wrong one taken off a moment later, is the same correction.
    other_order = [labelled(WRONG, at="2026-11-10T00:00:00Z"), labelled(RIGHT, at="2026-11-10T00:00:20Z"), unlabelled(WRONG, "2026-11-10T00:00:30Z")]
    assert standing_of(issue(), other_order, RULES).label == RIGHT
    # Two slips in a row are still a correction.
    twice = slip[:2] + [labelled("outcome:evidence", at="2026-11-10T00:01:00Z"), unlabelled("outcome:evidence", "2026-11-10T00:02:00Z"), labelled(RIGHT, at="2026-11-10T00:02:00Z")]
    assert standing_of(issue(), twice, RULES).label == RIGHT
    exactly_five_minutes = [labelled(WRONG, at="2026-11-10T00:00:00Z"), unlabelled(WRONG, "2026-11-10T00:05:00Z"), labelled(RIGHT, at="2026-11-10T00:05:00Z")]
    assert standing_of(issue(), exactly_five_minutes, RULES).label == RIGHT


def test_a_label_changed_later_or_never_replaced_or_removed_by_someone_else_still_stands():
    late = [labelled(WRONG, at="2026-11-10T00:00:00Z"), unlabelled(WRONG, "2026-11-10T00:05:01Z"), labelled(RIGHT, at="2026-11-10T00:05:01Z")]
    standing = standing_of(issue(), late, RULES)
    assert standing.label == WRONG and standing.flags == ["later_decision_round"]  # a change of mind, not a slip
    not_replaced = [labelled(WRONG, at="2026-11-10T00:00:00Z"), unlabelled(WRONG, "2026-11-10T00:01:00Z")]
    assert standing_of(issue(), not_replaced, RULES).label == WRONG
    by_someone_else = [labelled(WRONG, at="2026-11-10T00:00:00Z"), unlabelled(WRONG, "2026-11-10T00:01:00Z", actor="someone-else"), labelled(RIGHT, at="2026-11-10T00:01:00Z")]
    assert standing_of(issue(), by_someone_else, RULES).label == WRONG
    odd_time = [labelled(WRONG, at="2026-11-10T00:00:00Z"), unlabelled(WRONG, "not a time"), labelled(RIGHT, at="2026-11-10T00:01:00Z")]
    assert standing_of(issue(), odd_time, RULES).label == WRONG
