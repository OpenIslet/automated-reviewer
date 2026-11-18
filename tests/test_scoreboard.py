"""The scoreboard, against a made-up log whose right answers are known."""
import dataclasses
import json
import re

from reviewer import identity, record as records, scoreboard
from reviewer.github import Home
from reviewer.run import publish_scoreboard, standing_now

from .builders import issue, issue_text, with_marker
from .fakes import HOME, RECORD_AUTHOR, SOURCE, FakeTransport
from .test_run import BOARD, LOG, READY, Trial
from .world import DECIDER, PROMPTS, World, decided, proposal

POLICY = identity.policy_version(READY, PROMPTS)
OLD_POLICY = "a" * 64
LABELS = {o.key: o.label for o in READY.outcomes}
MODELS = {"gate": READY.gate, "panel": READY.panel}


def case(number, truth, panel, gate=(False, False, False), confidence=(0.9, 0.8, 0.6), covered_by=(None, None, None), same_as=None, second=None, flags=(), self_decided=False,
         submitted_at="2026-11-05T00:00:00Z", blind="Partly supported", ai_draft="ExampleModel 9", policy=POLICY, dry_run=False):
    """One reviewed proposal as it would sit in the log. `panel` holds an outcome key for each model, or None for no verdict."""
    raw = records.new_record("proposal", number, policy, "2026-11-20T06:17:00Z", dry_run)
    raw.update(submitted_at=submitted_at, first_reviewed_at="2026-11-20T06:17:00Z", self_decided=self_decided)
    raw["decision"] = {"label": LABELS[truth], "by": DECIDER, "at": "2026-11-10T00:00:00Z", "same_as": same_as, "same_as_how": "same_as_line" if same_as else None, "same_as_decided_then": bool(same_as) or None}
    raw["text"] = {"sha256": "b" * 64, "confirmed": "hash_match", "flags": list(flags)}
    raw["contributor"] = {"ai_draft": ai_draft, "ai_review": "OtherModel 3", "blind_review_verdict": blind}
    call = {"at": "2026-11-20T06:17:00Z", "status": "ok", "input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 100, "latency_ms": 5}
    for i, slot in enumerate(MODELS["gate"]):
        raw["gate"]["slots"].append({"slot": slot.slot, "provider": slot.provider, "model": slot.model, "kind": slot.kind, "effort": slot.effort, "status": "verdict", "verdict": {"spam": gate[i], "probability": 0.9 if gate[i] else 0.1, "reason": None, "flags": []}, "runs": 0, "calls": [dict(call)]})
    for i, slot in enumerate(MODELS["panel"]):
        entry = {"slot": slot.slot, "provider": slot.provider, "model": slot.model, "kind": "panel", "effort": slot.effort, "runs": 0, "calls": [dict(call)]}
        if panel[i] is None:
            entry.update(status="no_verdict", reason="refused")
        else:
            entry.update(status="verdict", verdict={"outcome": panel[i], "confidence": confidence[i], "covered_by": covered_by[i], "reasons": ["A reason."], "flags": []})
            if second is not None:
                entry["second"] = {"status": "verdict", "outcome": second[i]}
                entry["calls"].append(dict(call))
        raw["panel"]["slots"].append(entry)
    raw["panel"]["second_opinion"] = second is not None
    return records.clean(raw, READY)


def left_out(number, reason, truth="evidence"):
    raw = records.new_record("proposal", number, POLICY, "2026-11-20T06:17:00Z", False)
    raw["decision"] = {"label": LABELS[truth], "by": DECIDER, "at": "2026-11-10T00:00:00Z"}
    raw["exclusion"] = {"reason": reason, "detail": None}
    raw["self_decided"] = reason == "self_decided"
    return records.clean(raw, READY)


def the_log():
    e, c, q, a, s = "evidence", "changed_blueprint", "open_question", "already_covered", "spam"
    return [
        case(2, e, (e, e, e)),  # all agree, and right
        case(3, c, (e, e, e)),  # all agree, and wrong, about a change to the blueprint: a critical miss
        case(7, q, (e, q, None)),  # split, with one model silent; the second matched
        case(8, a, (a, a, a), covered_by=(2, 2, 5), same_as=2),
        case(9, s, (s, s, s), gate=(True, True, False)),  # recorded as spam, caught by the majority
        case(10, e, (e, e, e), gate=(True, True, True), second=(e, e, q), ai_draft="Claude Something"),  # a real proposal flagged as spam
        case(4, e, (e, e, e), self_decided=True, flags=("self_decided",)),
        case(11, e, (c, c, c), flags=("edited_before_decision",)),
        left_out(12, "administrative_triage"),
        left_out(13, "self_decided"),
        case(20, e, (e, e, e), dry_run=True),  # a stand-in result: never counted
        case(21, e, (e, e, e), policy=OLD_POLICY),  # an earlier policy version
    ]


NOW = {n: {"label": label} for n, label in {2: LABELS["evidence"], 3: LABELS["evidence"], 7: LABELS["open_question"], 8: LABELS["already_covered"], 9: LABELS["spam"], 10: LABELS["evidence"], 4: LABELS["evidence"], 11: LABELS["evidence"], 12: None, 13: LABELS["evidence"], 14: LABELS["later"]}.items()}


def built(log=None, settings=READY, now_labels=None):
    return scoreboard.numbers(the_log() if log is None else log, settings, POLICY, NOW if now_labels is None else now_labels, "2026-11-21T00:00:00Z")


def test_coverage_lists_every_decided_proposal_and_why_any_is_missing():
    coverage = built()["coverage"]
    assert coverage["decided_now"] == 11 and coverage["with_a_record"] == 10 and coverage["no_record_yet"] == [14]
    assert coverage["reviewed"] == 8 and coverage["complete"] == 8
    assert coverage["without_every_verdict"] == {"refused": [7]}
    assert coverage["left_out"] == {"administrative_triage": [12], "self_decided": [13]}
    assert built(now_labels={n: v for n, v in NOW.items() if n != 2})["coverage"]["no_longer_available"] == [2]


def test_before_a_freeze_everything_is_retrospective_and_the_apart_cases_stay_apart():
    found = built()
    assert found["frozen_at"] is None and found["prospective"]["cases"] == 0
    assert found["retrospective"]["proposals"] == [2, 3, 7, 8, 9, 10]
    apart = found["reported_apart"]
    assert apart["the_maintainers_own"] == {"cases": 1, "proposals": [4], "unanimous": 1, "unanimous_right": 1}
    assert apart["edited_before_the_decision"] == {"cases": 1, "proposals": [11], "unanimous": 1, "unanimous_right": 0}
    assert found["retired_versions"] == [OLD_POLICY]


def test_the_panel_counts_match_the_known_answers():
    group = built()["retrospective"]
    panel = group["panel"]
    assert (panel["unanimous"], panel["unanimous_right"], panel["split"], panel["critical_misses"]) == (5, 4, 1, [3])
    assert panel["matched_when_split"] == {"panel-2": 1}
    assert group["baseline"] == {"outcome": "evidence", "share": 0.3333} and group["outcomes_recorded"] == {"evidence": 2, "changed_blueprint": 1, "open_question": 1, "already_covered": 1, "spam": 1}
    one, two, three = (group["panel_models"][f"panel-{n}"] for n in (1, 2, 3))
    assert (one["agree"], two["agree"], three["agree"]) == (4, 5, 4) and (one["cases"], three["cases"]) == (6, 6)
    assert three["no_verdict"] == {"refused": 1} and three["agreement"] == 0.6667  # the silent case stays in the denominator
    assert one["per_outcome"]["evidence"] == {"predicted": 4, "recorded": 2, "right": 2, "precision": 0.5, "recall": 1.0}
    assert one["per_outcome"]["changed_blueprint"] == {"predicted": 0, "recorded": 1, "right": 0, "precision": None, "recall": 0.0}
    assert one["confusion"]["evidence"] == {"evidence": 2, "changed_blueprint": 1, "open_question": 1}
    assert (one["critical_misses"], two["critical_misses"]) == (1, 1)
    assert (one["covered_right"], one["covered_named"], three["covered_right"]) == (1, 1, 0)  # the third named proposal 5, not 2
    assert (one["second_asked"], one["second_same"], three["second_same"]) == (1, 1, 0)
    assert one["calibration"] == [[0, 0], [0, 0], [0, 0], [6, 4]] and three["calibration"][1] == [5, 4]
    assert (one["calls"], one["input_tokens"], one["output_tokens"]) == (7, 7000, 700)


def test_the_gate_counts_and_the_two_baselines():
    group = built()["retrospective"]
    gate = group["gate"]
    assert (gate["real_proposals"], gate["real_spam"], gate["majority_false_holds"], gate["majority_caught"], gate["no_majority"]) == (5, 1, 1, 1, 0)
    assert gate["models"]["gate-1"]["false_holds"] == 1 and gate["models"]["gate-1"]["caught"] == 1 and gate["models"]["gate-3"]["caught"] == 0
    assert group["blind_review"] == {"Partly supported": {"evidence": 2, "changed_blueprint": 1, "open_question": 1, "already_covered": 1, "spam": 1}}


def test_the_same_lab_split_goes_by_the_drafting_ai_the_contributor_named():
    models = built()["retrospective"]["panel_models"]
    assert models["panel-3"]["same_lab"] == [1, 1] and models["panel-3"]["other_lab"] == [0, 0]  # the Anthropic slot, on the proposal drafted with Claude
    assert models["panel-1"]["same_lab"] == [0, 0] and models["panel-1"]["other_lab"] == [1, 1]
    assert scoreboard.lab_of("GPT-something") == "openai" and scoreboard.lab_of("ExampleModel 9") is None and scoreboard.lab_of("Claude and Gemini") is None


def test_a_decision_changed_after_the_review_is_listed_with_its_direction():
    assert built()["reversals"] == [{"proposal": 3, "was": "changed_blueprint", "now": "evidence", "direction": "towards the panel"}]
    away = {**NOW, 2: {"label": LABELS["later"]}}
    assert {"proposal": 2, "was": "evidence", "now": "later", "direction": "away from the panel"} in built(now_labels=away)["reversals"]


def frozen(at="2026-11-15T00:00:00Z"):
    return dataclasses.replace(READY, freeze_policy_version=POLICY, freeze_at=at)


def test_only_cases_submitted_after_the_freeze_count_towards_graduation():
    n, c = "not_taken_up", "changed_blueprint"
    log = the_log() + [case(30 + i, n, (n, n, n), submitted_at=f"2026-11-16T00:00:{i:02d}Z") for i in range(3)] + [case(40, c, (c, c, c), submitted_at="2026-11-16T01:00:00Z")]
    labels = {**NOW, **{number: {"label": LABELS[n]} for number in (30, 31, 32)}, 40: {"label": LABELS[c]}}
    found = built(log, frozen(), labels)
    assert found["frozen_at"] == "2026-11-15T00:00:00Z" and found["prospective"]["proposals"] == [30, 31, 32, 40] and found["retrospective"]["proposals"] == [2, 3, 7, 8, 9, 10]
    assert found["graduation"]["not_taken_up"] == {"unanimous_cases": 3, "needed": 50, "differed": [], "disagreements_allowed": 1, "critical_misses": [], "state": "counting", "fixtures_that_steered_the_panel_to_it": 0}
    assert found["graduation"]["changed_blueprint"]["unanimous_cases"] == 1 and found["graduation"]["changed_blueprint"]["state"] == "counting"  # no outcome is barred
    assert found["graduation"]["evidence"]["unanimous_cases"] == 0  # the retrospective cases are not counted
    other_version = dataclasses.replace(READY, freeze_policy_version=OLD_POLICY, freeze_at="2026-11-15T00:00:00Z")
    assert built(log, other_version, labels)["prospective"]["cases"] == 0  # a freeze of another version freezes nothing here


def test_the_count_is_judged_on_the_first_fifty_and_a_later_reversal_counts_against_it():
    n, e = "not_taken_up", "evidence"
    fifty = [case(100 + i, n, (n, n, n), submitted_at=f"2026-11-16T00:{i:02d}:00Z") for i in range(55)]
    labels = {100 + i: {"label": LABELS[n]} for i in range(55)}
    assert built(fifty, frozen(), labels)["graduation"]["not_taken_up"] == {"unanimous_cases": 50, "needed": 50, "differed": [], "disagreements_allowed": 1, "critical_misses": [], "state": "passed the count", "fixtures_that_steered_the_panel_to_it": 0}
    labels[103] = {"label": LABELS[e]}  # one decision changed later: allowed
    assert built(fifty, frozen(), labels)["graduation"]["not_taken_up"]["differed"] == [103]
    assert built(fifty, frozen(), labels)["graduation"]["not_taken_up"]["state"] == "passed the count"
    labels[104] = {"label": LABELS[e]}  # a second: not allowed
    assert built(fifty, frozen(), labels)["graduation"]["not_taken_up"]["state"] == "did not pass"
    labels[154] = {"label": LABELS[e]}  # the fifty-fifth is outside the sample
    assert built(fifty, frozen(), labels)["graduation"]["not_taken_up"]["differed"] == [103, 104]
    miss = fifty[:49] + [case(160, "changed_blueprint", (n, n, n), submitted_at="2026-11-16T00:49:30Z")]
    assert built(miss, frozen(), {**{100 + i: {"label": LABELS[n]} for i in range(49)}, 160: {"label": LABELS["changed_blueprint"]}})["graduation"]["not_taken_up"]["state"] == "did not pass"


def test_the_page_is_built_from_counts_and_names_proposals_only_in_code_formatting():
    text = scoreboard.render(built(), READY)
    assert text.startswith("**Automated reviewer scoreboard, shadow mode. Nothing here is a decision")
    assert "not frozen yet, so every result below is retrospective" in text
    assert "All three panel models agreed in 5 of 6. When they agreed, the maintainer had recorded the same outcome in 4 of 5." in text
    assert "| `gemini-4-neon` | 4 of 6 (67%) | 0 | 1 of 1 | 7 | 7000 | 700 |" in text
    assert "| `claude-sonnet-5-5` | 4 of 6 (67%) | 1 (refused 1) |" in text
    assert "- `#3`: Changed the blueprint then, Added evidence now (towards the panel)." in text
    assert "| Changed the blueprint | 0 of 50 | 0 | 0 | counting |" in text
    assert "- Left out, the maintainer's own proposals: `#13`." in text and "- Without every verdict, refused: `#7`." in text
    assert not re.search(r"(?<!`)#\d", text) and "ExampleModel" not in text and "Claude Something" not in text
    assert not re.search(r"[$€£¥₹]|\b(USD|dollars?|cents?|price|budget)\b|\bcosts?\b", text + scoreboard.as_json(built()), re.IGNORECASE)
    assert json.loads(scoreboard.as_json(built()))["retrospective"]["panel"]["unanimous"] == 5


def test_the_page_stays_under_its_limit_however_long_the_lists_get():
    n = "not_taken_up"
    many = [case(1000 + i, n, (n, None, None) if i % 2 else (n, n, n)) for i in range(1500)]
    labels = {1000 + i: {"label": LABELS[n]} for i in range(4000)}
    text = scoreboard.render(built(many, READY, labels), READY)
    assert len(text) < scoreboard.BODY_LIMIT and " more" in text
    assert scoreboard.render(built(), READY, full=False).count("outcome by outcome") == 0 < scoreboard.render(built(), READY).count("outcome by outcome")


def test_the_scoreboard_job_writes_the_body_and_one_comment_per_retired_version_once():
    trial = Trial()
    trial.run()
    log = [case(21, "evidence", ("evidence",) * 3, policy=OLD_POLICY), case(22, "evidence", ("later",) * 3, policy=OLD_POLICY)]
    for old in log:
        trial.fake.add_home_comment(LOG, records.render(old, READY))
    trial.fake.add_home_comment(BOARD, scoreboard.RETIRED_MARK.format(policy="c" * 64) + " forged", author={"login": "a-passer-by", "type": "User"})
    writes = len(trial.fake.writes())
    found = publish_scoreboard(READY, PROMPTS, trial.world.source(), trial.home, "2026-11-21T00:00:00Z")
    assert found["coverage"]["reviewed"] == 2 and found["retired_versions"] == [OLD_POLICY]
    assert trial.home.issue(BOARD)["body"].startswith("**Automated reviewer scoreboard, shadow mode.") and "Decided proposals today: 2." in trial.home.issue(BOARD)["body"]
    retired = [c for c in trial.home.comments(BOARD) if c["user"] == RECORD_AUTHOR]
    assert len(retired) == 1 and retired[0]["body"].startswith(scoreboard.RETIRED_MARK.format(policy=OLD_POLICY)) and "Retrospective: 2 cases. All three panel models agreed in 2, and matched the maintainer in 1 of those." in retired[0]["body"]
    publish_scoreboard(READY, PROMPTS, trial.world.source(), trial.home, "2026-11-21T06:00:00Z")
    assert len([c for c in trial.home.comments(BOARD) if c["user"] == RECORD_AUTHOR]) == 1  # written once, not again
    new_writes = trial.fake.writes()[writes:]
    assert len(new_writes) == 3 and all(f"/repos/{HOME}/" in url for _, url in new_writes) and len(trial.asked()) == 15  # the scoreboard asked no model anything


def test_the_decisions_standing_today_are_read_from_label_events():
    world = World()
    world.add(proposal(12, "2026-11-12T00:00:00Z"), [])  # undecided
    world.add(proposal(13, "2026-11-12T01:00:00Z"), [decided("outcome:later", "2026-11-13T00:00:00Z"), {"event": "unlabeled", "label": {"name": "outcome:later"}, "actor": {"login": DECIDER}, "created_at": "2026-11-14T00:00:00Z"}, decided("outcome:evidence", "2026-11-14T00:00:01Z")])
    world.add(proposal(14, "2026-11-12T02:00:00Z"), [decided("status:closed-triage", "2026-11-13T00:00:00Z")])
    by_hand = issue(number=15, author="a-passer-by", body=with_marker(issue_text()), created_at="2026-11-12T03:00:00Z")  # not a proposal, though it is labelled
    world.add(by_hand, [decided("outcome:evidence", "2026-11-13T00:00:00Z")])
    assert standing_now(world.source(), READY) == {2: {"label": "outcome:evidence"}, 10: {"label": "outcome:already-covered"}, 13: {"label": "outcome:evidence"}, 14: {"label": None}}


def test_decisions_made_with_the_verdict_in_view_are_counted_apart_from_the_rest():
    e = "evidence"
    in_view = case(30, e, (e, e, e))
    in_view.update(posted_at="2026-11-09T00:00:00Z", reviewed_before_decision=True)  # posted the day before the decision
    found = built([in_view, case(31, e, ("later",) * 3), case(32, e, (e, e, e))], now_labels={})
    timing = found["retrospective"]["by_timing"]
    assert timing["after_the_verdict"] == {"cases": 1, "proposals": [30], "unanimous": 1, "unanimous_right": 1}
    assert timing["after_part_of_the_verdict"] == {"cases": 0, "proposals": [], "unanimous": 0, "unanimous_right": 0}
    assert timing["before_any_verdict"] == {"cases": 2, "proposals": [31, 32], "unanimous": 2, "unanimous_right": 1}
    assert found["retrospective"]["decided_after_the_verdict"] == 1
    page = scoreboard.render(found, READY)
    assert ("- Counted apart. Decided with the whole verdict already posted: cases 1, panel in agreement 1, same outcome as the maintainer 1. "
            "Decided with part of it posted: cases 0, panel in agreement 0, same outcome as the maintainer 0. "
            "Decided before any of it was posted: cases 2, panel in agreement 2, same outcome as the maintainer 1.") in page


def test_a_reviewed_proposal_that_is_still_undecided_is_not_listed_as_having_lost_its_decision():
    world = World()
    world.add(proposal(12, "2026-11-18T00:00:00Z"), [])
    trial = Trial(world=world)
    trial.run()
    found = publish_scoreboard(READY, PROMPTS, trial.world.source(), trial.home, "2026-11-20T07:00:00Z")
    assert found["coverage"]["no_longer_available"] == [] and "carries no decision now" not in trial.home.issue(BOARD)["body"]
    assert [waiting["proposal"] for waiting in found["coverage"]["waiting_for_a_decision"]] == [12]


def test_a_model_replaced_part_way_is_shown_beside_the_one_it_replaced():
    e = "evidence"
    newer = case(33, e, (e, e, e))
    newer["panel"]["slots"][1]["model"] = "gpt-7-made-up"
    page = scoreboard.render(built([case(32, e, (e, e, e)), newer], now_labels={}), READY)
    assert "| `gpt-6.2-sol` + `gpt-7-made-up` | 2 of 2 (100%) |" in page and "was replaced part-way and the row counts both together" in page
    assert "replaced part-way" not in scoreboard.render(built(), READY)


# ---- the stricter test for the two outcomes where a wrong decision does the most harm --------------------------------------

def strict_cases(key, count, start=200, in_view=True):
    """Prospective cases the panel was unanimous for, each matched by the maintainer. `in_view`: he had read the verdict."""
    minute = lambda i: start - 200 + i  # noqa: E731  a later batch is later in time, so it falls outside the first fifty
    cases = [case(start + i, key, (key, key, key), submitted_at=f"2026-11-16T{minute(i) // 60:02d}:{minute(i) % 60:02d}:00Z") for i in range(count)]
    for one in cases:
        if in_view:  # posted the day before the decision
            one.update(posted_at="2026-11-09T00:00:00Z", first_verdict_posted_at="2026-11-09T00:00:00Z", reviewed_before_decision=True)
    return cases


def graduation(cases, key, changed=None):
    labels = {c["proposal"]: {"label": LABELS[key]} for c in cases}
    labels.update(changed or {})
    return built(cases, frozen(), labels)["graduation"][key]


def test_the_two_strict_outcomes_wait_for_cases_decided_before_any_verdict_was_posted():
    from reviewer import actions

    assert scoreboard.STRICT_OUTCOMES == ("changed_blueprint", "dead_end") and set(scoreboard.STRICT_OUTCOMES) <= set(READY.outcome_keys)
    assert [actions.most_it_could_ever_be_given(key) for key in ("changed_blueprint", "dead_end", "evidence", "spam")] == ["pass_up", "pass_up", "decide", "decide"]
    for key in scoreboard.STRICT_OUTCOMES:
        fifty = strict_cases(key, 50)
        row = graduation(fifty, key)
        assert (row["state"], row["blind_cases"], row["blind_needed"], row["disagreements_allowed"], row["passing_allows"]) == ("waiting for blind cases", 0, 10, 0, "pass_up")
        nine_blind = fifty + strict_cases(key, 9, start=300, in_view=False)
        assert graduation(nine_blind, key)["state"] == "waiting for blind cases" and graduation(nine_blind, key)["blind_cases"] == 9
        ten_blind = fifty + strict_cases(key, 10, start=300, in_view=False)  # they need not be among the first fifty
        assert (graduation(ten_blind, key)["state"], graduation(ten_blind, key)["blind_cases"]) == ("passed the count", 10)
    # An ordinary outcome needs no blind cases.
    assert graduation(strict_cases("evidence", 50), "evidence")["state"] == "passed the count" and "blind_cases" not in graduation(strict_cases("evidence", 50), "evidence")


def test_the_two_strict_outcomes_allow_no_disagreement_at_all():
    for key in scoreboard.STRICT_OUTCOMES:
        cases = strict_cases(key, 50) + strict_cases(key, 10, start=300, in_view=False)
        one_changed = graduation(cases, key, {203: {"label": LABELS["evidence"]}})  # one decision in fifty changed later
        assert one_changed["differed"] == [203] and one_changed["state"] == "did not pass"
        blind_one_changed = graduation(cases, key, {305: {"label": LABELS["evidence"]}})  # a blind case outside the fifty
        assert blind_one_changed["differed"] == [] and blind_one_changed["blind_differed"] == [305] and blind_one_changed["state"] == "did not pass"
    ordinary = strict_cases("evidence", 50)
    assert graduation(ordinary, "evidence", {203: {"label": LABELS["later"]}})["state"] == "passed the count"  # one is allowed for the others


def test_a_miss_on_either_side_of_ruling_out_a_dead_end_is_critical_as_it_is_for_a_change_to_the_blueprint():
    d, e, c = "dead_end", "evidence", "changed_blueprint"
    log = [case(60, d, (e, e, e)), case(61, e, (d, d, d)), case(62, c, (d, d, d)), case(63, e, (e, e, e))]
    found = built(log, now_labels={})
    assert found["retrospective"]["panel"]["critical_misses"] == [60, 61, 62]
    assert found["retrospective"]["panel_models"]["panel-1"]["critical_misses"] == 3
    page = scoreboard.render(found, READY)
    assert '- Critical misses (the panel and the maintainer on opposite sides of "Changed the blueprint" or "Ruled out a dead end"): `#60`, `#61`, `#62`.' in page
    # In the count for an ordinary outcome, a case the maintainer recorded as a dead end is a critical miss too.
    forty_nine = strict_cases(e, 49)
    missed = forty_nine + [case(290, d, (e, e, e), submitted_at="2026-11-17T00:00:00Z")]
    labels = {**{one["proposal"]: {"label": LABELS[e]} for one in forty_nine}, 290: {"label": LABELS[d]}}
    row = built(missed, frozen(), labels)["graduation"][e]
    assert row["critical_misses"] == [290] and row["state"] == "did not pass"  # one disagreement would be allowed; a critical miss is not


def test_the_page_says_which_outcomes_are_strict_and_how_many_blind_cases_each_has():
    cases = strict_cases("changed_blueprint", 3, start=300, in_view=False) + strict_cases("dead_end", 1, start=400)
    labels = {**{300 + i: {"label": LABELS["changed_blueprint"]} for i in range(3)}, 400: {"label": LABELS["dead_end"]}}
    page = scoreboard.render(built(cases, frozen(), labels), READY)
    assert ('Two outcomes are held to a stricter test, because a wrong decision on either does the most harm: "Changed the blueprint" and "Ruled out a dead end". The second adds to the Idea Graveyard. '
            "For these, no disagreement is allowed in the 50, and at least 10 cases must have been decided before any of the verdict was posted, with no disagreement among them "
            "(so far: Changed the blueprint 3, Ruled out a dead end 0). Passing lets the panel pass a proposal up to the third layer of review. The panel never decides these alone.") in page
