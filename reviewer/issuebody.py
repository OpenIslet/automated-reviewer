"""Reads a proposal's body by its fixed structure, and separates what a model may see from what it may not.

A body is in one of two shapes: opened by the submission bot, or opened directly by the maintainer. Anything else is
refused. This module never guesses: an unknown line in the header, a missing block or an extra one all raise Unparsed.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .integrity import plain_newlines

_LOGIN = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_BOT_FIRST = re.compile(rf"^Submitted by @({_LOGIN}) \(verified through GitHub\)$")
_MAINTAINER_FIRST = re.compile(rf"^Opened directly by the maintainer, @({_LOGIN}), not through the submission queue, so it has no signed marker\.$")
_ORCID = re.compile(r"^ORCID iD \[[0-9X-]+\]\(https://orcid\.org/[0-9X-]+\) \(verified through ORCID\)$")
_STATED = re.compile(r"^Stated by the contributor, not verified: .+$")
_AI_TOOLS = re.compile(r"^AI tools \(self-reported\): draft = (.*)$")
_WRITTEN = re.compile(r"^Written against: (.+)$")
_CONSENT = re.compile(r"^Consent: .+$")
_SUMMARY = re.compile(r"^<summary>(.+)</summary>$")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_AI_TOOLS_WORDS = re.compile(r"\bAI[ \t*_`-]{0,4}tools?\b", re.IGNORECASE)
# The shapes of a line that heads or labels a section, however it is indented or quoted. A sentence in a paragraph, a
# list item or an ordinary table cell that mentions AI tools is none of these.
_HEADS_OR_LABELS = (
    re.compile(r"^[ \t>]*#{1,6}(?:[ \t]|$)"),  # a Markdown heading
    re.compile(r"^[ \t>]*<(?:h[1-6]|summary|strong|b)\b", re.IGNORECASE),  # an HTML heading
    re.compile(r"^[ \t>]*(?:\*\*|__)[^*_]+(?:\*\*|__)[ \t]*:?[ \t]*$"),  # a line that is bold text and nothing else
    re.compile(r"^[ \t>]*(?:[-*+][ \t]+)?[*_`]{0,3}[A-Za-z][A-Za-z0-9 /()*_`-]{0,45}:(?:[ \t*_`]|$)"),  # a short label, then a colon
    re.compile(r"^[ \t>]*\|[ \t*_`]*AI[ \t*_`-]{0,4}tools?(?:[ \t]+used)?[ \t*_`:]*\|", re.IGNORECASE),  # a table row labelled with it
)
_UNDERLINE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")  # under a line of text, this makes that line a heading
# A paragraph that opens with a label naming AI tools, such as "**AI tools (self-reported):** draft = ...".
_AI_TOOLS_LABEL = re.compile(r"^[ \t>]*(?:[-*+][ \t]+)?[*_`]{0,3}AI[ \t*_`-]{0,4}tools?\b[^:\n]{0,40}:", re.IGNORECASE)
_VERDICTS = ("not supported", "partly supported", "cannot tell", "supported")


class Unparsed(Exception):
    """The body does not have the expected structure. The reason is a short fixed word, never contributor text."""


@dataclass
class ParsedBody:
    shape: str  # "bot" or "maintainer"
    submitter: str
    written_against: str
    draft_for_models: str
    wetlab_for_models: Optional[str]
    # Kept in the record, never sent to a model:
    ai_draft: Optional[str]
    ai_review: Optional[str]
    draft_ai_tools_section: Optional[str]
    review_verdict: Optional[str]
    claim: Optional[str]
    notes: List[str] = field(default_factory=list)
    kind: Optional[str] = None  # the Draft's "Type" field, when it is a few plain words


def first_line_login(body: str) -> Tuple[Optional[str], Optional[str]]:
    """(shape, login) from the first line alone, without parsing the rest. (None, None) if it is neither shape."""
    first = plain_newlines(body or "").split("\n", 1)[0].rstrip()
    for shape, pattern in (("bot", _BOT_FIRST), ("maintainer", _MAINTAINER_FIRST)):
        found = pattern.match(first)
        if found:
            return shape, found.group(1)
    return None, None


def clean_title(title: str) -> str:
    """The title as a model sees it: the leading [IIP] tag removed."""
    return re.sub(r"^\s*\[IIP\]\s*", "", title or "").strip()


def verdict_of(value: Optional[str]) -> Optional[str]:
    """The blind reviewer's verdict from its Verdict line, read the way the submission backend reads it."""
    if not value or "|" in value:
        return None
    v = re.sub(r"\s+", " ", value).strip().lower()
    v = re.sub(r"[*_`()]", "", v)
    v = re.sub(r"^[\s:]+", "", v).strip()
    for label in _VERDICTS:
        if v == label or re.match(rf"^{label}[\s.,:;!—–-]", v):
            return label.capitalize()
    return None


