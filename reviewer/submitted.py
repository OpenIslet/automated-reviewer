"""A proposal's text and title as they were submitted.

- Fast path: the signed marker at the end of a bot-opened issue holds a hash of everything above it. If the hash
  matches, the text is as submitted.
- Fallback: otherwise GitHub's edit history is read. A body that was never edited is as submitted. An edited one is
  taken from the first version in the history. If that version was deleted or cannot be reached, the text cannot be
  confirmed and the case is excluded.
- A marker is trusted only when its hash matches the text it sits under.
- The title as submitted is the one before the first rename event.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .integrity import Marker, confirm


@dataclass
class Submitted:
    text: Optional[str]  # everything above the marker, as submitted; None when it cannot be confirmed
    marker: Optional[Marker]  # only a marker whose hash matches `text`
    how: str  # hash_match | never_edited | first_version | unverified
    title: str
    why: Optional[str] = None  # for "unverified": what was wrong with the history
    body_edit_times: List[str] = field(default_factory=list)
    title_change_times: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)

    def edited_before(self, when: str) -> bool:
        """True if the body or the title changed before `when` (the time of the decision)."""
        return any(t < when for t in self.body_edit_times + self.title_change_times)


def as_submitted(issue: Dict, events: List[Dict], source) -> Submitted:
    """`source` is asked for the edit history only when the marker does not settle it. A GitHubError passes through."""
    renames = sorted((e for e in events or [] if e.get("event") == "renamed" and isinstance(e.get("created_at"), str)), key=lambda e: e["created_at"])
    first_name = ((renames[0].get("rename") or {}).get("from")) if renames else None
    title = first_name if isinstance(first_name, str) else (issue.get("title") or "")
    rename_times = [e["created_at"] for e in renames]

    above, marker, how = confirm(issue.get("body") or "")
    if how == "hash_match":
        return Submitted(above, marker, "hash_match", title, title_change_times=rename_times)

    flags = [] if how == "no_marker" else [f"marker_{how}" if not how.startswith("marker_") else how]
    history = source.edit_history(issue["number"])
    if not history["edited"]:
        return Submitted(above, None, "never_edited", title, title_change_times=rename_times, flags=flags)
    times = list(history.get("edit_times") or [])
    if history.get("original") is None:
        return Submitted(None, None, "unverified", title, history.get("why"), times, rename_times, flags)
    first_above, first_marker, first_how = confirm(history["original"])
    if first_how == "hash_match":
        return Submitted(first_above, first_marker, "first_version", title, None, times, rename_times, flags)
    if first_how != "no_marker":
        flags = flags + [f"first_version_{first_how}"]
    return Submitted(first_above, None, "first_version", title, None, times, rename_times, flags)
