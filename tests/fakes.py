"""A stand-in for GitHub, for tests. It answers the same addresses the real API does, from data held in memory.

Everything it is asked is kept in `requests`, so a test can say what was read and prove that nothing was written
where it must not be.
"""
import json
import re
import urllib.parse
from typing import Dict, List, Optional

from reviewer.github import API, RAW

SOURCE = "OpenIslet/openislet"
HOME = "OpenIslet/automated-reviewer"
RECORD_AUTHOR = {"login": "github-actions[bot]", "type": "Bot"}


class FakeTransport:
    def __init__(self):
        self.requests: List[tuple] = []
        self.issues: Dict[int, dict] = {}
        self.events: Dict[int, list] = {}
        self.comments: Dict[int, list] = {}
        self.histories: Dict[int, dict] = {}
        self.commits: List[dict] = []  # each: {"sha", "date", "files": {path: bytes}}
        self.home_issues: Dict[int, dict] = {}
        self.home_comments: Dict[int, list] = {}
        self.fail: Dict[str, int] = {}  # part of an address -> the status to answer with
        self.truncated_trees = False
        self._next_id = 1000

    # ---- filling it ----------------------------------------------------------------------------------------------

    def add_issue(self, issue: dict, events: Optional[list] = None, comments: Optional[list] = None) -> dict:
        if "labels" not in issue:  # the labels the issue carries now, as GitHub lists them: applied and not removed since
            carried = []
            for event in sorted(events or [], key=lambda e: e.get("created_at") or ""):
                name = (event.get("label") or {}).get("name")
                if event.get("event") == "labeled" and name not in carried:
                    carried.append(name)
                elif event.get("event") == "unlabeled" and name in carried:
                    carried.remove(name)
            issue["labels"] = [{"name": name} for name in carried]
        self.issues[issue["number"]] = issue
        self.events[issue["number"]] = list(events or [])
        self.comments[issue["number"]] = list(comments or [])
        return issue

    def add_commit(self, sha: str, date: str, files: Optional[Dict[str, object]] = None, remove=()) -> None:
        """A commit on the default branch. It keeps the files of the commit before it, plus or minus the ones given."""
        snapshot = dict(self.commits[-1]["files"]) if self.commits else {}
        for path in remove:
            snapshot.pop(path, None)
        for path, content in (files or {}).items():
            snapshot[path] = content.encode("utf-8") if isinstance(content, str) else content
        self.commits.append({"sha": sha, "date": date, "files": snapshot})
        self.commits.sort(key=lambda c: c["date"])

    def add_home_issue(self, number: int, body: str = "") -> None:
        self.home_issues[number] = {"number": number, "body": body, "user": dict(RECORD_AUTHOR)}
        self.home_comments.setdefault(number, [])

    def add_home_comment(self, issue_number: int, body: str, author: Optional[dict] = None) -> dict:
        self._next_id += 1
        comment = {"id": self._next_id, "body": body, "user": dict(author or RECORD_AUTHOR), "created_at": "2026-11-20T00:00:00Z"}
        self.home_comments.setdefault(issue_number, []).append(comment)
        return comment

    # ---- what was asked ------------------------------------------------------------------------------------------

    def writes(self) -> List[tuple]:
        return [(method, url) for method, url in self.requests if method != "GET" and not url.endswith("/graphql")]

    def count(self, part: str) -> int:
        return sum(1 for _, url in self.requests if part in url)

    # ---- answering -----------------------------------------------------------------------------------------------

    def send(self, method: str, url: str, body: Optional[dict] = None, accept: str = "application/vnd.github+json"):
        self.requests.append((method, url))
        for part, status in self.fail.items():
            if part in url:
                return status, b""
        parsed = urllib.parse.urlsplit(url)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = urllib.parse.unquote(parsed.path)
        try:
            if base == RAW:
                return self._raw(path)
            if base == API and path == "/graphql" and method == "POST":
                return self._graphql(body)
            if base == API:
                for repo, handler in ((SOURCE, self._source), (HOME, self._home)):
                    prefix = f"/repos/{repo}/"
                    if path.startswith(prefix):
                        return handler(method, path[len(prefix) :], params, body)
        except (KeyError, StopIteration, AttributeError):  # no such issue, commit or file
            return 404, b""
        return 404, b""

    @staticmethod
    def _ok(data, status: int = 200):
        return status, json.dumps(data).encode("utf-8")

    @staticmethod
    def _page(rows: list, params: dict):
        size, page = int(params.get("per_page", 30)), int(params.get("page", 1))
        return rows[(page - 1) * size : page * size]

    def _raw(self, path: str):
        found = re.fullmatch(rf"/{re.escape(SOURCE)}/([0-9a-f]+)/(.+)", path)
        commit = next(c for c in self.commits if c["sha"] == found.group(1))
        return 200, commit["files"][found.group(2)]

    def _graphql(self, body: dict):
        number = body["variables"]["number"]
        if number not in self.issues:
            return self._ok({"data": {"repository": {"issue": None}}})
        history = self.histories.get(number) or {
            "createdAt": self.issues[number].get("created_at"),
            "lastEditedAt": None,
            "includesCreatedEdit": False,
            "userContentEdits": {"totalCount": 0, "pageInfo": {"hasNextPage": False}, "nodes": []},
        }
        return self._ok({"data": {"repository": {"issue": history}}})

    def _source(self, method: str, path: str, params: dict, body):
        if method != "GET":
            return 403, b""  # the trial's token cannot write to the source repository, and must never try
        if path == "issues":
            rows = sorted(self.issues.values(), key=lambda i: (i.get("created_at", ""), i["number"]))
            return self._ok(self._page(rows, params))
        found = re.fullmatch(r"issues/(\d+)(/events|/comments)?", path)
        if found:
            number, what = int(found.group(1)), found.group(2)
            if what is None:
                return self._ok(self.issues[number])
            rows = self.events[number] if what == "/events" else self.comments[number]
            return self._ok(self._page(rows, params))
        found = re.fullmatch(r"commits/([0-9a-f]+)", path)
        if found:
            commit = next(c for c in self.commits if c["sha"] == found.group(1))
            return self._ok({"sha": commit["sha"], "commit": {"committer": {"date": commit["date"]}}})
        if path == "commits":
            until = params.get("until")
            rows = [c for c in reversed(self.commits) if until is None or c["date"] <= until]
            listed = [{"sha": c["sha"], "commit": {"committer": {"date": c["date"]}}} for c in rows]
            return self._ok(self._page(listed, params))
        found = re.fullmatch(r"git/trees/([0-9a-f]+)", path)
        if found:
            commit = next(c for c in self.commits if c["sha"] == found.group(1))
            tree = [{"path": p, "type": "blob", "size": len(content), "sha": "b" + str(abs(hash(p)))} for p, content in sorted(commit["files"].items())]
            tree.append({"path": "data", "type": "tree", "sha": "t1"})
            return self._ok({"sha": commit["sha"], "tree": tree, "truncated": self.truncated_trees})
        return 404, b""

    def _home(self, method: str, path: str, params: dict, body):
        found = re.fullmatch(r"issues/(\d+)/comments", path)
        if found:
            number = int(found.group(1))
            if method == "GET":
                return self._ok(self._page(self.home_comments[number], params))
            if method == "POST" and number in self.home_issues:
                return self._ok(self.add_home_comment(number, body["body"]), 201)
        found = re.fullmatch(r"issues/comments/(\d+)", path)
        if found and method == "PATCH":
            for comments in self.home_comments.values():
                for comment in comments:
                    if comment["id"] == int(found.group(1)):
                        comment["body"] = body["body"]
                        return self._ok(comment)
            return 404, b""
        found = re.fullmatch(r"issues/(\d+)", path)
        if found:
            issue = self.home_issues[int(found.group(1))]
            if method == "PATCH":
                issue["body"] = body["body"]
            return self._ok(issue)
        return 404, b""
