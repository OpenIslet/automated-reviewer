"""Shared by the tools that check the code against the real, public source repository.

Reads are anonymous (no sign-in, no token) and kept on disk in local/cache/, which is never published, so a check
can be run again without asking GitHub twice. Anonymous API reads are limited to 60 an hour; file reads are not.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reviewer.config import load  # noqa: E402
from reviewer.github import HttpTransport, Source  # noqa: E402

REAL = ROOT / "local" / "real"
CACHE = ROOT / "local" / "cache"


class CachedTransport:
    """Answers a read from disk when it has been asked before. Refuses anything that is not a read."""

    def __init__(self):
        self._inner = HttpTransport(token=None)
        self.asked = 0
        CACHE.mkdir(parents=True, exist_ok=True)

    def send(self, method, url, body=None, accept="application/vnd.github+json"):
        if method != "GET":
            raise RuntimeError("the checking tools only read")
        kept = CACHE / hashlib.sha256(url.encode("utf-8")).hexdigest()
        if kept.exists():
            return 200, kept.read_bytes()
        self.asked += 1
        status, data = self._inner.send(method, url, body, accept)
        if status == 200:
            kept.write_bytes(data)
        return status, data


def config():
    return load(ROOT / "settings")


def source(transport=None):
    settings = config()
    return Source(transport or CachedTransport(), settings.source_repo, settings.default_branch)


def saved_issues():
    """The local copies made by tools/fetch_local_copies.py, in number order: (issue, events, comments)."""
    for path in sorted(REAL.glob("issue-*.json"), key=lambda p: int(p.stem.split("-")[1])):
        saved = json.loads(path.read_text())
        yield saved["issue"], saved["events"], saved["comments"]
