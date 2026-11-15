"""The repository context for one proposal: the blueprint, the Stage 0 playbook and the Idea Graveyard file, as they
stood in the last commit before the proposal was submitted.

Never the current head, which may already contain the change the proposal asked for. If no commit precedes the
proposal, or one of the files is not there at that commit, the context is unavailable and the case is excluded.
"""
import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .github import GitHubError

_VERSION = r"([0-9]+(?:\.[0-9]+)*)"


class ContextUnavailable(Exception):
    """The reason is a short fixed word, sometimes followed by the name of a context file from the settings.

    `transient` is True when GitHub could not be read just now. Such a case is tried again on the next run and is
    not recorded as excluded. When it is False the repository itself lacks what is needed, and the case is excluded.
    """

    def __init__(self, reason: str, transient: bool = False):
        super().__init__(reason)
        self.transient = transient


@dataclass(frozen=True)
class ContextFile:
    name: str  # the name in the settings: blueprint, playbook, idea_graveyard
    path: str  # where it was at that commit
    text: str
    sha256: str


@dataclass
class Context:
    commit: str
    commit_date: str
    files: Tuple[ContextFile, ...]
    paths: Dict[str, dict]  # every file at that commit: path -> {"size", "sha"}
    notes: List[str] = field(default_factory=list)

    def full_paths(self) -> frozenset:
        return frozenset(f.path for f in self.files)


def resolve(pattern: str, paths) -> Tuple[Optional[str], bool]:
    """The path a pattern stands for among `paths`, and whether several versions matched.

    A pattern with no "*" is the path itself. A "*" stands for a version number such as 0.5 or 12; when several
    versions are present the highest is used.
    """
    if "*" not in pattern:
        return (pattern if pattern in paths else None), False
    before, after = pattern.split("*", 1)
    shape = re.compile("^" + re.escape(before) + _VERSION + re.escape(after) + "$")
    found = []
    for path in paths:
        hit = shape.match(path)
        if hit:
            found.append((tuple(int(part) for part in hit.group(1).split(".")), path))
    if not found:
        return None, False
    return max(found)[1], len(found) > 1


class ContextReader:
    """Reads contexts for one run. File lists and files are kept, because several proposals often share a commit."""

    def __init__(self, source, context_files):
        self._source = source
        self._context_files = tuple(context_files)
        self._trees: Dict[str, Dict[str, dict]] = {}
        self._files: Dict[Tuple[str, str], bytes] = {}

    def tree(self, sha: str) -> Dict[str, dict]:
        if sha not in self._trees:
            self._trees[sha] = self._source.tree(sha)
        return self._trees[sha]

    def raw(self, sha: str, path: str) -> bytes:
        key = (sha, path)
        if key not in self._files:
            self._files[key] = self._source.file_at(sha, path)
        return self._files[key]

    def text(self, sha: str, path: str) -> str:
        """A file at a commit as text. Raises UnicodeDecodeError if it is not text."""
        return self.raw(sha, path).decode("utf-8")

    def context_for(self, created_at: str) -> Context:
        """The context for a proposal submitted at `created_at`: the last commit strictly before it."""
        return self._context(lambda: self._source.commit_before(created_at))

    def context_at(self, sha: str) -> Context:
        """The context at one named commit. Used by fixtures, which declare their own."""
        return self._context(lambda: self._source.commit(sha))

    def _context(self, find_commit) -> Context:
        try:
            commit = find_commit()
            if commit is None:
                raise ContextUnavailable("no_commit_before_submission")
            paths = self.tree(commit["sha"])
        except GitHubError:
            raise ContextUnavailable("repository_unreadable", transient=True)
        files: List[ContextFile] = []
        notes: List[str] = []
        for name, pattern in self._context_files:
            path, several = resolve(pattern, paths)
            if path is None:
                raise ContextUnavailable(f"file_missing:{name}")
            if several:
                notes.append(f"several_versions:{name}")
            try:
                raw = self.raw(commit["sha"], path)
                text = raw.decode("utf-8")
            except GitHubError:
                raise ContextUnavailable(f"file_unreadable:{name}", transient=True)
            except UnicodeDecodeError:
                raise ContextUnavailable(f"file_not_text:{name}")
            files.append(ContextFile(name, path, text, hashlib.sha256(raw).hexdigest()))
        return Context(commit["sha"], commit["date"], tuple(files), paths, notes)
