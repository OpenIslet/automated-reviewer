"""Fixtures: made-up submissions that test the gate and try to steer the panel.

They are read from files, run only in `fixtures` mode, never opened as issues, and reported apart from real proposals.

- junk and borderline fixtures go to the gate only.
- direct fixtures are text typed without the template. The script must leave them out before any model is asked.
- adversarial fixtures go to both stages. Each declares the commit whose files it is judged against and the time it
  counts as submitted, and names the outcome it tries to force.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

KINDS = ("junk", "borderline", "direct", "adversarial")
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,59}")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_COMMIT = re.compile(r"[0-9a-f]{4,40}")

# The fixed parts of a proposal's body, as the submission backend writes them. The blind review is never sent to a
# model, so a placeholder is enough.
_BODY = """Submitted by @fixture (verified through GitHub)

AI tools (self-reported): draft = not stated, review = not stated

Written against: fixture

Consent: published openly under CC BY 4.0

<details open>
<summary>Draft</summary>

{draft}

</details>

<details>
<summary>Blind review</summary>

Verdict: Supported

</details>
"""


class BadFixture(Exception):
    """A fixture file is not in the expected form. The message names the fixture and the field."""


@dataclass(frozen=True)
class Fixture:
    id: str
    kind: str
    title: str
    draft: Optional[str] = None  # for every kind but "direct"
    body: Optional[str] = None  # for "direct": the whole text as typed
    expect_spam: Optional[bool] = None  # junk: True; borderline: False
    steers_towards: Optional[str] = None  # adversarial: the outcome it tries to force
    context_commit: Optional[str] = None  # adversarial
    submitted_at: Optional[str] = None  # adversarial

    def text(self) -> str:
        """The body a real issue would have."""
        return self.body if self.kind == "direct" else _BODY.format(draft=self.draft.strip("\n"))


def _one(raw, outcome_keys) -> Fixture:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not _ID.fullmatch(raw["id"]):
        raise BadFixture("a fixture has no usable id")
    name = raw["id"]
    kind = raw.get("kind")
    if kind not in KINDS:
        raise BadFixture(f"{name}: kind")
    if not isinstance(raw.get("title"), str) or not raw["title"].strip():
        raise BadFixture(f"{name}: title")
    allowed = {"id", "kind", "title"} | {"direct": {"body", "expect"}, "junk": {"draft", "expect_spam"}, "borderline": {"draft", "expect_spam"}, "adversarial": {"draft", "steers_towards", "context_commit", "submitted_at"}}[kind]
    if set(raw) - allowed:
        raise BadFixture(f"{name}: {sorted(set(raw) - allowed)[0]}")
    text_field = "body" if kind == "direct" else "draft"
    if not isinstance(raw.get(text_field), str) or not raw[text_field].strip():
        raise BadFixture(f"{name}: {text_field}")
    if kind == "direct":
        if raw.get("expect") != "unparsed":
            raise BadFixture(f"{name}: expect")
        return Fixture(name, kind, raw["title"], body=raw["body"])
    if kind in ("junk", "borderline"):
        if raw.get("expect_spam") is not (kind == "junk"):
            raise BadFixture(f"{name}: expect_spam")
        return Fixture(name, kind, raw["title"], draft=raw["draft"], expect_spam=raw["expect_spam"])
    if raw.get("steers_towards") not in outcome_keys:
        raise BadFixture(f"{name}: steers_towards")
    if not isinstance(raw.get("context_commit"), str) or not _COMMIT.fullmatch(raw["context_commit"]):
        raise BadFixture(f"{name}: context_commit")
    if not isinstance(raw.get("submitted_at"), str) or not _TIME.fullmatch(raw["submitted_at"]):
        raise BadFixture(f"{name}: submitted_at")
    return Fixture(name, kind, raw["title"], draft=raw["draft"], steers_towards=raw["steers_towards"], context_commit=raw["context_commit"], submitted_at=raw["submitted_at"])


def load_fixtures(folder, outcome_keys) -> List[Fixture]:
    """Every fixture in the folder's .yml files, in file-name order and then the order written."""
    found: List[Fixture] = []
    for path in sorted(Path(folder).glob("*.yml")):
        listed = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(listed, list):
            raise BadFixture(f"{path.name}: must be a list of fixtures")
        found += [_one(raw, outcome_keys) for raw in listed]
    if len({f.id for f in found}) != len(found):
        raise BadFixture("an id is used twice")
    return found
