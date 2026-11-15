"""Reads the source repository and writes to the home repository, through GitHub's API.

Three classes:

- Source: read-only. Issues, label events, comments, commits, file lists and files by commit, and an issue's edit history.
- Home: the log and the scoreboard. The only class with methods that write, and it can only build addresses inside
  the home repository.
- HttpTransport: the one place that talks to the network. Tests use FakeTransport from tests/fakes.py in its place.

Errors carry the kind of request and the status code only. Nothing here prints a response, a header or the token.
"""
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
PAGE = 100
MAX_PAGES = 50
COMMENT_LIMIT = 65536

EDIT_HISTORY_QUERY = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      createdAt lastEditedAt includesCreatedEdit
      userContentEdits(first: 100) {
        totalCount pageInfo { hasNextPage }
        nodes { createdAt deletedAt diff }
      }
    }
  }
}"""


class GitHubError(Exception):
    """A request failed. The message is the kind of request and the status code, never a response body."""

    def __init__(self, what: str, status: int):
        super().__init__(f"{what}: status {status}")
        self.what = what
        self.status = status


def request_headers(token: Optional[str], url: str, accept: str, has_body: bool) -> Dict[str, str]:
    """The headers for one request. The token goes to the API only: files are read from the raw address without it."""
    headers = {"Accept": accept, "User-Agent": "automated-reviewer-script"}
    if url.startswith(API + "/"):
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        if token:
            headers["Authorization"] = f"Bearer {token}"
    if has_body:
        headers["Content-Type"] = "application/json"
    return headers


class HttpTransport:
    """Sends one request and returns (status, body). Never logs anything."""

    def __init__(self, token: Optional[str] = None, timeout: int = 60):
        self._token = token
        self._timeout = timeout

    def send(self, method: str, url: str, body: Optional[dict] = None, accept: str = "application/vnd.github+json") -> Tuple[int, bytes]:
        headers = request_headers(self._token, url, accept, body is not None)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, b""
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):  # includes an answer cut short
            return 0, b""


def _json(what: str, body: bytes):
    """The answer as JSON. An answer that is not JSON is a failed request, to be tried again, not a crash."""
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise GitHubError(what + " (answer not readable)", 0) from None


def _query(params: Dict[str, object]) -> str:
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})


class _Api:
    def __init__(self, transport, repo: str):
        self._transport = transport
        self.repo = repo

    def _get(self, what: str, path: str, params: Optional[dict] = None):
        url = f"{API}/repos/{self.repo}/{path}"
        if params:
            url += "?" + _query(params)
        status, body = self._transport.send("GET", url)
        if status != 200:
            raise GitHubError(what, status)
        return _json(what, body)

    def _get_all(self, what: str, path: str, params: Optional[dict] = None) -> List[dict]:
        rows: List[dict] = []
        for page in range(1, MAX_PAGES + 1):
            batch = self._get(what, path, dict(params or {}, per_page=PAGE, page=page))
            if not isinstance(batch, list):
                raise GitHubError(what + " (answer not a list)", 0)
            rows.extend(batch)
            if len(batch) < PAGE:
                return rows
        raise GitHubError(what + " (too many pages)", 0)


class Source(_Api):
    """The source repository, read-only. This class has no method that writes."""

    def __init__(self, transport, repo: str, branch: str):
        super().__init__(transport, repo)
        self.branch = branch

    def list_issues(self) -> List[dict]:
        """Every issue, open or closed, oldest first. Pull requests are in the list too; the caller leaves them out."""
        return self._get_all("list issues", "issues", {"state": "all", "sort": "created", "direction": "asc"})

    def issue(self, number: int) -> dict:
        return self._get("read issue", f"issues/{int(number)}")

    def events(self, number: int) -> List[dict]:
        return self._get_all("read issue events", f"issues/{int(number)}/events")

    def comments(self, number: int) -> List[dict]:
        return self._get_all("read issue comments", f"issues/{int(number)}/comments")

    def commit_before(self, when: str) -> Optional[dict]:
        """The last commit on the default branch dated strictly before `when` (an ISO time in UTC), or None.

        GitHub's `until` includes commits dated at that exact second, so those are stepped over.
        """
        for commit in self._get("list commits", "commits", {"sha": self.branch, "until": when, "per_page": 20}):
            date = commit["commit"]["committer"]["date"]
            if date < when:
                return {"sha": commit["sha"], "date": date}
        return None

    def commit(self, sha: str) -> dict:
        """One commit by its id: {"sha": the full id, "date": ...}."""
        found = self._get("read commit", f"commits/{sha}")
        return {"sha": found["sha"], "date": found["commit"]["committer"]["date"]}

    def tree(self, sha: str) -> Dict[str, dict]:
        """Every file at a commit: path -> {"size": ..., "sha": ...}."""
        data = self._get("list files at commit", f"git/trees/{sha}", {"recursive": 1})
        if data.get("truncated"):
            raise GitHubError("list files at commit (list cut short)", 0)
        return {t["path"]: {"size": t.get("size", 0), "sha": t["sha"]} for t in data["tree"] if t.get("type") == "blob"}

    def file_at(self, sha: str, path: str) -> bytes:
        """A file as it was at a commit. Read from the raw address, which serves files of any size and uses no API allowance."""
        url = f"{RAW}/{self.repo}/{sha}/{urllib.parse.quote(path)}"
        status, body = self._transport.send("GET", url, accept="*/*")
        if status != 200:
            raise GitHubError("read file at commit", status)
        return body

    def edit_history(self, number: int) -> dict:
        """What GitHub records about edits to an issue's body.

        Returns {"edited": False} when the body has never been edited, so the body now is the body as submitted.
        Returns {"edited": True, "original": text, "edit_times": [...]} when the first version is in the history.
        Returns {"edited": True, "original": None, "why": ..., "edit_times": [...]} when it is not: a deleted
        revision, a history that does not start at creation, or one too long to read in one request.
        "edit_times" are the times of the edits after creation, oldest first, as far as the history shows them.
        Raises GitHubError when the history cannot be read at all.
        """
        owner, name = self.repo.split("/", 1)
        payload = {"query": EDIT_HISTORY_QUERY, "variables": {"owner": owner, "name": name, "number": int(number)}}
        status, body = self._transport.send("POST", f"{API}/graphql", body=payload)
        if status != 200:
            raise GitHubError("read edit history", status)
        answer = _json("read edit history", body)
        issue = (((answer.get("data") or {}).get("repository") or {}).get("issue")) if isinstance(answer, dict) and not answer.get("errors") else None
        if not issue:
            raise GitHubError("read edit history (no answer)", 0)
        edits = issue["userContentEdits"]
        if edits["totalCount"] == 0 and issue["lastEditedAt"] is None:
            return {"edited": False}
        times = sorted(node["createdAt"] for node in edits["nodes"])
        if issue["includesCreatedEdit"]:
            times = times[1:]  # the first entry is the creation itself
        if not times and issue["lastEditedAt"]:
            times = [issue["lastEditedAt"]]
        if edits["pageInfo"]["hasNextPage"]:
            return {"edited": True, "original": None, "why": "history_too_long", "edit_times": times}
        if not issue["includesCreatedEdit"] or not edits["nodes"]:
            return {"edited": True, "original": None, "why": "history_does_not_start_at_creation", "edit_times": times}
        oldest = min(edits["nodes"], key=lambda node: node["createdAt"])
        if oldest["deletedAt"] is not None or oldest["diff"] is None:
            return {"edited": True, "original": None, "why": "revision_deleted", "edit_times": times}
        return {"edited": True, "original": oldest["diff"], "edit_times": times}


class Home(_Api):
    """The home repository: the log and the scoreboard. Every address this class builds is inside that repository."""

    def __init__(self, transport, repo: str, source_repo: str):
        if repo.lower() == source_repo.lower():
            raise ValueError("the home repository must not be the source repository")
        super().__init__(transport, repo)

    def comments(self, issue_number: int) -> List[dict]:
        return self._get_all("read log comments", f"issues/{int(issue_number)}/comments")

    def issue(self, issue_number: int) -> dict:
        return self._get("read home issue", f"issues/{int(issue_number)}")

    def _write(self, what: str, method: str, path: str, text: str) -> dict:
        if len(text) > COMMENT_LIMIT:
            raise ValueError(f"{what}: longer than GitHub allows")
        status, body = self._transport.send(method, f"{API}/repos/{self.repo}/{path}", body={"body": text})
        if status not in (200, 201):
            raise GitHubError(what, status)
        return _json(what, body)

    def create_comment(self, issue_number: int, text: str) -> dict:
        return self._write("post comment", "POST", f"issues/{int(issue_number)}/comments", text)

    def update_comment(self, comment_id: int, text: str) -> dict:
        return self._write("edit comment", "PATCH", f"issues/comments/{int(comment_id)}", text)

    def set_issue_body(self, issue_number: int, text: str) -> dict:
        return self._write("edit issue body", "PATCH", f"issues/{int(issue_number)}", text)