def _field(text: str, name: str) -> Optional[str]:
    """The value of a template field such as "Verdict: Supported", on its line or the next one."""
    lines = text.split("\n")
    pattern = re.compile(rf"^[\s>*_#-]*{re.escape(name)}[\s*_]*:[\s*_]*(.*)$", re.IGNORECASE)
    for i, line in enumerate(lines):
        found = pattern.match(line)
        if not found:
            continue
        value = found.group(1).strip()
        if not value:
            value = next((later.strip() for later in lines[i + 1 :] if later.strip()), "")
        return value or None
    return None


def strip_ai_tools_section(draft: str) -> Tuple[str, Optional[str]]:
    """Removes what the Draft says about AI tools. Returns (the draft without it, the removed text).

    Two things are removed: the "AI tools used" section, and any paragraph that opens with a label naming AI tools.
    """
    kept, section = _without_the_section(draft)
    kept, labelled = _without_labelled_paragraphs(kept)
    removed = ([section] if section is not None else []) + labelled
    return kept, ("\n\n".join(removed) if removed else None)


def _without_labelled_paragraphs(text: str) -> Tuple[str, List[str]]:
    """Removes each paragraph that opens with a label naming AI tools and says something under it.

    The paragraph runs to the next blank line or heading. A label with nothing after it, on its line or under it, is
    left where it is: how far its subject runs cannot be known, so `still_names_ai_tools` refuses the Draft.
    """
    lines = text.split("\n")
    kept: List[str] = []
    taken: List[str] = []
    i = 0
    while i < len(lines):
        label = _AI_TOOLS_LABEL.match(lines[i])
        if not label:
            kept.append(lines[i])
            i += 1
            continue
        end = i + 1
        while end < len(lines) and lines[end].strip() and not _HEADING.match(lines[end]):
            end += 1
        says_something = bool(lines[i][label.end() :].strip(" \t*_`")) or end > i + 1
        if says_something:
            taken.append("\n".join(lines[i:end]).strip())
        else:
            kept.extend(lines[i:end])
        i = end
    return "\n".join(kept).strip(), taken


def _without_the_section(draft: str) -> Tuple[str, Optional[str]]:
    """Removes the "AI tools used" section. Returns (the draft without it, the removed text).

    The section runs from its heading to the next heading of the same or a higher level, or to the end. Lower-level
    headings do not end it, and they do not end other sections either.
    """
    lines = draft.split("\n")
    start = level = None
    fence = None
    for i, line in enumerate(lines):
        # Code fences, by the CommonMark rules GitHub follows. A line that opens with backticks and has more of them
        # later is a code span, not a fence; and a fence is closed only by a line of its own marks and nothing else.
        mark = _FENCE.match(line)
        if fence is None:
            if mark and not (mark.group(1)[0] == "`" and "`" in mark.group(2)):
                fence = mark.group(1)
                continue
        else:
            if mark and mark.group(1)[0] == fence[0] and len(mark.group(1)) >= len(fence) and not mark.group(2).strip():
                fence = None
            continue
        heading = _HEADING.match(line)
        if heading:
            name = re.sub(r"[*_`:]", "", heading.group(2).rstrip(" \t#")).strip().lower()
            if start is None and name == "ai tools used":
                start, level = i, len(heading.group(1))
            elif start is not None and len(heading.group(1)) <= level:
                kept = lines[:start] + lines[i:]
                return "\n".join(kept).strip(), "\n".join(lines[start:i]).strip()
        elif start is None and re.fullmatch(r"\*\*AI tools used:?\*\*:?", line.strip(), re.IGNORECASE):
            start, level = i, 6
    if start is None:
        return draft.strip(), None
    return "\n".join(lines[:start]).strip(), "\n".join(lines[start:]).strip()


