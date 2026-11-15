#!/usr/bin/env python3
"""Saves local copies of real public proposals, for checking the code against real data.

The copies go to local/real/, which is never published: they hold contributors' own words and names, and the
published tests use made-up proposals of the same shape instead.

    python3 tools/fetch_local_copies.py 1 15            # issues 1 to 15, read with `gh api` on this machine
    GH_VIA="ssh that-machine" python3 tools/fetch_local_copies.py 1 15   # the same, run through another machine

It prints where each copy went and a few facts about its shape (author, first line, labels, decision). It never
prints a proposal's text.
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO = os.environ.get("SOURCE_REPO", "OpenIslet/openislet")
OUT = Path(__file__).resolve().parent.parent / "local" / "real"
VIA = shlex.split(os.environ.get("GH_VIA", ""))


def gh(path: str):
    command = ["gh", "api", "-H", "Accept: application/vnd.github+json", path]
    if VIA:
        # Through another machine: one quoted command, with Homebrew's folder on the path.
        command = VIA + ["export PATH=/opt/homebrew/bin:$PATH; " + " ".join(shlex.quote(c) for c in command)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise SystemExit(f"gh api {path} failed: {done.stderr.strip()[:200]}")
    return json.loads(done.stdout)


def main() -> None:
    first, last = int(sys.argv[1]), int(sys.argv[2])
    OUT.mkdir(parents=True, exist_ok=True)
    commits = gh(f"repos/{REPO}/commits?per_page=100")
    (OUT / "commits.json").write_text(json.dumps(commits, indent=1))
    print(f"commits: {len(commits)} saved")
    for n in range(first, last + 1):
        issue = gh(f"repos/{REPO}/issues/{n}")
        events = gh(f"repos/{REPO}/issues/{n}/events?per_page=100")
        comments = gh(f"repos/{REPO}/issues/{n}/comments?per_page=100")
        (OUT / f"issue-{n}.json").write_text(json.dumps({"issue": issue, "events": events, "comments": comments}, indent=1))
        body = issue.get("body") or ""
        first_line = body.split("\n", 1)[0]
        shape = "bot header" if first_line.startswith("Submitted by @") else "maintainer header" if first_line.startswith("Opened directly by the maintainer") else "other"
        labelled = [(e["label"]["name"], e["actor"]["login"], e["created_at"]) for e in events if e.get("event") == "labeled"]
        decisions = [x for x in labelled if x[0].startswith("outcome:") or x[0] in ("decision:needs-work", "status:closed-triage")]
        print(
            f"#{n}: author {issue['user']['login']} | {shape} | marker {'yes' if '<!-- openislet:submission' in body else 'no'}"
            f" | body {len(body)} chars | pull request {'yes' if 'pull_request' in issue else 'no'}"
            f" | first decision {decisions[0] if decisions else None}"
            f" | comments by {[c['user']['login'] for c in comments]}"
        )


if __name__ == "__main__":
    main()
