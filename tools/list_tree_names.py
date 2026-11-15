"""Prints file NAMES (never contents) in the source repository at a few commits, to check the file patterns in the configuration.

It needs a signed-in gh. To run gh on another machine:   GH_VIA="ssh -o BatchMode=yes that-machine" python3 tools/list_tree_names.py
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO = "OpenIslet/openislet"
ROOT = Path(__file__).resolve().parent.parent
INTEREST = re.compile(r"blueprint|playbook|graveyard|ring1|evidence|ceg|corpus|extracted|dossier|stage", re.IGNORECASE)


def gh(path):
    via = shlex.split(os.environ.get("GH_VIA", ""))
    remote = "export PATH=/opt/homebrew/bin:$PATH; gh api " + shlex.quote(path)
    cmd = via + [remote] if via else ["gh", "api", path]
    return json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)


def main():
    commits = json.loads((ROOT / "local/real/commits.json").read_text())
    commits = sorted(commits, key=lambda c: c["commit"]["committer"]["date"])
    print(f"{len(commits)} commits, {commits[0]['commit']['committer']['date']} to {commits[-1]['commit']['committer']['date']}")
    wanted = sys.argv[1:] or [commits[0]["sha"], commits[len(commits) // 2]["sha"], commits[-1]["sha"]]
    for sha in wanted:
        tree = gh(f"repos/{REPO}/git/trees/{sha}?recursive=1")
        blobs = [t for t in tree["tree"] if t["type"] == "blob"]
        when = next((c["commit"]["committer"]["date"] for c in commits if c["sha"].startswith(sha)), "?")
        print(f"\n== {sha[:7]}  {when}  {len(blobs)} files  truncated={tree.get('truncated')}")
        tops = sorted({t["path"].split("/")[0] + ("/" if "/" in t["path"] else "") for t in blobs})
        print("top level:", " ".join(tops))
        shown = 0
        for t in blobs:
            name = t["path"]
            if "/extracted/" in name and shown > 400:
                continue
            if INTEREST.search(name):
                if "/extracted/" in name or "extracted" in name.split("/")[0:2]:
                    continue
                print(f"  {t.get('size', 0):>9}  {name}")
                shown += 1
        per_study = [t["path"] for t in blobs if "extracted" in t["path"]]
        if per_study:
            folders = sorted({p.rsplit("/", 1)[0] for p in per_study})
            print(f"  per-study files: {len(per_study)} in {folders[:4]}  e.g. {per_study[0].rsplit('/', 1)[1]}")


if __name__ == "__main__":
    main()
