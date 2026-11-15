"""The workflow file and the dependency lock: the safety properties the spec asks for, checked from the files themselves."""
import re
from pathlib import Path

import yaml

from reviewer.config import load

from .test_config import SHIPPED

ROOT = SHIPPED.parent
SWITCHED_ON = {"GEMINI_API_KEY", "OPENAI_API_KEY"}  # the providers whose key line is live in the workflow. Each is tested on one proposal first.
TEXT = (ROOT / ".github" / "workflows" / "automated-reviewer.yml").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(TEXT)
TRIGGERS = WORKFLOW.get("on", WORKFLOW.get(True))  # YAML reads a bare "on" as true
JOBS = WORKFLOW["jobs"]
GUARD = f"github.repository == '{load(SHIPPED).home_repo}'"


def steps(job):
    return JOBS[job]["steps"]


def test_nothing_an_outsider_does_can_start_a_run():
    assert set(TRIGGERS) <= {"workflow_dispatch", "schedule"}
    for never in ("issues", "issue_comment", "pull_request", "pull_request_target", "push", "workflow_run"):
        assert never not in TRIGGERS


def test_no_permission_unless_a_job_asks_and_each_job_asks_for_the_least():
    assert WORKFLOW["permissions"] == {}
    assert set(JOBS) == {"find", "review", "publish"}
    # Reading the log needs "issues: read" while the home repository is private. Neither of these two jobs can write anywhere.
    assert JOBS["find"]["permissions"] == {"contents": "read", "issues": "read"} and "environment" not in JOBS["find"]  # the quick look holds no key
    assert JOBS["review"]["permissions"] == {"contents": "read", "issues": "read"}  # the job that can call models can write nowhere
    assert JOBS["publish"]["permissions"] == {"contents": "read", "issues": "write"}


def test_only_the_review_job_uses_the_environment_and_only_its_review_step_may_hold_a_key():
    assert JOBS["review"]["environment"] == "automated-reviewer" and "environment" not in JOBS["publish"]
    for job in JOBS:
        for step in steps(job):
            holds_a_secret = "secrets." in yaml.safe_dump(step)
            assert not holds_a_secret or (job == "review" and step.get("name") == "Review")
    assert "secrets." not in yaml.safe_dump(JOBS["publish"])
    assert "env" not in WORKFLOW and "env" not in JOBS["review"] and "env" not in JOBS["publish"]  # nothing is set for a whole job


def test_forks_never_run_it_and_runs_never_overlap_or_run_for_ever():
    assert all(GUARD in JOBS[job]["if"] for job in JOBS)
    assert WORKFLOW["concurrency"] == {"group": "automated-reviewer", "cancel-in-progress": False}
    assert all(isinstance(JOBS[job]["timeout-minutes"], int) for job in JOBS)


def test_the_publish_job_runs_even_when_the_review_failed_and_the_results_are_kept_either_way():
    assert JOBS["publish"]["needs"] == ["find", "review"] and "always()" in JOBS["publish"]["if"]
    assert JOBS["review"]["needs"] == "find" and "(needs.find.outputs.work == 'true' || github.event_name == 'workflow_dispatch')" in JOBS["review"]["if"]  # an empty hour on the clock asks no model
    assert "inputs.mode != 'scoreboard'" in JOBS["review"]["if"]
    look = next(step for step in steps("find") if step.get("id") == "look")
    assert set(look["env"]) == {"GITHUB_TOKEN", "AUTOMATED_REVIEWER_ENABLED", "REVIEWER_NUMBERS"}  # a run that names a proposal always has work
    write = next(step for step in steps("publish") if step.get("name") == "Write the log and rebuild the scoreboard")
    assert set(write["env"]) == {"GITHUB_TOKEN", "AUTOMATED_REVIEWER_ENABLED"}  # the kill switch reaches the job that writes
    assert "publish --in results.json || status=1" in write["run"] and "scoreboard --out scoreboard.json || status=1" in write["run"] and write["run"].rstrip().endswith("exit $status")
    assert JOBS["find"]["outputs"] == {"work": "${{ steps.look.outputs.work }}"}
    keep = next(step for step in steps("review") if "upload-artifact" in step.get("uses", ""))
    assert keep["if"] == "always()" and keep["with"]["path"] == "results.json"