def still_names_ai_tools(draft_for_models: str) -> bool:
    """True when a heading or a label in what is left still names AI tools.

    The section can be missed: a heading worded differently, indented, written as HTML, placed inside an unclosed
    code fence, or given twice. So can a label: one with nothing under it, or one where AI tools are named after the
    colon. A model must not learn which AI drafted a proposal, so such a Draft is refused. A sentence that only
    mentions AI tools is not a reason to refuse one.
    """
    lines = draft_for_models.split("\n")
    for i, line in enumerate(lines):
        if not _AI_TOOLS_WORDS.search(line):
            continue
        if any(shape.match(line) for shape in _HEADS_OR_LABELS):
            return True
        if i + 1 < len(lines) and _UNDERLINE.match(lines[i + 1]):
            return True
    return False


def _blocks(lines: List[str]) -> List[Tuple[str, str]]:
    """The <details> blocks, in order, as (summary, content). Anything between or after them must be blank."""
    blocks: List[Tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line not in ("<details open>", "<details>"):
            raise Unparsed("text_outside_blocks")
        summary = _SUMMARY.match(lines[i + 1]) if i + 1 < len(lines) else None
        if not summary:
            raise Unparsed("block_without_summary")
        try:
            end = lines.index("</details>", i + 2)
        except ValueError:
            raise Unparsed("block_not_closed")
        blocks.append((summary.group(1), "\n".join(lines[i + 2 : end]).strip("\n")))
        i = end + 1
    return blocks


def parse_body(text_above_marker: str) -> ParsedBody:
    lines = plain_newlines(text_above_marker or "").split("\n")
    shape, submitter = first_line_login(lines[0] if lines else "")
    if shape is None:
        raise Unparsed("unknown_first_line")

    try:
        block_start = next(i for i, line in enumerate(lines) if line in ("<details open>", "<details>"))
    except StopIteration:
        raise Unparsed("no_blocks")

    ai_draft = ai_review = written = None
    seen = set()
    for line in lines[1:block_start]:
        if not line.strip():
            continue
        kind = None
        if _ORCID.match(line):
            kind = "orcid"
        elif _STATED.match(line):
            kind = "stated"
        elif _AI_TOOLS.match(line):
            kind = "ai_tools"
            rest = _AI_TOOLS.match(line).group(1)
            # The draft's tool list may itself contain a comma: split on the last ", review = ".
            cut = rest.rfind(", review = ")
            if cut < 0:
                raise Unparsed("ai_tools_line_shape")
            ai_draft, ai_review = rest[:cut].strip(), rest[cut + len(", review = ") :].strip()
        elif _WRITTEN.match(line):
            kind = "written_against"
            written = _WRITTEN.match(line).group(1).strip()
        elif _CONSENT.match(line):
            kind = "consent"
        if kind is None:
            raise Unparsed("unknown_header_line")
        if kind in seen:
            raise Unparsed("repeated_header_line")
        seen.add(kind)
    if not {"ai_tools", "written_against", "consent"} <= seen:
        raise Unparsed("header_line_missing")

    blocks = _blocks(lines[block_start:])
    names = [name for name, _ in blocks]
    if len(blocks) not in (2, 3) or names[0] != "Draft" or names[1] != "Blind review":
        raise Unparsed("unexpected_blocks")
    wetlab = None
    if len(blocks) == 3:
        if not names[2].startswith("Wet-lab readouts"):
            raise Unparsed("unexpected_blocks")
        wetlab = blocks[2][1].strip()

    draft, review = blocks[0][1], blocks[1][1]
    if not draft.strip():
        raise Unparsed("empty_draft")
    draft_for_models, ai_section = strip_ai_tools_section(draft)
    if still_names_ai_tools(draft_for_models):
        raise Unparsed("ai_tools_section_not_removed")
    notes = []
    if ai_section is None:
        notes.append("draft_has_no_ai_tools_section")
    claim = _field(draft, "Claim in one sentence")
    if claim:
        claim = re.sub(r"\s+", " ", re.sub(r"[*_`]", "", claim)).strip()[:300]
    kind = re.sub(r"\s+", " ", re.sub(r"[*_`]", "", _field(draft, "Type") or "")).strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z -]{0,39}", kind):
        kind = None
    verdict = verdict_of(_field(review, "Verdict"))
    if verdict is None:
        notes.append("review_verdict_unreadable")

    return ParsedBody(
        shape=shape,
        submitter=submitter,
        written_against=written or "",
        draft_for_models=draft_for_models,
        wetlab_for_models=wetlab,
        ai_draft=ai_draft,
        ai_review=ai_review,
        draft_ai_tools_section=ai_section,
        review_verdict=verdict,
        claim=claim or None,
        notes=notes,
        kind=kind,
    )
