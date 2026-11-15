"""The Claude adapter, against a stand-in client. It has not been run against the real API: no key exists yet."""
import json
from types import SimpleNamespace

import pytest

from reviewer import verdicts
from reviewer.anthropic_adapter import ClaudeAdapter, request_for, schema_for_claude
from reviewer.caps import Limits, Usage
from reviewer.models import ProviderError, Transient
from reviewer.providers import real_adapters
from reviewer.review import review_stage

from .test_pipeline import run
from .test_run import READY
from .world import World

PREPARED = run(World(), 10)
SONNET = next(slot for slot in READY.panel if slot.provider == "anthropic")
HAIKU = next(slot for slot in READY.gate if slot.provider == "anthropic")
PANEL_SCHEMA = verdicts.panel_schema(READY)
ANSWER = json.dumps({"outcome": "already_covered", "confidence": 0.8, "covered_by": 2, "reasons": ["It repeats proposal 2."]})


def message(text=ANSWER, stop_reason="end_turn", model="claude-sonnet-5-5", category=None, usage=None):
    return SimpleNamespace(
        model=model,
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category, explanation="x") if stop_reason == "refusal" else None,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)] if stop_reason != "refusal" else [],
        usage=usage or SimpleNamespace(input_tokens=1200, cache_read_input_tokens=90000, cache_creation_input_tokens=300, output_tokens=4000),
    )


class ApiError(Exception):
    def __init__(self, status_code):
        super().__init__("an error whose text must never be printed")
        self.status_code = status_code


class APIConnectionError(Exception):
    pass


class FakeClient:
    """Stands in for anthropic.Anthropic: it has `messages.count_tokens` and `messages.stream`, and keeps what it was sent."""

    def __init__(self, reply=None, counted=100000, fail=None):
        self.reply, self.counted, self.fail = reply or message(), counted, fail
        self.counted_with, self.streamed_with, self.created = [], [], 0
        self.messages = self

    def count_tokens(self, **kwargs):
        self.counted_with.append(kwargs)
        return SimpleNamespace(input_tokens=self.counted)

    def create(self, **kwargs):  # must never be used: a plain request can time out at this output limit
        self.created += 1
        raise AssertionError("the adapter must stream")

    def stream(self, **kwargs):
        self.streamed_with.append(kwargs)
        if self.fail:
            raise self.fail
        client = self

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return client.reply

        return Stream()


# ---- what is sent ------------------------------------------------------------------------------------------------------

def test_the_request_holds_exactly_what_the_spec_allows():
    request = request_for(SONNET, PREPARED.panel, PANEL_SCHEMA)
    assert set(request) == {"model", "max_tokens", "system", "messages", "output_config"}  # no thinking, tools, sampling, fallback or beta setting
    assert request["model"] == "claude-sonnet-5-5" and request["max_tokens"] == 64000
    assert request["output_config"]["effort"] == "xhigh" and request["output_config"]["format"]["type"] == "json_schema"
    assert request["system"] == [{"type": "text", "text": PREPARED.panel.instructions()}]
    assert len(request["messages"]) == 1 and request["messages"][0]["role"] == "user"
    blocks = request["messages"][0]["content"]
    assert [block["text"] for block in blocks] == [part.text for part in PREPARED.panel.rest()]
    assert "".join(json.dumps(request)).count("fallback") == 0 and "temperature" not in json.dumps(request) and "tool" not in json.dumps(request["output_config"])


def test_only_the_last_part_that_never_changes_is_marked_for_the_cache():
    blocks = request_for(SONNET, PREPARED.panel, PANEL_SCHEMA)["messages"][0]["content"]
    marked = [i for i, block in enumerate(blocks) if "cache_control" in block]
    names = [part.name for part in PREPARED.panel.rest()]
    assert marked == [names.index("context:idea_graveyard")] and blocks[marked[0]]["cache_control"] == {"type": "ephemeral"}
    assert all(not part.stable for part in PREPARED.panel.rest()[marked[0] + 1 :])  # nothing after it is the same next time
    gate = request_for(HAIKU, PREPARED.gate, verdicts.GATE_SCHEMA)
    assert not any("cache_control" in block for block in gate["messages"][0]["content"])
    assert gate["model"] == "claude-haiku-5-5" and gate["output_config"]["effort"] == "medium" and gate["max_tokens"] == 8000


