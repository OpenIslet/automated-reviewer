"""One made-up repository and a handful of made-up proposals, for the end-to-end tests."""
import json

from reviewer.assemble import Prompts
from reviewer.config import load
from reviewer.github import Source
from reviewer.pipeline import Readers

from .builders import DRAFT, checks_comment, issue, issue_text, labelled, person_comment, with_marker
from .fakes import SOURCE, FakeTransport
from .test_config import SHIPPED

SETTINGS = load(SHIPPED)
PROMPTS = Prompts.load(SHIPPED / "prompts")
DECIDER = sorted(SETTINGS.allowed_deciders)[0]
BOT = SETTINGS.submission_bot

PROTOCOL = "\n".join(f"protocol line {n}" for n in range(1, 121))
DOSSIERS = json.dumps({"study_metadata": {"pmid": "22222222", "pmcid": None, "doi": None, "nct_id": None}})
CORPUS = json.dumps({"pmid": "22222222", "pmcid": None, "doi": None})
FILES = {
    "ISLET-1-BLUEPRINT.md": "BLUEPRINT-V1. The blueprint as it stood before the proposal.",
    "docs/STAGE-0-EXECUTION-PLAYBOOK.md": "PLAYBOOK-V1. The playbook.",
    "data/graveyard/idea_graveyard_v0.5.json": '{"entries": ["IDEA-GRAVEYARD-V1"]}',
    "data/protocols/protocol.md": PROTOCOL,
    "data/extracted/22222222.json": "{}",
    "data/extracted/ring1_study_dossiers_v0.5.jsonl": DOSSIERS,
    "data/raw/corpus_manifest_all_sources.jsonl": CORPUS,
}
CITING_DRAFT = DRAFT.replace("A made-up study reports the effect in one assay only.", "A made-up study (PMID 22222222) reports the effect in one assay only. See data/protocols/protocol.md:50 and PMID 99999999.")


def decided(label, at):
    return labelled(label, actor=DECIDER, at=at)


def proposal(number, created_at, draft=None, login="some-one", title=None, **text):
    body = with_marker(issue_text(login=login, draft=draft or CITING_DRAFT, **text), claim=f"Claim of proposal {number}.", pmids=("22222222",))
    return issue(number=number, author=BOT, body=body, created_at=created_at, title=title or f"[IIP] Proposal {number}")


def bot_events(at):
    return [labelled("submission", actor=BOT, at=at), labelled("status:queued", actor=BOT, at=at), labelled("type:improvement", actor=BOT, at=at)]


class World:
    """A source repository with two commits, one earlier proposal (#2) and one target proposal (#10)."""

    def __init__(self):
        self.fake = FakeTransport()
        self.fake.add_commit("aaa1", "2026-11-01T05:00:00Z", FILES)
        self.fake.add_commit("bbb2", "2026-11-10T05:00:00Z", {"ISLET-1-BLUEPRINT.md": "BLUEPRINT-V2. THE CHANGE THE PROPOSAL ASKED FOR is now in."})
        self.rows = {}
        self.add(proposal(2, "2026-11-02T00:00:00Z"), bot_events("2026-11-02T00:00:00Z") + [decided("outcome:evidence", "2026-11-03T00:00:00Z")], [checks_comment(at="2026-11-02T00:00:01Z")])
        target = proposal(10, "2026-11-09T12:00:00Z", orcid="0000-0002-1825-0097", stated="name A Person, X [x.com/aperson](https://x.com/aperson)")
        events = bot_events("2026-11-09T12:00:00Z") + [decided("outcome:already-covered", "2026-11-11T00:00:05Z"), decided("status:reviewed", "2026-11-11T00:00:06Z")]
        comments = [
            checks_comment(at="2026-11-09T12:00:01Z"),
            person_comment("HUMAN-COMMENT-PHRASE: I think this is a duplicate.", author="a-passer-by", at="2026-11-10T00:00:00Z"),
            person_comment("DECISION-COMMENT-PHRASE. Thank you.\n\nSame as: IIP-0002", author=DECIDER, at="2026-11-11T00:00:00Z"),
        ]
        self.add(target, events, comments)

    def add(self, one, events=None, comments=None):
        self.fake.add_issue(one, events=events or [], comments=comments or [])
        self.rows[one["number"]] = (one, events or [], comments or [])
        return one

    def everything(self):
        return [(one, events) for one, events, _ in sorted(self.rows.values(), key=lambda row: row[0]["number"])]

    def source(self):
        return Source(self.fake, SOURCE, "main")

    def readers(self):
        return Readers(self.source(), SETTINGS)
