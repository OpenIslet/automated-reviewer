"""The submission bot's "Automatic checks" comment: what little of it a model may see.

The comment has two tables. "The basic rules" is left out whole: one of its rows states the blind reviewer's verdict.
Of "The other checks", only the rows named in the settings are kept. Rows about the blind review, the AI tools or
personal details are never on that list, and a row this script does not know is left out.

The comment is used only when it is certainly the bot's own first comment, unedited and in its fixed shape. In any
doubt it is left out whole.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from .integrity import plain_newlines

STATUS_WORDS = {"✅": "passed", "🟡": "advice", "❌": "not passed"}
INTRO = "Checks that software ran when the proposal was submitted. They are advice from a script, not a judgement by a person."


@dataclass
class OtherChecks:
    text: Optional[str]  # None when nothing from the comment is used
    why: str  # "included", or the reason nothing is used
    rows_kept: int = 0
    rows_left_out: int = 0


def _when(stamp) -> Optional[datetime]:
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def _cells(line: str) -> List[str]:
    inner = line.strip()
    if not (inner.startswith("|") and inner.endswith("|") and len(inner) >= 2):
        return []
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", inner[1:-1])]


def other_checks(issue: dict, comments: List[dict], settings, submission_bot: str) -> OtherChecks:
    if not comments:
        return OtherChecks(None, "no_comment")
    first = sorted(comments, key=lambda c: c.get("created_at") or "")[0]
    author = first.get("user") or {}
    if author.get("login") != submission_bot or author.get("type") != "Bot":
        return OtherChecks(None, "first_comment_not_by_the_bot")
    opened, posted = _when(issue.get("created_at")), _when(first.get("created_at"))
    if opened is None or posted is None or not opened <= posted <= opened + timedelta(minutes=settings.within_minutes):
        return OtherChecks(None, "not_posted_at_submission")
    if first.get("updated_at") not in (None, first.get("created_at")):
        return OtherChecks(None, "comment_was_edited")

    lines = plain_newlines(first.get("body") or "").split("\n")
    headings = [line for line in lines if line.startswith("#")]
    if not lines or lines[0] != settings.starts_with or headings != [settings.starts_with, settings.left_out_heading, settings.included_heading]:
        return OtherChecks(None, "not_in_the_fixed_shape")
    part = [line for line in lines[lines.index(settings.included_heading) + 1 :] if line.strip()]
    rows = [_cells(line) for line in part]
    if len(rows) < 2 or any(len(row) != 3 for row in rows) or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]):
        return OtherChecks(None, "not_in_the_fixed_shape")

    kept: List[str] = []
    left_out = 0
    for status, name, detail in rows[2:]:
        if name in settings.included_rows and status in STATUS_WORDS and detail:
            kept.append(f"- {STATUS_WORDS[status]}: {name}. {detail}")
        else:
            left_out += 1
    if not kept:
        return OtherChecks(None, "no_row_to_include", 0, left_out)
    return OtherChecks("\n".join([INTRO] + kept), "included", len(kept), left_out)
