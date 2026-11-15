#!/usr/bin/env python3
"""The entry point for the jobs.

    scripts/automated_reviewer.py review --out results.json --dry-run     reads GitHub, asks stand-in models, saves results
    scripts/automated_reviewer.py publish --in results.json               writes the saved results to the log
    scripts/automated_reviewer.py scoreboard --out scoreboard.json        rebuilds the scoreboard from the log
    scripts/automated_reviewer.py pending                                 says whether a run would have anything to do

Inputs from the workflow arrive as environment variables, never on a command line:

    GITHUB_TOKEN                 the workflow's own token
    AUTOMATED_REVIEWER_ENABLED   the kill switch: no job reads, asks or writes anything unless this is "true"
    REVIEWER_NUMBERS             for a manual run: proposal numbers, which can only narrow the set
    REVIEWER_OVERRIDE_CAPS       for a manual run: "true" lifts the limits (never the kill switch)
    REVIEWER_MODE                "fixtures" runs the made-up test submissions; anything else reviews real proposals

What this prints is public when it runs in a workflow. It prints counts and fixed words only: never the environment,
a header, a response or the text of an error.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reviewer import caps  # noqa: E402
from reviewer import config as settings_module  # noqa: E402
from reviewer import review as review_module  # noqa: E402
from reviewer import scoreboard  # noqa: E402
from reviewer.assemble import Prompts  # noqa: E402
from reviewer.fixtures import load_fixtures  # noqa: E402
from reviewer.github import Home, HttpTransport, Source  # noqa: E402
from reviewer.models import ProviderError, fake_adapters  # noqa: E402
from reviewer.providers import real_adapters  # noqa: E402
from reviewer.run import pending_work, publish, publish_scoreboard, read_log, reconcile, reconcile_fixtures  # noqa: E402

SETTINGS_FOLDER = ROOT / "settings"
FIXTURES_FOLDER = ROOT / "tests" / "fixtures" / "reviewer"
NO_KEY = "No provider key is set, so there is no model to ask. Nothing was read and no model was called. Use --dry-run for stand-in models."
NO_LOG = "The log and scoreboard issues are not set in the settings. Without the log a run has no memory and no limits, so nothing was read and no model was called."
SWITCH_OFF = "The kill switch is off. Nothing was read and no model was called."
SWITCH_OFF_NO_WRITE = "The kill switch is off. Nothing was read and nothing was written."


def numbers_from(text):
    """Proposal numbers from an environment variable. Anything that is not a list of whole numbers stops the run."""
    if text is None or not text.strip():
        return None
    if not re.fullmatch(r"\s*\d{1,7}(?:[\s,]+\d{1,7})*\s*", text):
        raise ValueError("proposal numbers must be whole numbers")
    return [int(n) for n in re.findall(r"\d+", text)]


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def review(args, env, out=print) -> int:
    settings = settings_module.load(args.settings)  # stops here on any mode but "shadow", before anything is read
    if not caps.enabled(env):
        out(SWITCH_OFF)
        return 0
    adapters = fake_adapters(settings_module.PROVIDERS) if args.dry_run else real_adapters(env)
    if not adapters:
        out(NO_KEY)
        return 2
    if not args.dry_run and not settings.can_record():
        out(NO_LOG)
        return 2
    prompts = Prompts.load(Path(args.settings) / "prompts")
    numbers = numbers_from(env.get("REVIEWER_NUMBERS"))
    transport = HttpTransport(env.get("GITHUB_TOKEN"))
    source = Source(transport, settings.source_repo, settings.default_branch)
    log = read_log(Home(transport, settings.home_repo, settings.source_repo), settings)
    results = []
    override = str(env.get("REVIEWER_OVERRIDE_CAPS", "")).lower() == "true"
    now = now_utc()

    def save() -> None:
        Path(args.out).write_text(json.dumps(results), encoding="utf-8")

    if not args.dry_run:
        review_module.wait = time.sleep  # a short pause before a real request is tried again
    try:
        if env.get("REVIEWER_MODE") == "fixtures":
            fixtures = load_fixtures(FIXTURES_FOLDER, settings.outcome_keys)
            summary = reconcile_fixtures(settings, prompts, source, log, adapters, env, now, results, fixtures, override_caps=override, dry_run=args.dry_run, checkpoint=save)
        else:
            summary = reconcile(settings, prompts, source, log, adapters, env, now, results, numbers=numbers, override_caps=override, dry_run=args.dry_run, checkpoint=save)
    finally:
        save()  # whatever happened, what was done is kept. It is also saved after each proposal.
    if summary.status == "disabled":
        out(SWITCH_OFF)
        return 0
    if summary.status != "ran":
        out(f"Stopped before any proposal was read: {summary.status.replace('_', ' ')}.")
        return 1
    out(f"{'Dry run with stand-in models' if args.dry_run else 'Models asked: ' + ', '.join(sorted(adapters))}. Policy version {summary.policy_version[:12]}.")
    out(f"Reviewed: {summary.reviewed}. Left out: {summary.excluded}. Decisions recorded: {summary.decisions_recorded}. Labels updated: {summary.labels_updated}. Already done: {summary.already_done}.")
    out(f"Left for a later run: {summary.left_for_later}. Stopped by: {summary.stopped_by}.")
    turned_down = sum(1 for record in results for stage in ("gate", "panel") for slot in record[stage]["slots"] for call in slot["calls"] if call["status"] == "rejected" and call["at"] == now)
    faults = sorted({why.split(":", 1)[1] for why in summary.left_for_later.values() if why.startswith("internal_error:")})
    if turned_down:
        out(f"A provider turned down {turned_down} request(s). Each is asked again on later runs before it is recorded as an error for that model.")
    if faults:  # the kind of each fault is safe to print; its text is not
        out(f"An unexpected fault left some proposals for a later run. Kind of fault: {', '.join(faults)}.")
    return 1 if turned_down or faults else 0  # the results are saved either way; a failed run is one a person should look at


def publish_results(args, env, out=print) -> int:
    settings = settings_module.load(args.settings)
    if not caps.enabled(env):
        out(SWITCH_OFF_NO_WRITE)
        return 0
    try:
        saved = json.loads(Path(getattr(args, "in")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out("The results file could not be read.")
        return 1
    home = Home(HttpTransport(env.get("GITHUB_TOKEN")), settings.home_repo, settings.source_repo)
    summary = publish(saved, settings, home, now_utc(), pause=lambda: time.sleep(1.5))
    out(f"Posted: {summary.posted}. Edited: {summary.edited}. Refused: {summary.refused}. Not written, because GitHub would not take them: {summary.failed}.")
    return 1 if summary.failed else 0


def pending(args, env, out=print) -> int:
    """A quick look: how many proposals a run would do something for. Asks no model and writes nothing to GitHub."""
    settings = settings_module.load(args.settings)
    prompts = Prompts.load(Path(args.settings) / "prompts")
    count = 0
    if caps.enabled(env):  # with the switch off nothing is read at all
        transport = HttpTransport(env.get("GITHUB_TOKEN"))
        source = Source(transport, settings.source_repo, settings.default_branch)
        log = read_log(Home(transport, settings.home_repo, settings.source_repo), settings)
        count = pending_work(settings, prompts, source, log, env, now_utc(), numbers_from(env.get("REVIEWER_NUMBERS")))
    out(f"Proposals with something to do: {count}.")
    if env.get("GITHUB_OUTPUT"):  # how one job tells the next whether to run
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"work={'true' if count else 'false'}\n")
    return 0


def rebuild_scoreboard(args, env, out=print) -> int:
    settings = settings_module.load(args.settings)
    if not caps.enabled(env):
        out(SWITCH_OFF_NO_WRITE)
        return 0
    prompts = Prompts.load(Path(args.settings) / "prompts")
    transport = HttpTransport(env.get("GITHUB_TOKEN"))
    source = Source(transport, settings.source_repo, settings.default_branch)
    home = Home(transport, settings.home_repo, settings.source_repo)
    found = publish_scoreboard(settings, prompts, source, home, now_utc(), pause=lambda: time.sleep(1.5))
    Path(args.out).write_text(scoreboard.as_json(found), encoding="utf-8")
    out(f"Scoreboard rebuilt for policy version {found['policy_version'][:12]}. Reviewed: {found['coverage']['reviewed']}.")
    return 0


def main(argv=None, env=None, out=print) -> int:
    parser = argparse.ArgumentParser(description="The automated reviewer's jobs.")
    parser.add_argument("--settings", default=str(SETTINGS_FOLDER), help=argparse.SUPPRESS)
    jobs = parser.add_subparsers(dest="job", required=True)
    one = jobs.add_parser("review")
    one.add_argument("--out", required=True)
    one.add_argument("--dry-run", action="store_true")
    two = jobs.add_parser("publish")
    two.add_argument("--in", required=True)
    three = jobs.add_parser("scoreboard")
    three.add_argument("--out", required=True)
    jobs.add_parser("pending")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    try:
        return {"review": review, "publish": publish_results, "scoreboard": rebuild_scoreboard, "pending": pending}[args.job](args, env, out)
    except ProviderError as error:  # its message is the provider's name, a status and a short code, and nothing else
        if error.repeated:
            out(f"Stopped: the same model turned down two proposals in one run ({error}). That points to the set-up and not to a proposal, so it needs a person.")
        else:
            out(f"Stopped: a provider turned the request down ({error}). This needs a person: a key, a permission, credit or the model's name.")
        return 1
    except Exception as error:  # the type of the fault is safe to print; its text may not be
        out(f"Stopped: {type(error).__name__}.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
