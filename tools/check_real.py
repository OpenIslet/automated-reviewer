#!/usr/bin/env python3
"""Runs the code over the real proposals and prints their shape only, never their text.

    python3 tools/check_real.py              # the input side, from the local copies alone
    python3 tools/check_real.py context      # also the repository context for each one (reads GitHub anonymously, cached)
"""
import sys

from real import CachedTransport, config, saved_issues, source

from reviewer import excerpts
from reviewer.checks import other_checks
from reviewer.context import ContextReader, ContextUnavailable
from reviewer.decisions import standing_of
from reviewer.evidence import EvidenceReader
from reviewer.integrity import confirm
from reviewer.issuebody import Unparsed, parse_body

SETTINGS = config()
RULES = SETTINGS.rules()
WITH_CONTEXT = "context" in sys.argv[1:]
transport = CachedTransport()
reader = ContextReader(source(transport), SETTINGS.context_files)
evidence = EvidenceReader(source(transport), SETTINGS.evidence_places, SETTINGS.corpus_list)

for issue, events, comments in saved_issues():
    standing = standing_of(issue, events, RULES)
    above, marker, how = confirm(issue.get("body") or "")
    parsed = None
    try:
        parsed = parse_body(above)
        body = (
            f"{parsed.shape} | draft for models {len(parsed.draft_for_models)} chars | AI section removed {'yes' if parsed.draft_ai_tools_section else 'NO'}"
            f" | verdict {parsed.review_verdict} | claim {'yes' if parsed.claim else 'NO'} | wet-lab {'yes' if parsed.wetlab_for_models else 'no'}"
            f" | notes {parsed.notes}"
        )
    except Unparsed as refused:
        body = f"UNPARSED: {refused}"
    print(f"#{issue['number']}: {standing.status}{'/' + standing.reason if standing.reason else ''} {standing.label or ''} | text {how} | {body}")
    if standing.status != "not_a_proposal":
        seen = other_checks(issue, comments, SETTINGS.checks_comment, SETTINGS.submission_bot)
        leaks = [w for w in ("verdict is", "Supported", "supported", "Submit to OpenIslet", "AI tool") if seen.text and w in seen.text]
        print(f"      bot checks: {seen.why} | rows kept {seen.rows_kept}, left out {seen.rows_left_out} | {len(seen.text or '')} chars | words that must not be there: {leaks}")
    if WITH_CONTEXT and standing.status != "not_a_proposal":
        try:
            context = reader.context_for(issue["created_at"])
            files = ", ".join(f"{f.name}={f.path.rsplit('/', 1)[-1]} ({len(f.text)} chars)" for f in context.files)
            print(f"      submitted {issue['created_at']} | context commit {context.commit[:7]} of {context.commit_date} | {files} | {len(context.paths)} files there | notes {context.notes}")
            if parsed is not None:
                cut = excerpts.build(parsed.draft_for_models, context, reader, SETTINGS.excerpt_lines_either_side, SETTINGS.excerpt_lines_when_no_line_is_given, SETTINGS.excerpt_max_tokens)
                print(f"      cited files: sent {cut.included} | in full already {cut.already_in_full} | by name only {cut.by_name_only} | missing {cut.missing} | about {cut.tokens} tokens")
                found = evidence.lookup(marker.pmids if marker else (), parsed.draft_for_models, context)
                kinds = {}
                for study, extracted, in_corpus in found.rows:
                    tally = kinds.setdefault(study.kind, {"named": 0, "extracted": 0, "in corpus": 0})
                    tally["named"] += 1
                    tally["extracted"] += 1 if extracted else 0
                    tally["in corpus"] += 1 if in_corpus else 0
                print(f"      studies: place used {found.source} | corpus list there {found.corpus_available} | {kinds} | block {len(found.text)} chars")
        except ContextUnavailable as unavailable:
            print(f"      submitted {issue['created_at']} | CONTEXT UNAVAILABLE: {unavailable}")

if WITH_CONTEXT:
    print(f"requests sent to GitHub this time: {transport.asked}")


def prepared_inputs():
    """The whole path to the two model inputs, for every real proposal. Sizes, counts and flags only."""
    from reviewer import identity
    from reviewer.assemble import Prompts
    from reviewer.pipeline import Excluded, Readers, prepare
    from real import ROOT

    class NeverEdited(type(source())):
        # Anonymous reads cannot ask for the edit history. tools/check_edit_history.py (run where gh is signed in) showed on
        # 14 Nov 2026 that none of issues 1 to 15 has ever been edited, so that is what this stand-in answers.
        def edit_history(self, number):
            return {"edited": False}

    prompts = Prompts.load(ROOT / "settings" / "prompts")
    readers = Readers(NeverEdited(transport, SETTINGS.source_repo, SETTINGS.default_branch), SETTINGS)
    rows = list(saved_issues())
    everything = [(issue, events) for issue, events, _ in rows]
    print(f"policy version {identity.policy_version(SETTINGS, prompts)[:12]}")
    for issue, events, comments in rows:
        for named in (False, True):
            got = prepare(issue, events, comments, everything, readers, SETTINGS, prompts, named_in_manual_run=named)
            if got is None:
                print(f"#{issue['number']}: never touched")
                break
            if isinstance(got, Excluded):
                if not named:
                    print(f"#{issue['number']}: excluded, {got.reason}", end="")
                    if got.reason != "self_decided":
                        print()
                        break
                    print("; if named in a manual run:", end=" ")
                continue
            parts = ", ".join(f"{p.name.split(',')[0]}={len(p.text)}" for p in got.panel.parts)
            print(f"#{issue['number']}: text {got.submitted.how} | gate about {got.gate.tokens} tokens | panel about {got.panel.tokens} tokens | index holds {sorted(got.index.numbers)} | flags {got.flags}")
            print(f"      panel parts (characters): {parts}")
            break


if "inputs" in sys.argv[1:]:
    prepared_inputs()
