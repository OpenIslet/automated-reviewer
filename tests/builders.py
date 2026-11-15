"""Made-up proposals in the two real body shapes, built the way the submission backend builds them.

Nothing here is a real proposal. The words are placeholders about a made-up "Signal X".
"""
import json

from reviewer.integrity import body_sha256

DRAFT = """Type: Improvement
Claim in one sentence: Signal X should be checked in a second assay before it is relied on.
In plain English: One test is not enough.
Affects test: 3
Affects design: Design A

## Evidence

A made-up study reports the effect in one assay only.

## What I propose changing

### 1. Add the second assay

Add it beside the first.

### 2. Record both

Keep both results.

## How this could be wrong

The second assay may not apply.

## Cheapest way to check it

Run it once.

## AI tools used

ExampleModel 9 wrote the first version.

### Notes

It was checked by hand."""

REVIEW = """Claim reviewed: Signal X should be checked in a second assay.
Verdict: Partly supported
Confidence: Medium
Submit to OpenIslet?: Yes
In plain English: Mostly right.

## What I checked

The cited study.

## AI tools used

OtherModel 3 wrote this review."""


def issue_text(
    login="someone",
    draft=DRAFT,
    review=REVIEW,
    ai_draft="ExampleModel 9",
    ai_review="OtherModel 3",
    written_against="islet-1-draft (v0.8.0)",
    orcid=None,
    stated=None,
    wetlab=None,
    maintainer=False,
):
    """Everything in the issue above the hidden marker, as the backend writes it."""
    first = (
        f"Opened directly by the maintainer, @{login}, not through the submission queue, so it has no signed marker."
        if maintainer
        else f"Submitted by @{login} (verified through GitHub)"
    )
    lines = [first, ""]
    if orcid:
        lines += [f"ORCID iD [{orcid}](https://orcid.org/{orcid}) (verified through ORCID)", ""]
    if stated:
        lines += [f"Stated by the contributor, not verified: {stated}", ""]
    lines += [
        f"AI tools (self-reported): draft = {ai_draft}, review = {ai_review}",
        "",
        f"Written against: {written_against}",
        "",
        "Consent: published openly under CC BY 4.0",
        "",
        "<details open>",
        "<summary>Draft</summary>",
        "",
        draft,
        "",
        "</details>",
        "",
        "<details>",
        "<summary>Blind review</summary>",
        "",
        review,
        "",
        "</details>",
        "",
    ]
    if wetlab is not None:
        lines += [
            "<details>",
            "<summary>Wet-lab readouts (self-reported; they passed the endpoint checks)</summary>",
            "",
            "```json",
            json.dumps(wetlab, indent=2),
            "```",
            "",
            "</details>",
            "",
        ]
    return "\n".join(lines)


def with_marker(text, claim="Signal X should be checked in a second assay.", pmids=("12345678",)):
    """The whole body: the text, then the marker on its own last line, as the backend writes it."""
    fields = {"v": 1, "idempotency_key": None, "claim": claim, "pmids": list(pmids), "via": "web", "body_sha256": body_sha256(text), "sig": "0" * 64}
    return f"{text}\n<!-- openislet:submission {json.dumps(fields, separators=(',', ':'))} -->"


def labelled(label, actor="maintainer", at="2026-11-10T00:00:00Z"):
    return {"event": "labeled", "label": {"name": label}, "actor": {"login": actor}, "created_at": at}


def issue(number=20, author="openislet-submissions[bot]", body=None, pull_request=False, created_at="2026-11-09T12:00:00Z", title="[IIP] A made-up proposal"):
    data = {
        "number": number,
        "user": {"login": author, "type": "Bot" if author.endswith("[bot]") else "User"},
        "body": with_marker(issue_text()) if body is None else body,
        "title": title,
        "created_at": created_at,
    }
    if pull_request:
        data["pull_request"] = {}
    return data


BASIC_ROWS = [
    ("✅", "A draft is attached", "Yes."),
    ("✅", "A blind review is attached", "Yes."),
    ("✅", "The review supports the IIP", "The reviewer's verdict is Partly supported."),
]
OTHER_ROWS = [
    ("✅", "Says which design it changes", "It says it affects Design A."),
    ("✅", "AI tools named", "Both the draft and the review say which AI tools were used (shown as reported by you, never as verified)."),
    ("✅", "Papers are cited by PubMed ID", "1 paper cited in the evidence (0 already in the OpenIslet studies)."),
    ("🟡", "Quotes match their papers", "1 quoted sentence: 0 matched the OpenIslet record, 0 matched a different paper than the one cited, and 1 cannot be checked here."),
    ("✅", "The review is about the same claim", "The reviewer restated the same claim."),
    ("🟡", "Idea Graveyard", "This may touch GY-001 (A made-up rule). These are word-matches, not a verdict."),
    ("✅", "Not already known", "Nothing similar found in the open questions or earlier contributions."),
    ("✅", "Written against the current version", "Checked against islet-1-draft."),
    ("🟡", "Links and personal details", "The text seems to contain an email address. Everything submitted is public, so remove personal details."),
]


def checks_comment(other_rows=None, basic_rows=None, author="openislet-submissions[bot]", author_type="Bot", at="2026-11-09T12:00:01Z", updated_at=None, extra_lines=()):
    """The submission bot's first comment, in the shape the backend writes it."""

    def table(second, third, rows):
        return [f"|  | {second} | {third} |", "| --- | --- | --- |"] + [f"| {status} | {name} | {detail} |" for status, name, detail in rows]

    lines = ["## Automatic checks", "", "Software ran these checks the moment the Islet Improvement Proposal (IIP) was sent.", "", "### The basic rules", ""]
    lines += table("Rule", "Result", BASIC_ROWS if basic_rows is None else basic_rows)
    lines += ["", "### The other checks", ""]
    lines += table("Check", "What it found", OTHER_ROWS if other_rows is None else other_rows)
    lines += list(extra_lines) + [""]
    return {"id": 501, "body": "\n".join(lines), "user": {"login": author, "type": author_type}, "created_at": at, "updated_at": updated_at or at}


def person_comment(text="A comment from a person.", author="maintainer", at="2026-11-10T09:00:00Z"):
    return {"id": 601, "body": text, "user": {"login": author, "type": "User"}, "created_at": at, "updated_at": at}