def test_the_schema_is_put_in_the_form_structured_outputs_accept():
    schema = schema_for_claude(PANEL_SCHEMA)
    assert schema["properties"]["covered_by"] == {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    assert schema["properties"]["outcome"]["enum"] == list(READY.outcome_keys) and schema["additionalProperties"] is False
    assert "anyOf" in schema_for_claude(verdicts.GATE_SCHEMA)["properties"]["reason"]
    assert PANEL_SCHEMA["properties"]["covered_by"] == {"type": ["integer", "null"]}  # the original is not changed
    text = json.dumps(schema)
    for unsupported in ("minimum", "maximum", "minLength", "maxLength", "maxItems"):
        assert unsupported not in text


# ---- what comes back -----------------------------------------------------------------------------------------------------

def test_a_normal_answer_is_streamed_counted_and_recorded():
    client = FakeClient()
    call = ClaudeAdapter(client).call(SONNET, PREPARED.panel, PANEL_SCHEMA)
    assert client.created == 0 and len(client.streamed_with) == 1 and len(client.counted_with) == 1
    assert client.counted_with[0] == {key: client.streamed_with[0][key] for key in ("model", "system", "messages")}  # what is counted is what is sent
    assert (call.status, call.answer, call.reported_model) == ("ok", ANSWER, "claude-sonnet-5-5")
    assert (call.input_tokens, call.cached_input_tokens, call.output_tokens) == (1200 + 90000 + 300, 90000, 4000)
    assert verdicts.read_panel(call.answer, READY, frozenset({2})).covered_by == 2


def test_a_refusal_is_recorded_with_its_category_and_no_content_is_read():
    refused = message(stop_reason="refusal", category="bio")
    refused.content = None  # reading it would fail: the adapter must not try
    call = ClaudeAdapter(FakeClient(refused)).call(SONNET, PREPARED.panel, PANEL_SCHEMA)
    assert (call.status, call.refusal_category, call.answer) == ("refused", "bio", None)


def test_an_answer_cut_off_at_the_limit_and_an_input_that_cannot_fit():
    cut = ClaudeAdapter(FakeClient(message(text='{"outcome": "evid', stop_reason="max_tokens"))).call(SONNET, PREPARED.panel, PANEL_SCHEMA)
    assert (cut.status, cut.answer) == ("truncated", None)
    client = FakeClient(counted=990000)
    too_big = ClaudeAdapter(client).call(SONNET, PREPARED.panel, PANEL_SCHEMA)
    assert too_big.status == "too_large" and client.streamed_with == []  # caught by the count, before any model call
    assert ClaudeAdapter(FakeClient(message(stop_reason="pause_turn"))).call(SONNET, PREPARED.panel, PANEL_SCHEMA).status == "error"


def test_a_passing_fault_is_worth_another_try_and_a_real_error_is_not_hidden():
    for passing in (ApiError(429), ApiError(500), ApiError(529), APIConnectionError()):
        with pytest.raises(Transient):
            ClaudeAdapter(FakeClient(fail=passing)).call(SONNET, PREPARED.panel, PANEL_SCHEMA)
    for status in (400, 401, 403, 404):  # turned down by the provider: its status is kept, its words are not
        with pytest.raises(ProviderError) as error:
            ClaudeAdapter(FakeClient(fail=ApiError(status))).call(SONNET, PREPARED.panel, PANEL_SCHEMA)
        assert str(error.value) == f"anthropic: status {status}" and error.value.stops_the_run == (status in (401, 403, 404))
    with_a_word = ApiError(400)
    with_a_word.body = {"type": "error", "error": {"type": "invalid_request_error", "message": "words that are never kept"}}
    with pytest.raises(ProviderError) as error:
        ClaudeAdapter(FakeClient(fail=with_a_word)).call(SONNET, PREPARED.panel, PANEL_SCHEMA)
    assert str(error.value) == "anthropic: status 400, invalid_request_error"
    with pytest.raises(RuntimeError):  # a fault that is not the provider's answer is not dressed up as one
        ClaudeAdapter(FakeClient(fail=RuntimeError("something else"))).call(SONNET, PREPARED.panel, PANEL_SCHEMA)
    with pytest.raises(ValueError):
        ClaudeAdapter(FakeClient()).call(next(slot for slot in READY.panel if slot.provider == "openai"), PREPARED.panel, PANEL_SCHEMA)


def test_through_the_review_step_only_the_claude_slot_is_filled_and_the_others_wait():
    client = FakeClient()
    results = []
    review_stage("panel", READY.panel, PREPARED.panel, results, {"anthropic": ClaudeAdapter(client)}, READY, Limits(READY.caps), Usage(day="2026-11-20"), "2026-11-20T06:17:00Z", index_numbers=PREPARED.index.numbers)
    by_slot = {slot["slot"]: slot for slot in results}
    assert by_slot["panel-3"]["status"] == "verdict" and by_slot["panel-3"]["verdict"]["outcome"] == "already_covered" and by_slot["panel-3"]["reported_model"] == "claude-sonnet-5-5"
    assert by_slot["panel-3"]["calls"][0]["cached_input_tokens"] == 90000
    assert [(by_slot[name]["status"], by_slot[name]["reason"]) for name in ("panel-1", "panel-2")] == [("pending", "no_adapter")] * 2
    refusing = []
    review_stage("panel", READY.panel, PREPARED.panel, refusing, {"anthropic": ClaudeAdapter(FakeClient(message(stop_reason="refusal", category="bio")))}, READY, Limits(READY.caps), Usage(day="2026-11-20"), "2026-11-20T06:17:00Z")
    assert [(s["status"], s["reason"], s["refusal_category"]) for s in refusing if s["slot"] == "panel-3"] == [("no_verdict", "refused", "bio")]


def test_with_no_key_there_is_no_real_adapter():
    assert real_adapters({}) == {} and real_adapters({"ANTHROPIC_API_KEY": ""}) == {} and real_adapters(None) == {}


def test_the_sdk_is_told_not_to_try_again_on_its_own(monkeypatch):
    import sys

    made = []
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=lambda **options: made.append(options) or FakeClient()))
    adapters = real_adapters({"ANTHROPIC_API_KEY": "a-made-up-key"})
    assert made == [{"api_key": "a-made-up-key", "max_retries": 0}] and isinstance(adapters["anthropic"], ClaudeAdapter)


def test_no_credit_arrives_as_a_bad_request_and_stops_the_run_without_keeping_the_providers_words():
    empty = ApiError(400)
    empty.body = {"type": "error", "error": {"type": "invalid_request_error", "message": "Your credit balance is too low to access the API."}}
    with pytest.raises(ProviderError) as error:
        ClaudeAdapter(FakeClient(fail=empty)).call(SONNET, PREPARED.panel, PANEL_SCHEMA)
    assert str(error.value) == "anthropic: status 400, credit_balance_too_low" and error.value.stops_the_run
    ordinary = ApiError(400)
    ordinary.body = {"type": "error", "error": {"type": "invalid_request_error", "message": "max_tokens: must be at most 64000"}}
    with pytest.raises(ProviderError) as error:
        ClaudeAdapter(FakeClient(fail=ordinary)).call(SONNET, PREPARED.panel, PANEL_SCHEMA)
    assert str(error.value) == "anthropic: status 400, invalid_request_error" and not error.value.stops_the_run
