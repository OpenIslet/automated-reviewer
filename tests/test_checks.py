"""The bot's checks comment: only the allowed rows of "The other checks", and only when it is certainly the bot's own."""
import copy

import pytest
import yaml

from reviewer.checks import other_checks
from reviewer.config import ConfigError, load

from .builders import OTHER_ROWS, checks_comment, issue, person_comment
from .test_config import SHIPPED, folder_with

SETTINGS = load(SHIPPED)
BOT = SETTINGS.submission_bot


def read(comments, created_at="2026-11-09T12:00:00Z"):
    return other_checks(issue(created_at=created_at), comments, SETTINGS.checks_comment, BOT)


def test_only_the_allowed_rows_of_the_other_checks_are_kept():
    result = read([checks_comment(), person_comment()])
    assert (result.why, result.rows_kept, result.rows_left_out) == ("included", 6, 3)
    lines = result.text.split("\n")
    assert lines[0].startswith("Checks that software ran when the proposal was submitted.")
    assert lines[1:] == [
        "- passed: Says which design it changes. It says it affects Design A.",
        "- passed: Papers are cited by PubMed ID. 1 paper cited in the evidence (0 already in the OpenIslet studies).",
        "- advice: Quotes match their papers. 1 quoted sentence: 0 matched the OpenIslet record, 0 matched a different paper than the one cited, and 1 cannot be checked here.",
        "- advice: Idea Graveyard. This may touch GY-001 (A made-up rule). These are word-matches, not a verdict.",
        "- passed: Not already known. Nothing similar found in the open questions or earlier contributions.",
        "- passed: Written against the current version. Checked against islet-1-draft.",
    ]


def test_nothing_about_the_blind_review_the_ai_tools_or_personal_details_gets_through():
    rows = OTHER_ROWS + [("❌", "The review recommends submitting", 'The review answers "Submit to OpenIslet?" with No.'), ("✅", "A check added next year", "Something this script has never seen.")]
    result = read([checks_comment(other_rows=rows)])
    assert result.rows_left_out == 5
    # ("not a verdict" in the Idea Graveyard row is the bot's own wording about word-matches, and stays.)
    for hidden in ("reviewer's verdict", "Partly supported", "reviewer", "the review", "Submit to OpenIslet", "AI tools", "email", "personal", "never seen", "basic rules", "A draft is attached"):
        assert hidden.lower() not in result.text.lower(), hidden


def test_a_human_comment_first_means_the_bot_comment_is_left_out():
    result = read([person_comment(at="2026-11-09T12:00:00Z"), checks_comment(at="2026-11-09T12:00:01Z")])
    assert (result.text, result.why) == (None, "first_comment_not_by_the_bot")


@pytest.mark.parametrize(
    "comment, why",
    [
        (checks_comment(author="someone-else"), "first_comment_not_by_the_bot"),
        (checks_comment(author_type="User"), "first_comment_not_by_the_bot"),  # the right name on an ordinary account
        (checks_comment(at="2026-11-09T12:10:01Z"), "not_posted_at_submission"),
        (checks_comment(at="2026-11-09T11:59:59Z"), "not_posted_at_submission"),
        (checks_comment(updated_at="2026-11-12T08:00:00Z"), "comment_was_edited"),
        (checks_comment(extra_lines=["", "### One more part", "", "Anything."]), "not_in_the_fixed_shape"),
        (checks_comment(extra_lines=["", "A line that is not a table row."]), "not_in_the_fixed_shape"),
        (checks_comment(other_rows=[("✅", "Idea Graveyard", "one | two")]), "not_in_the_fixed_shape"),  # a fourth cell
        (checks_comment(other_rows=[("✅", "AI tools named", "Both.")]), "no_row_to_include"),
    ],
)
def test_in_any_doubt_the_comment_is_left_out_whole(comment, why):
    result = read([comment])
    assert (result.text, result.why) == (None, why)


def test_a_comment_that_is_not_in_the_fixed_shape_is_left_out():
    for change in (
        lambda body: body.replace("## Automatic checks", "## Automatic checks (edited)"),
        lambda body: body.replace("### The basic rules", "### Rules"),
        lambda body: "Preface\n" + body,
        lambda body: body.replace("| --- | --- | --- |", "", 2),
        lambda body: body.split("### The other checks")[0],
    ):
        comment = checks_comment()
        comment["body"] = change(comment["body"])
        assert read([comment]).why == "not_in_the_fixed_shape"
    assert read([]).why == "no_comment"


def test_a_pipe_inside_a_cell_is_read_as_the_backend_wrote_it():
    result = read([checks_comment(other_rows=[("✅", "Idea Graveyard", r"No rule looks related \| by its words.")])])
    assert result.text.endswith("- passed: Idea Graveyard. No rule looks related | by its words.")


def test_a_row_with_an_unknown_mark_or_no_detail_is_left_out():
    result = read([checks_comment(other_rows=[("⭐", "Idea Graveyard", "Starred."), ("✅", "Not already known", ""), ("✅", "Idea Graveyard", "Kept.")])])
    assert (result.rows_kept, result.rows_left_out) == (1, 2) and result.text.endswith("- passed: Idea Graveyard. Kept.")


def test_the_settings_cannot_add_a_row_about_the_review_the_ai_tools_or_personal_details(tmp_path):
    for row in ("The review is about the same claim", "AI tools named", "Links and personal details", "The review recommends submitting"):
        def add(config, row=row):
            config["checks_comment"]["included_rows"].append(row)

        with pytest.raises(ConfigError) as error:
            load(folder_with(tmp_path, change=add))
        assert "can never be included" in str(error.value)
