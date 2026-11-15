"""Is this the text that was submitted?

The submission backend ends every issue it opens with a hidden marker that holds a SHA-256 hash of everything above it.
This module recomputes that hash the same way the backend does. It never checks the marker's signature: that needs the
backend's signing key, which this project must not hold.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Optional, Tuple

MARKER_NAME = "openislet:submission"
_MARKER_LINE = re.compile(r"<!--\s*openislet:submission\s+(\{.*\})\s*-->")
_MARKER_ANYWHERE = re.compile(r"<!--\s*openislet:submission", re.IGNORECASE)
MAX_MARKER = 20000  # characters. The backend's markers are a few hundred.

# What JavaScript's trimEnd() removes (its white space and line terminators), so the hash is worked out on the same text.
JS_SPACE = (
    "\t\n\x0b\x0c\r   "
    "           "
    "    　﻿"
)


def plain_newlines(text: str) -> str:
    return re.sub(r"\r\n?", "\n", text)


def canonical_body(text: str) -> str:
    """The text as the backend fingerprints it: line endings made plain and the end trimmed."""
    return plain_newlines(text).rstrip(JS_SPACE)


def body_sha256(text: str) -> str:
    return hashlib.sha256(canonical_body(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Marker:
    body_sha256: str
    claim: Optional[str]
    pmids: Tuple[str, ...]
    via: Optional[str]


def split_marker(body: str) -> Tuple[str, Optional[Marker], Optional[str]]:
    """Returns (the text above the marker, the marker, a problem).

    With no marker the whole body is the text and the marker is None. A problem is a short word for something wrong with
    the marker: the text then cannot be confirmed by hash.
    """
    text = plain_newlines(body or "").rstrip(JS_SPACE)
    cut = text.rfind("\n")
    last = text[cut + 1 :].strip()
    found = _MARKER_LINE.fullmatch(last)
    if not found:
        # A marker that is not the whole last line is not a marker the backend wrote.
        return text, None, ("marker_misplaced" if _MARKER_ANYWHERE.search(text) else None)
    above = text[:cut] if cut >= 0 else ""
    if _MARKER_ANYWHERE.search(above):
        return above, None, "marker_repeated"
    if len(last) > MAX_MARKER:
        return above, None, "marker_unreadable"
    try:
        fields = json.loads(found.group(1))
        digest = fields["body_sha256"]
        if not (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("digest")
        claim = fields.get("claim")
        pmids = tuple(p for p in fields.get("pmids", []) if isinstance(p, str))
        via = fields.get("via")
        return above, Marker(digest, claim if isinstance(claim, str) else None, pmids, via if isinstance(via, str) else None), None
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return above, None, "marker_unreadable"


def confirm(body: str) -> Tuple[str, Optional[Marker], str]:
    """Returns (the text above the marker, the marker, how the text stands).

    "hash_match": the text is what the marker fingerprints. "hash_mismatch": it has changed since, or the marker was
    copied from elsewhere. "no_marker": nothing to compare with. Any other word is a marker problem.
    """
    above, marker, problem = split_marker(body)
    if problem:
        return above, None, problem
    if marker is None:
        return above, None, "no_marker"
    return above, marker, ("hash_match" if body_sha256(above) == marker.body_sha256 else "hash_mismatch")
