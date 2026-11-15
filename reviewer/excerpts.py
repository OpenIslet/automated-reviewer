"""Passages from repository files that a proposal's Draft cites, taken from the context commit. No model is involved.

- A file is recognised because its path, or its file name when only one file has that name, appears in the Draft and
  is in the file list at that commit. Nothing is guessed from what a path looks like.
- With a line reference the passage is the cited lines and a margin either side; with none it is the top of the file.
- Files already sent in full are skipped. The total has a cap; a passage that does not fit is listed by name only,
  never cut.
- A path that is cited and not there at that commit is reported as missing. Its name is the contributor's own
  writing, so that list is kept apart (`missing_text`) and is sent only inside a part marked as untrusted.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import tokens
from .github import GitHubError

MAX_FILE_BYTES = 2_000_000  # a cited file bigger than this is listed by name only, without being fetched
MAX_CITED_SPAN = 2000  # a "lines 1 to 90000" reference is treated as no line reference
MAX_MISSING = 20  # how many paths that are not there are listed
MAX_MISSING_LENGTH = 120  # a longer one is not listed at all

_AFTER = re.compile(
    r"""^[`'"*_)\]]{0,3}
    (?: :(\d+)(?:\s?[-–]\s?(\d+))?
      | \#L(\d+)(?:-L?(\d+))?
      | [,;(\s]{1,3}(?:lines?|L)\.?\s?(\d+)(?:\s?(?:[-–]|to|and)\s?L?(\d+))?
    )""",
    re.VERBOSE | re.IGNORECASE,
)
_PATH_CHARS = "A-Za-z0-9_.-"
_CANDIDATE = re.compile(rf"(?<![{_PATH_CHARS}/])([{_PATH_CHARS}]+(?:/[{_PATH_CHARS}]+)+)")


@dataclass(frozen=True)
class Citation:
    path: str  # as it is in the repository, or as written when it is not there
    first: Optional[int]  # the cited lines, counted from 1; None when no line is given
    last: Optional[int]
    found: bool


@dataclass
class Excerpts:
    text: str = ""
    included: List[Tuple[str, int, int]] = field(default_factory=list)  # (path, first line sent, last line sent)
    already_in_full: List[str] = field(default_factory=list)
    by_name_only: List[Tuple[str, str]] = field(default_factory=list)  # (path, why)
    missing: List[str] = field(default_factory=list)
    missing_text: str = ""  # untrusted: these names were written by the contributor
    tokens: int = 0


def _line_reference(following: str) -> Tuple[Optional[int], Optional[int]]:
    hit = _AFTER.match(following)
    if not hit:
        return None, None
    numbers = [int(g) for g in hit.groups() if g is not None]
    first, last = numbers[0], numbers[-1]
    if last < first:
        first, last = last, first
    if first < 1 or last - first > MAX_CITED_SPAN:
        return None, None
    return first, last


def find_citations(draft: str, paths) -> List[Citation]:
    """Every repository file the Draft cites, in the order first cited."""
    found: List[Tuple[int, int, Citation]] = []
    taken: List[Tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= a or start >= b for a, b in taken)

    def ends_here(end: int) -> bool:
        """True when the name stops at `end`: "x.md." and "x.md)" do, "x.md.bak" and "x.mdx" do not."""
        after = draft[end : end + 1]
        if re.match(r"[A-Za-z0-9_/]", after):
            return False
        return not (after in (".", "-") and re.match(r"[A-Za-z0-9_]", draft[end + 1 : end + 2]))

    def collect(needle: str, path: str, may_follow_a_slash: bool) -> None:
        at = draft.find(needle)
        while at >= 0:
            end = at + len(needle)
            before = draft[at - 1] if at > 0 else " "
            starts_here = not re.match(rf"[{_PATH_CHARS}]", before) and (may_follow_a_slash or before != "/")
            if starts_here and ends_here(end) and free(at, end):
                first, last = _line_reference(draft[end : end + 40])
                found.append((at, end, Citation(path, first, last, True)))
                taken.append((at, end))
            at = draft.find(needle, end)

    # Whole paths first, longest first. One may follow a "/", as it does in a link to the file on GitHub.
    for path in sorted(paths, key=len, reverse=True):
        if "/" in path:
            collect(path, path, True)
    # Then a bare file name, when exactly one file at this commit has that name.
    names: Dict[str, List[str]] = {}
    for path in paths:
        names.setdefault(path.rsplit("/", 1)[-1], []).append(path)
    for name, owners in sorted(names.items(), key=lambda item: len(item[0]), reverse=True):
        if len(owners) == 1 and "." in name and len(name) >= 6:
            collect(name, owners[0], False)

    # Last, something written as a path under one of the repository's own folders that is not there at this commit.
    top = {path.split("/", 1)[0] for path in paths if "/" in path}
    for hit in _CANDIDATE.finditer(draft):
        written = hit.group(1).rstrip(".-")
        start, end = hit.start(1), hit.start(1) + len(written)
        is_a_file_path = written.split("/", 1)[0] in top and "." in written.rsplit("/", 1)[-1]
        if is_a_file_path and written not in paths and free(start, end):
            found.append((start, end, Citation(written, None, None, False)))
            taken.append((start, end))

    found.sort(key=lambda item: item[0])
    return [citation for _, _, citation in found]


def _windows(citations: List[Citation], total: int, either_side: int, top_lines: int) -> List[Tuple[int, int]]:
    """The line ranges to send for one file, merged where they touch. Lines count from 1."""
    wanted = []
    for c in citations:
        if c.first is None:
            wanted.append((1, min(total, top_lines)))
        elif c.first <= total:
            wanted.append((max(1, c.first - either_side), min(total, c.last + either_side)))
    merged: List[Tuple[int, int]] = []
    for start, end in sorted(wanted):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def build(draft: str, context, reader, either_side: int, top_lines: int, max_tokens: int) -> Excerpts:
    """The cited passages for one proposal. `context` is its Context; `reader` is the run's ContextReader."""
    result = Excerpts()
    by_path: Dict[str, List[Citation]] = {}
    for citation in find_citations(draft, context.paths):
        if not citation.found:
            if citation.path not in result.missing and len(citation.path) <= MAX_MISSING_LENGTH and len(result.missing) < MAX_MISSING:
                result.missing.append(citation.path)
        elif citation.path in context.full_paths():
            if citation.path not in result.already_in_full:
                result.already_in_full.append(citation.path)
        else:
            by_path.setdefault(citation.path, []).append(citation)

    blocks: List[str] = []
    for path, citations in by_path.items():  # in the order first cited
        if context.paths[path]["size"] > MAX_FILE_BYTES:
            result.by_name_only.append((path, "file_too_large"))
            continue
        try:
            lines = reader.text(context.commit, path).split("\n")
        except UnicodeDecodeError:
            result.by_name_only.append((path, "not_text"))
            continue
        except GitHubError:
            result.by_name_only.append((path, "unreadable"))
            continue
        if lines and lines[-1] == "":
            lines.pop()
        windows = _windows(citations, len(lines), either_side, top_lines)
        if not windows:
            result.by_name_only.append((path, "cited_lines_past_the_end"))
            continue
        parts = []
        for start, end in windows:
            numbered = "\n".join(f"{n}\t{lines[n - 1]}" for n in range(start, end + 1))
            parts.append(f"File: {path}, lines {start} to {end} of {len(lines)}\n{numbered}")
        block = "\n\n".join(parts)
        size = tokens.estimate(block)
        if result.tokens + size > max_tokens:
            result.by_name_only.append((path, "over_the_cap"))
            continue
        result.tokens += size
        result.included.extend((path, start, end) for start, end in windows)
        blocks.append(block)

    tail = []
    if result.already_in_full:
        tail.append("Cited, and included in full above: " + ", ".join(result.already_in_full))
    if result.by_name_only:
        tail.append("Cited, but not included here (too long, or not text): " + ", ".join(path for path, _ in result.by_name_only))
    result.text = "\n\n".join(blocks + (["\n".join(tail)] if tail else []))
    if result.missing:
        result.missing_text = "Files the proposal names that were not in the project's repository at that commit: " + ", ".join(result.missing)
    return result