def test_every_action_is_an_official_one_pinned_to_a_commit_and_checkout_keeps_no_credentials():
    used = [step["uses"] for job in JOBS for step in steps(job) if "uses" in step]
    assert len(used) == 9 and all(re.fullmatch(r"actions/(checkout|setup-python|upload-artifact|download-artifact)@[0-9a-f]{40}", use) for use in used)
    checkouts = [step for job in JOBS for step in steps(job) if step.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 3 and all(step["with"]["persist-credentials"] is False for step in checkouts)


def test_no_command_contains_a_value_from_outside():
    commands = [step["run"] for job in JOBS for step in steps(job) if "run" in step]
    assert len(commands) == 6 and all("${{" not in command for command in commands)
    review = next(step for step in steps("review") if step.get("name") == "Review")
    assert set(review["env"]) == {"GITHUB_TOKEN", "AUTOMATED_REVIEWER_ENABLED", "REVIEWER_NUMBERS", "REVIEWER_OVERRIDE_CAPS", "REVIEWER_MODE"} | SWITCHED_ON
    assert all(review["env"][key] == "${{ secrets." + key + " }}" for key in SWITCHED_ON)  # each key under its own name, from the environment's secrets
    assert TRIGGERS["workflow_dispatch"]["inputs"]["mode"]["options"] == ["reconcile", "fixtures", "scoreboard"]
    assert review["env"]["AUTOMATED_REVIEWER_ENABLED"] == "${{ vars.AUTOMATED_REVIEWER_ENABLED }}" and review["env"]["REVIEWER_NUMBERS"] == "${{ inputs.numbers }}"
    assert review["run"] == "python scripts/automated_reviewer.py review --out results.json"  # a real run: the providers switched on are asked


def test_dependencies_are_installed_from_a_lock_file_with_hashes():
    installs = [step["run"] for job in JOBS for step in steps(job) if "pip install" in step.get("run", "")]
    assert len(installs) == 3 and all("--require-hashes -r settings/requirements.lock" in run for run in installs)
    lock = (SHIPPED / "requirements.lock").read_text(encoding="utf-8")
    requirements = [line for line in lock.split("\n") if line and not line.startswith(("#", " "))]
    assert requirements == ["pyyaml==6.0.3 \\"]
    hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", lock)
    assert len(hashes) > 20 and len(set(hashes)) == len(hashes)


def test_every_line_that_names_a_secret_is_inside_the_review_step_even_while_it_is_commented_out():
    start, end = TEXT.index("      - name: Review\n"), TEXT.index("      - name: Keep the results, whatever happened")
    named = [found.start() for found in re.finditer(r"secrets\.", TEXT)]
    assert len(named) == 3 and all(start < at < end for at in named)
    from reviewer.providers import KEYS

    live = {line.strip().split(":")[0] for line in TEXT.split("\n") if "secrets." in line and not line.lstrip().startswith("#")}
    waiting = {line.strip().lstrip("# ").split(":")[0] for line in TEXT.split("\n") if "secrets." in line and line.lstrip().startswith("#")}
    assert live == SWITCHED_ON and live | waiting == set(KEYS.values()) and not live & waiting


def test_the_policy_version_covers_every_file_that_shapes_a_request_or_the_reading_of_an_answer():
    from reviewer import identity

    not_input = {"__init__.py", "actions.py", "caps.py", "config.py", "github.py", "identity.py", "providers.py", "record.py", "run.py", "scoreboard.py"}
    present = {path.name for path in (ROOT / "reviewer").glob("*.py")}
    assert set(identity.INPUT_CODE) | not_input == present and not set(identity.INPUT_CODE) & not_input  # a new file must be put on one list or the other
    assert {"anthropic_adapter.py", "google_adapter.py", "openai_adapter.py", "fixtures.py", "decisions.py", "review.py", "http.py", "models.py"} <= set(identity.INPUT_CODE)
