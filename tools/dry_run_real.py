#!/usr/bin/env python3
"""A whole dry run over the real proposals, on this machine only: stand-in models, and nothing posted anywhere.

    python3 tools/dry_run_real.py            # every decided proposal
    python3 tools/dry_run_real.py 4 11       # also name these (self-decided ones are reviewed only when named)
    python3 tools/dry_run_real.py fixtures   # the made-up test submissions, in fixture mode

Issues, label events and comments come from the local copies (tools/fetch_local_copies.py). Commits and files are
read from GitHub anonymously and kept in local/cache/. The results and the comments that would be posted are written
to local/out/, which is never published. What is printed is shape only.
"""
import json
import sys

from real import ROOT, CachedTransport, config, saved_issues

from reviewer import record as records
from reviewer.assemble import Prompts
from reviewer.config import PROVIDERS
from reviewer.github import Source
from reviewer.models import fake_adapters
from reviewer.run import reconcile

OUT = ROOT / "local" / "out"


class LocalSource(Source):
    """Issues from the local copies; commits and files from GitHub, read anonymously."""

    def __init__(self, settings):
        super().__init__(CachedTransport(), settings.source_repo, settings.default_branch)
        self._saved = {issue["number"]: (issue, events, comments) for issue, events, comments in saved_issues()}

    def list_issues(self):
        return [issue for issue, _, _ in self._saved.values()]

    def issue(self, number):
        return self._saved[number][0]

    def events(self, number):
        return self._saved[number][1]

    def comments(self, number):
        return self._saved[number][2]

    def edit_history(self, number):
        # Anonymous reads cannot ask for the edit history. tools/check_edit_history.py (run where gh is signed in) showed on
        # 14 Nov 2026 that none of issues 1 to 15 has ever been edited.
        return {"edited": False}


def fixtures_dry_run():
    """Every shipped fixture through fixture mode, with stand-in models, against the real repository's files."""
    from reviewer.fixtures import load_fixtures
    from reviewer.run import reconcile_fixtures

    settings = config()
    prompts = Prompts.load(ROOT / "settings" / "prompts")
    fixtures = load_fixtures(ROOT / "tests" / "fixtures" / "reviewer", settings.outcome_keys)
    adapters = fake_adapters(PROVIDERS)
    results = []
    summary = reconcile_fixtures(settings, prompts, LocalSource(settings), [], adapters, {"AUTOMATED_REVIEWER_ENABLED": "true"}, "2026-11-14T12:00:00Z", results, fixtures, override_caps=True, dry_run=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fixture-results.json").write_text(json.dumps(results, indent=1))
    cleaned = [records.clean(raw, settings) for raw in results]
    for record in cleaned:
        records.render(record, settings)
    kinds = {}
    for record in cleaned:
        row = kinds.setdefault(record["fixture"]["kind"], {"run": 0, "complete": 0, "left out": 0, "panel asked": 0})
        row["run"] += 1
        row["complete"] += record["complete"]
        row["left out"] += record["exclusion"] is not None
        row["panel asked"] += bool(record["panel"]["slots"])
    sizes = sorted({record["context"]["index_size"] for record in cleaned if record["fixture"]["kind"] == "adversarial"})
    commits = sorted({(record["context"]["commit"] or "")[:7] for record in cleaned if record["fixture"]["kind"] == "adversarial"})
    print(f"fixtures: reviewed {len(summary.reviewed)} | left out {len(summary.excluded)} | later {summary.left_for_later} | by kind {kinds}")
    print(f"adversarial fixtures: context commit {commits} | earlier proposals in their index {sizes} | stand-in calls {sum(len(a.asked) for a in adapters.values())}")


def main():
    if "fixtures" in sys.argv[1:]:
        return fixtures_dry_run()
    settings = config()
    prompts = Prompts.load(ROOT / "settings" / "prompts")
    named = [int(n) for n in sys.argv[1:]]
    adapters = fake_adapters(PROVIDERS)
    OUT.mkdir(parents=True, exist_ok=True)
    everything = []
    for numbers in ([None] + ([named] if named else [])):
        results = []
        summary = reconcile(settings, prompts, LocalSource(settings), [], adapters, {"AUTOMATED_REVIEWER_ENABLED": "true"}, "2026-11-14T12:00:00Z", results, numbers=numbers, override_caps=True, dry_run=True)
        print(f"{'named ' + str(numbers) if numbers else 'scheduled'}: reviewed {summary.reviewed} | left out {summary.excluded} | later {summary.left_for_later} | policy {summary.policy_version[:12]}")
        everything += results
    (OUT / "results.json").write_text(json.dumps(everything, indent=1))
    for raw in everything:
        clean = records.clean(raw, settings)
        text = records.render(clean, settings)
        (OUT / f"comment-{clean['proposal']}{'-named' if clean['self_decided'] and not clean['exclusion'] else ''}.md").write_text(text)
        back = records.clean(records.parse(text), settings)
        slots = [(s["slot"], s["status"]) for stage in ("gate", "panel") for s in clean[stage]["slots"]]
        print(
            f"#{clean['proposal']}: {'left out, ' + clean['exclusion']['reason'] if clean['exclusion'] else 'reviewed'} | decision {clean['decision']['label']} | same as {clean['decision']['same_as']} ({clean['decision']['same_as_how']})"
            f" | complete {clean['complete']} | gate {clean['gate']['recommendation']} | panel {clean['panel']['recommendation']} | second opinions {clean['panel']['second_opinion']}"
            f" | actions {clean['actions']} | comment {len(text)} chars, reads back equal: {back == clean} | slots answered {sum(1 for _, s in slots if s == 'verdict')} of {len(slots)}"
        )
    # A preview of the scoreboard, from the stand-in results. The real scoreboard never counts a dry run.
    from reviewer import identity, scoreboard
    from reviewer.run import standing_now

    as_if_real = [dict(records.clean(raw, settings), dry_run=False) for raw in everything]
    latest = {record["proposal"]: record for record in as_if_real}  # a named review replaces the exclusion it follows
    found = scoreboard.numbers(list(latest.values()), settings, identity.policy_version(settings, prompts), standing_now(LocalSource(settings), settings), "2026-11-14T12:00:00Z")
    page = "PREVIEW FROM A DRY RUN. Every verdict counted below is a stand-in: no model was called.\n\n" + scoreboard.render(found, settings)
    (OUT / "scoreboard-preview.md").write_text(page)
    (OUT / "scoreboard-preview.json").write_text(scoreboard.as_json(found))
    print(f"scoreboard preview: {len(page)} chars | decided today {found['coverage']['decided_now']} | reviewed {found['coverage']['reviewed']} | left out {found['coverage']['left_out']}")
    calls = sum(len(adapter.asked) for adapter in adapters.values())
    print(f"stand-in calls: {calls} | files in {OUT.relative_to(ROOT)}: {len(list(OUT.iterdir()))}")


if __name__ == "__main__":
    main()
