#!/usr/bin/env python3
"""Writes settings/requirements.lock: every dependency pinned to one version, with the hashes PyPI publishes for it.

    python3 tools/make_lock.py            # writes the lock file
    python3 tools/make_lock.py actions    # prints the commit each official action's latest release points at

Only package metadata is read (names, versions and hashes). Nothing is downloaded or installed.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINNED = {"PyYAML": "6.0.3"}
ACTIONS = ("actions/checkout", "actions/setup-python", "actions/upload-artifact", "actions/download-artifact")


def read(url):
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "lock-file-tool"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def lock():
    lines = [
        "# Every dependency of the automated reviewer, pinned to one version with the hashes PyPI publishes for it.",
        "# Written by tools/make_lock.py. Install with: python -m pip install --require-hashes -r settings/requirements.lock",
        "",
    ]
    for name, version in sorted(PINNED.items()):
        files = read(f"https://pypi.org/pypi/{name}/{version}/json")["urls"]
        hashes = sorted({item["digests"]["sha256"] for item in files if not item.get("yanked")})
        lines.append(f"{name.lower()}=={version} \\")
        lines += [f"    --hash=sha256:{digest}" + (" \\" if i < len(hashes) - 1 else "") for i, digest in enumerate(hashes)]
        print(f"{name} {version}: {len(hashes)} files")
    (ROOT / "settings" / "requirements.lock").write_text("\n".join(lines) + "\n", encoding="utf-8")


def actions():
    for repo in ACTIONS:
        release = read(f"https://api.github.com/repos/{repo}/releases/latest")
        tag = release["tag_name"]
        target = read(f"https://api.github.com/repos/{repo}/git/ref/tags/{tag}")["object"]
        if target["type"] == "tag":  # an annotated tag points at a tag object, which points at the commit
            target = read(f"https://api.github.com/repos/{repo}/git/tags/{target['sha']}")["object"]
        print(f"{repo}@{target['sha']} # {tag}  (released {release['published_at'][:10]})")


if __name__ == "__main__":
    actions() if "actions" in sys.argv[1:] else lock()
