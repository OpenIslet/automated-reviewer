"""The policy version: one hash that changes whenever anything that shapes a model's input or answer changes.

It covers the outcome definitions, the prompts, the answer schemas, the list of context files and the other settings
that decide what is sent, and the code that builds the input, sends each request and reads each answer. Any change
starts a new prospective count
(docs/SPEC.md, sections 3 and 9). It does not cover the models, the caps or who may decide: those are recorded
separately, the models in the roster.

Computed, never typed by hand.
"""
import hashlib
import json
from pathlib import Path

from . import verdicts

INPUT_CODE = (
    "anthropic_adapter.py",
    "assemble.py",
    "checks.py",
    "context.py",
    "decisions.py",
    "evidence.py",
    "excerpts.py",
    "fixtures.py",
    "google_adapter.py",
    "http.py",
    "index.py",
    "integrity.py",
    "issuebody.py",
    "models.py",
    "openai_adapter.py",
    "pipeline.py",
    "review.py",
    "submitted.py",
    "tokens.py",
    "verdicts.py",
)


def policy_parts(settings, prompts) -> dict:
    here = Path(__file__).resolve().parent
    return {
        "outcomes": [[o.key, o.label, o.name, o.meaning] for o in settings.outcomes],
        "administrative_closure_label": settings.administrative_closure_label,
        "prompts": {"panel": prompts.panel, "gate": prompts.gate, "gate_question": prompts.gate_question, "gate_answer": prompts.gate_answer},
        "schemas": {"panel": verdicts.panel_schema(settings), "gate": verdicts.GATE_SCHEMA},
        "context_files": [list(pair) for pair in settings.context_files],
        "excerpts": [settings.excerpt_lines_either_side, settings.excerpt_lines_when_no_line_is_given, settings.excerpt_max_tokens],
        "evidence_places": [[p.name, p.file, p.per_study_folder] for p in settings.evidence_places],
        "corpus_list": settings.corpus_list,
        "checks_comment": [settings.checks_comment.starts_with, settings.checks_comment.left_out_heading, settings.checks_comment.included_heading, settings.checks_comment.within_minutes, list(settings.checks_comment.included_rows)],
        "answer_limits": [settings.reasons_kept, settings.reason_max_characters],
        "gate_threshold": settings.gate_threshold,
        "code": {name: hashlib.sha256((here / name).read_bytes()).hexdigest() for name in INPUT_CODE},
    }


def policy_version(settings, prompts) -> str:
    canonical = json.dumps(policy_parts(settings, prompts), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def roster(settings) -> list:
    """What was asked of each model slot. The version each provider reports back is added to each result."""
    return [
        {"slot": s.slot, "stage": s.stage, "provider": s.provider, "model": s.model, "kind": s.kind, "effort": s.effort, "max_output_tokens": s.max_output_tokens, "threshold": s.threshold}
        for s in settings.slots
    ]
