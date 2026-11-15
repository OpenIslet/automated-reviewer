"""Prints the SHAPE of GitHub's edit history for real proposals (counts, dates, lengths, yes/no), never their text.

It needs a signed-in gh. To run gh on another machine:   GH_VIA="ssh -o BatchMode=yes that-machine" python3 tools/check_edit_history.py 2 4 11
"""
import hashlib
import json
import os
import shlex
import subprocess
import sys

QUERY = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      createdAt lastEditedAt includesCreatedEdit body
      userContentEdits(first: 100) {
        totalCount pageInfo { hasNextPage }
        nodes { createdAt editedAt deletedAt diff editor { login } }
      }
    }
  }
}"""


def graphql(number):
    via = shlex.split(os.environ.get("GH_VIA", ""))
    remote = (
        "export PATH=/opt/homebrew/bin:$PATH; gh api graphql -F owner=OpenIslet -F name=openislet -F number="
        + str(int(number))
        + " -f query="
        + shlex.quote(QUERY)
    )
    cmd = via + [remote] if via else ["bash", "-c", remote]
    return json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)


def short(text):
    return "none" if text is None else f"{len(text)} chars, sha {hashlib.sha256(text.encode()).hexdigest()[:8]}"


for number in sys.argv[1:]:
    issue = graphql(number)["data"]["repository"]["issue"]
    edits = issue["userContentEdits"]
    print(f"#{number}: created {issue['createdAt']}, last edited {issue['lastEditedAt']}, includesCreatedEdit {issue['includesCreatedEdit']}, body now {short(issue['body'])}")
    print(f"   edits: {edits['totalCount']}, more pages: {edits['pageInfo']['hasNextPage']}")
    for node in edits["nodes"]:
        who = (node["editor"] or {}).get("login")
        print(f"   created {node['createdAt']}  edited {node['editedAt']}  deleted {node['deletedAt']}  by {who}  content {short(node['diff'])}")
