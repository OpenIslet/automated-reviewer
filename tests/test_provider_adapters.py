"""The Google and OpenAI adapters, against a stand-in for HTTP. Neither has been run against the real API: no key exists yet."""
import dataclasses
import json

import pytest

from reviewer import google_adapter, openai_adapter, verdicts
from reviewer.anthropic_adapter import ClaudeAdapter
from reviewer.caps import Limits, Usage
from reviewer.google_adapter import GoogleAdapter
from reviewer.models import ProviderError, Transient
from reviewer.openai_adapter import OpenAIAdapter
from reviewer.providers import real_adapters
from reviewer.review import gate_recommendation, review_stage

from .test_anthropic_adapter import ANSWER, PREPARED, FakeClient, message
from .test_run import READY
from .world import PROMPTS

KEY = "a-made-up-key-that-must-never-be-printed"
PANEL_SCHEMA = verdicts.panel_schema(READY)
HYDROGEN = next(s for s in READY.panel if s.provider == "google")
SOL = next(s for s in READY.panel if s.provider == "openai")
HYDROGEN_GATE = next(s for s in READY.gate if s.provider == "google")
LUNA_GATE = next(s for s in READY.gate if s.provider == "openai")
# No slot in the settings uses a model that answers with a probability. These two stand for one that would.
DECISION = dataclasses.replace(HYDROGEN_GATE, model="gemini-decision-1", kind="probability", threshold=0.5)
LUNA = dataclasses.replace(LUNA_GATE, kind="probability", threshold=0.5)
GATE_ANSWER = json.dumps({"spam": False, "probability": 0.04, "reason": "On topic."})
QUESTION = PROMPTS.gate_question


class FakeHttp:
    """Answers each address from a script, in turn, and keeps what it was sent."""

    def __init__(self, **answers):
        self.answers = {ending: list(replies) if isinstance(replies, list) else [replies] for ending, replies in answers.items()}
        self.sent = []

    def post(self, url, headers, body, timeout=600):
        self.sent.append((url, dict(headers), json.loads(json.dumps(body))))
        for ending, replies in self.answers.items():
            if url.endswith(ending):
                return replies.pop(0) if len(replies) > 1 else replies[0]
        return 404, {"error": {"code": "not_found", "message": "no such address"}}

    def to(self, ending):
        return [(url, headers, body) for url, headers, body in self.sent if url.endswith(ending)]


def interaction(status="completed", text=ANSWER):
    return 200, {
        "id": "v1_x", "model": "gemini-4-hydrogen", "status": status,
        "steps": [{"type": "thought", "summary": []}, {"type": "model_output", "content": [{"type": "text", "text": text}]}] if status == "completed" else [],
        "usage": {"total_input_tokens": 95000, "total_cached_tokens": 90000, "total_output_tokens": 200, "total_thought_tokens": 3800, "total_tokens": 99000},
    }


def response(status="completed", content=None, reason=None, model="gpt-6.1-sol"):
    content = [{"type": "output_text", "text": ANSWER}] if content is None else content
    return 200, {
        "id": "resp_x", "model": model, "status": status, "incomplete_details": {"reason": reason} if reason else None,
        "output": [{"type": "reasoning", "summary": []}, {"type": "message", "role": "assistant", "content": content}] if status == "completed" else [],
        "usage": {"input_tokens": 96000, "input_tokens_details": {"cached_tokens": 90000}, "output_tokens": 4100, "output_tokens_details": {"reasoning_tokens": 3900}, "total_tokens": 100100},
    }


COUNT_OK = (200, {"totalTokens": 95000})
TOKENS_OK = (200, {"object": "response.input_tokens", "input_tokens": 96000})


# ---- Google: the panel slot ----------------------------------------------------------------------------------------------

def test_google_panel_request_holds_exactly_what_the_spec_allows():
    http = FakeHttp(**{":countTokens": COUNT_OK, "/interactions": interaction()})
    GoogleAdapter(KEY, http).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA)
    (count_url, count_headers, count_body), (url, headers, body) = http.sent
    assert count_url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-4-hydrogen:countTokens"
    assert url == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert headers == {"x-goog-api-key": KEY, "Api-Revision": "2026-05-20"} == count_headers
    assert set(body) == {"model", "system_instruction", "input", "generation_config", "response_format", "store"}  # no tool, sampling or cache setting
    assert body["model"] == "gemini-4-hydrogen" and body["store"] is False
    assert body["generation_config"] == {"thinking_level": "xhigh", "max_output_tokens": 128000}
    assert body["response_format"] == {"type": "text", "mime_type": "application/json", "schema": PANEL_SCHEMA}
    assert body["system_instruction"] == PREPARED.panel.instructions()
    assert body["input"] == "\n\n".join(part.text for part in PREPARED.panel.rest())
    assert count_body == {"contents": [{"parts": [{"text": body["system_instruction"] + "\n\n" + body["input"]}]}]}  # what is counted is what is sent


def test_google_panel_answers_are_read_as_documented():
    ok = GoogleAdapter(KEY, FakeHttp(**{":countTokens": COUNT_OK, "/interactions": interaction()})).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA)
    assert (ok.status, ok.answer, ok.reported_model) == ("ok", ANSWER, "gemini-4-hydrogen")
    assert (ok.input_tokens, ok.cached_input_tokens, ok.output_tokens) == (95000, 90000, 200 + 3800)  # thinking counts as output
    for state in ("incomplete", "requires_continuation"):
        cut = GoogleAdapter(KEY, FakeHttp(**{":countTokens": COUNT_OK, "/interactions": interaction(state)})).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA)
        assert (cut.status, cut.answer) == ("truncated", None)
    too_big = FakeHttp(**{":countTokens": (200, {"totalTokens": 1_040_000})})
    assert GoogleAdapter(KEY, too_big).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA).status == "too_large" and too_big.to("/interactions") == []


def test_google_reports_a_blocked_request_as_an_error_with_a_code_and_that_is_a_refusal():
    for code in ("safety", "prohibited_content", "blocklist", "jailbreak", "content_blocked"):
        blocked = (400, {"error": {"code": code, "message": "Request blocked. The text of this message is never recorded."}})
        call = GoogleAdapter(KEY, FakeHttp(**{":countTokens": COUNT_OK, "/interactions": blocked})).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA)
        assert (call.status, call.refusal_category, call.answer) == ("refused", code, None)
    failed = (200, {"model": "gemini-4-hydrogen", "status": "failed", "errors": [{"code": "safety", "message": "x"}], "usage": {}})
    assert GoogleAdapter(KEY, FakeHttp(**{":countTokens": COUNT_OK, "/interactions": failed})).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA).status == "refused"


def test_google_faults_are_sorted_into_passing_ones_and_real_ones():
    for status in (0, 429, 500, 503, 504):
        with pytest.raises(Transient):
            GoogleAdapter(KEY, FakeHttp(**{":countTokens": COUNT_OK, "/interactions": (status, None)})).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA)
    for status, code in ((400, "invalid_request"), (401, "authentication"), (402, "payment_required"), (403, "permission_denied"), (404, "model_not_found")):
        with pytest.raises(ProviderError) as error:
            GoogleAdapter(KEY, FakeHttp(**{":countTokens": COUNT_OK, "/interactions": (status, {"error": {"code": code, "message": f"detail mentioning {KEY}"}})})).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA)
        assert str(error.value) == f"google: status {status}, {code}" and KEY not in str(error.value)
    with pytest.raises(ValueError):
        GoogleAdapter(KEY, FakeHttp()).call(SOL, PREPARED.panel, PANEL_SCHEMA)


# ---- the gate as it is set: three small models that answer in text -------------------------------------------------------

def test_the_gate_slots_ask_the_same_question_in_text_at_a_medium_setting():
    google = FakeHttp(**{":countTokens": COUNT_OK, "/interactions": interaction(text=GATE_ANSWER)})
    call = GoogleAdapter(KEY, google).call(HYDROGEN_GATE, PREPARED.gate, verdicts.GATE_SCHEMA)
    body = google.to("/interactions")[0][2]
    assert body["model"] == "gemini-4-hydrogen" and body["generation_config"] == {"thinking_level": "medium", "max_output_tokens": 8000}
    assert body["response_format"]["schema"] == verdicts.GATE_SCHEMA and body["system_instruction"] == PROMPTS.gate
    assert PROMPTS.gate_answer in body["input"] and "Signal X should be checked in a second assay" in body["input"]
    assert verdicts.read_gate(call.answer).spam is False and google.to("/decisions") == []

    openai = FakeHttp(**{"/responses/input_tokens": TOKENS_OK, "/responses": response(content=[{"type": "output_text", "text": GATE_ANSWER}], model="gpt-6-luna")})
    call = OpenAIAdapter(KEY, openai).call(LUNA_GATE, PREPARED.gate, verdicts.GATE_SCHEMA)
    body = openai.to("/responses")[0][2]
    assert body["model"] == "gpt-6-luna" and body["reasoning"] == {"effort": "medium"} and body["max_output_tokens"] == 8000
    assert body["text"]["format"]["schema"] == verdicts.GATE_SCHEMA and body["text"]["format"]["strict"] is True
    assert body["instructions"] == PROMPTS.gate and body["input"][0]["content"][0]["text"] == PROMPTS.gate_answer
    assert (call.status, call.reported_model) == ("ok", "gpt-6-luna") and verdicts.read_gate(call.answer).reason == "On topic." and openai.to("/decisions") == []


# ---- Google: a model that answers with a probability (kept, not in use) ----------------------------------------------------

def test_google_gate_asks_one_yes_or_no_question_and_reads_a_probability():
    http = FakeHttp(**{"/decisions": (200, {"answers": {"spam": {"type": "noul", "noul": 0.97, "confidence": 0.97}}, "usage": {"total_input_tokens": 1500, "total_output_tokens": 0}})})
    call = GoogleAdapter(KEY, http).call(DECISION, PREPARED.gate, verdicts.GATE_SCHEMA, QUESTION)
    url, _, body = http.sent[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/decisions" and len(http.sent) == 1
    assert set(body) == {"model", "input", "questions"} and body["model"] == "gemini-decision-1"
    assert body["questions"] == {"spam": {"type": "noul", "instructions": QUESTION}}
    assert "Signal X should be checked in a second assay" in body["input"] and body["input"].startswith(PROMPTS.gate)
    assert "JSON" not in body["input"]  # the answer shape meant for text models is not sent to a model that writes no text
    assert (call.status, call.answer, call.reported_model, call.input_tokens, call.output_tokens) == ("ok", 0.97, None, 1500, 0)
    blocked = FakeHttp(**{"/decisions": (400, {"error": {"code": "safety", "message": "x"}})})
    assert GoogleAdapter(KEY, blocked).call(DECISION, PREPARED.gate, verdicts.GATE_SCHEMA, QUESTION).refusal_category == "safety"
    odd = FakeHttp(**{"/decisions": (200, {"answers": {"spam": {"type": "choice", "choice": "yes"}}, "usage": {}})})
    assert GoogleAdapter(KEY, odd).call(DECISION, PREPARED.gate, verdicts.GATE_SCHEMA, QUESTION).status == "error"


# ---- OpenAI: the panel slot -----------------------------------------------------------------------------------------------

def test_openai_panel_request_holds_exactly_what_the_spec_allows():
    http = FakeHttp(**{"/responses/input_tokens": TOKENS_OK, "/responses": response()})
    OpenAIAdapter(KEY, http).call(SOL, PREPARED.panel, PANEL_SCHEMA)
    (count_url, count_headers, count_body), (url, headers, body) = http.sent
    assert count_url == "https://api.openai.com/v1/responses/input_tokens" and url == "https://api.openai.com/v1/responses"
    assert headers == {"Authorization": f"Bearer {KEY}"} == count_headers
    assert set(body) == {"model", "instructions", "input", "reasoning", "max_output_tokens", "text", "store"}  # no tool, sampling or stream setting
    assert body["model"] == "gpt-6.1-sol" and body["store"] is False and body["max_output_tokens"] == 128000 and body["reasoning"] == {"effort": "xhigh"}
    assert body["text"] == {"format": {"type": "json_schema", "name": "verdict", "schema": PANEL_SCHEMA, "strict": True}}
    assert body["instructions"] == PREPARED.panel.instructions()
    assert body["input"] == [{"role": "user", "content": [{"type": "input_text", "text": part.text} for part in PREPARED.panel.rest()]}]
    assert count_body == {key: body[key] for key in ("model", "instructions", "input")}
    # Strict structured outputs need every field required and no extra ones: the panel's schema is already in that form.
    assert sorted(PANEL_SCHEMA["required"]) == sorted(PANEL_SCHEMA["properties"]) and PANEL_SCHEMA["additionalProperties"] is False


def test_openai_panel_answers_are_read_as_documented():
    ok = OpenAIAdapter(KEY, FakeHttp(**{"/responses/input_tokens": TOKENS_OK, "/responses": response()})).call(SOL, PREPARED.panel, PANEL_SCHEMA)
    assert (ok.status, ok.answer, ok.reported_model) == ("ok", ANSWER, "gpt-6.1-sol")
    assert (ok.input_tokens, ok.cached_input_tokens, ok.output_tokens) == (96000, 90000, 4100)

    def said(reply):
        return OpenAIAdapter(KEY, FakeHttp(**{"/responses/input_tokens": TOKENS_OK, "/responses": reply})).call(SOL, PREPARED.panel, PANEL_SCHEMA)

    refused = said(response(content=[{"type": "refusal", "refusal": "A refusal whose words are never recorded."}]))
    assert (refused.status, refused.refusal_category, refused.answer) == ("refused", "refusal", None)
    assert said(response("incomplete", reason="max_output_tokens")).status == "truncated"
    filtered = said(response("incomplete", reason="content_filter"))
    assert (filtered.status, filtered.refusal_category) == ("refused", "content_filter")
    declined = said((400, {"error": {"code": "invalid_prompt", "message": "x"}}))
    assert (declined.status, declined.refusal_category) == ("refused", "invalid_prompt")
    with pytest.raises(Transient):  # the provider's own fault: worth another try
        said((200, {"model": "gpt-6.1-sol", "status": "failed", "error": {"code": "server_error"}, "usage": {}}))
    assert said((200, {"model": "gpt-6.1-sol", "status": "failed", "error": {"code": "something_else"}, "usage": {}})).status == "error"
    too_big = FakeHttp(**{"/responses/input_tokens": (200, {"input_tokens": 930000})})
    assert OpenAIAdapter(KEY, too_big).call(SOL, PREPARED.panel, PANEL_SCHEMA).status == "too_large" and len(too_big.sent) == 1


def test_openai_faults_are_sorted_into_passing_ones_and_real_ones():
    for status in (0, 429, 500, 503):
        with pytest.raises(Transient):
            OpenAIAdapter(KEY, FakeHttp(**{"/responses/input_tokens": TOKENS_OK, "/responses": (status, None)})).call(SOL, PREPARED.panel, PANEL_SCHEMA)
    for status, code in ((400, "invalid_request_error"), (401, "invalid_api_key"), (404, "model_not_found")):
        with pytest.raises(ProviderError) as error:
            OpenAIAdapter(KEY, FakeHttp(**{"/responses/input_tokens": TOKENS_OK, "/responses": (status, {"error": {"code": code, "message": f"detail mentioning {KEY}"}})})).call(SOL, PREPARED.panel, PANEL_SCHEMA)
        assert str(error.value) == f"openai: status {status}, {code}" and KEY not in str(error.value)
    with pytest.raises(ProviderError):
        OpenAIAdapter(KEY, FakeHttp(**{"/responses/input_tokens": (401, {"error": {"code": "invalid_api_key"}})})).call(SOL, PREPARED.panel, PANEL_SCHEMA)


# ---- OpenAI: a model that answers with a probability (kept, not in use) ----------------------------------------------------

def test_openai_gate_asks_one_predicate_question_and_reads_a_probability():
    reply = (200, {"model": "gpt-6-luna", "answers": [{"type": "predicate", "name": "spam", "probability": 0.02}], "usage": {"input_tokens": 1400, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}, "output_tokens": 0}})
    http = FakeHttp(**{"/decisions": reply})
    call = OpenAIAdapter(KEY, http).call(LUNA, PREPARED.gate, verdicts.GATE_SCHEMA, QUESTION)
    url, _, body = http.sent[0]
    assert url == "https://api.openai.com/v1/decisions" and len(http.sent) == 1
    assert set(body) == {"model", "input", "questions"} and body["model"] == "gpt-6-luna"
    assert body["questions"] == [{"type": "predicate", "name": "spam", "instructions": QUESTION}]
    assert [m["role"] for m in body["input"]] == ["developer", "user"] and body["input"][0]["content"] == PROMPTS.gate
    assert "Signal X should be checked in a second assay" in body["input"][1]["content"] and "JSON" not in json.dumps(body["input"])
    assert (call.status, call.answer, call.reported_model, call.input_tokens) == ("ok", 0.02, "gpt-6-luna", 1400)
    refused = FakeHttp(**{"/decisions": (200, {"model": "gpt-6-luna", "answers": [{"type": "refusal", "name": "spam"}], "usage": {}})})
    assert OpenAIAdapter(KEY, refused).call(LUNA, PREPARED.gate, verdicts.GATE_SCHEMA, QUESTION).status == "refused"
    with pytest.raises(ValueError):
        OpenAIAdapter(KEY, FakeHttp()).call(LUNA, PREPARED.gate, verdicts.GATE_SCHEMA, None)  # no question, nothing to ask


# ---- all three together -----------------------------------------------------------------------------------------------------

def test_the_three_real_adapters_fill_the_whole_gate_and_the_whole_panel():
    spam = json.dumps({"spam": True, "probability": 0.9, "reason": "An advert."})
    google = FakeHttp(**{":countTokens": COUNT_OK, "/interactions": [interaction(text=GATE_ANSWER), interaction()]})
    openai = FakeHttp(**{"/responses/input_tokens": TOKENS_OK, "/responses": [response(content=[{"type": "output_text", "text": spam}], model="gpt-6-luna"), response()]})
    claude_gate = FakeClient(message(text=json.dumps({"spam": False, "probability": 0.03, "reason": "On topic."}), model="claude-haiku-5-5"))
    adapters = {"google": GoogleAdapter(KEY, google), "openai": OpenAIAdapter(KEY, openai), "anthropic": ClaudeAdapter(claude_gate)}
    gate, usage = [], Usage(day="2026-11-20")
    review_stage("gate", READY.gate, PREPARED.gate, gate, adapters, READY, Limits(READY.caps), usage, "2026-11-20T06:17:00Z", question=PROMPTS.gate_question)
    assert [(s["slot"], s["status"], s["verdict"]["spam"], s["reported_model"]) for s in gate] == [("gate-1", "verdict", False, "claude-haiku-5-5"), ("gate-2", "verdict", False, "gemini-4-hydrogen"), ("gate-3", "verdict", True, "gpt-6-luna")]
    assert gate_recommendation(gate) == "not_spam" and all(s["verdict"]["reason"] and s["threshold"] is None for s in gate)  # every verdict comes with a reason
    assert google.to("/decisions") == [] and openai.to("/decisions") == []
    adapters["anthropic"] = ClaudeAdapter(FakeClient())
    panel = []
    review_stage("panel", READY.panel, PREPARED.panel, panel, adapters, READY, Limits(READY.caps), usage, "2026-11-20T06:17:00Z", index_numbers=PREPARED.index.numbers)
    assert [(s["status"], s["verdict"]["outcome"], s["verdict"]["covered_by"]) for s in panel] == [("verdict", "already_covered", 2)] * 3
    assert [s["reported_model"] for s in panel] == ["gemini-4-hydrogen", "gpt-6.1-sol", "claude-sonnet-5-5"]
    assert usage.provider("google").calls == 2 and usage.provider("openai").calls == 2  # one gate call and one panel call each: counting tokens is not a model call
    assert KEY not in json.dumps(gate) + json.dumps(panel)


def test_an_adapter_is_made_only_for_a_provider_whose_key_is_set():
    made = real_adapters({"GEMINI_API_KEY": KEY, "OPENAI_API_KEY": KEY})
    assert sorted(made) == ["google", "openai"] and isinstance(made["google"], GoogleAdapter) and isinstance(made["openai"], OpenAIAdapter)
    assert real_adapters({"GEMINI_API_KEY": "", "OPENAI_API_KEY": None}) == {}
    assert set(google_adapter.TOTAL_TOKENS) | set(openai_adapter.MAX_INPUT_TOKENS) >= {s.model for s in READY.slots if s.provider != "anthropic"}  # every model in the settings is one an adapter knows


def test_a_text_the_provider_declines_at_the_count_is_a_refusal_not_a_fault():
    blocked = FakeHttp(**{":countTokens": (400, {"error": {"code": "safety", "message": "x"}})})
    call = GoogleAdapter(KEY, blocked).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA)
    assert (call.status, call.refusal_category) == ("refused", "safety") and blocked.to("/interactions") == []
    declined = FakeHttp(**{"/responses/input_tokens": (400, {"error": {"code": "invalid_prompt", "message": "x"}})})
    call = OpenAIAdapter(KEY, declined).call(SOL, PREPARED.panel, PANEL_SCHEMA)
    assert (call.status, call.refusal_category) == ("refused", "invalid_prompt") and len(declined.sent) == 1


def test_no_credit_arrives_like_a_rate_limit_and_is_not_tried_again():
    for ending in ("/responses/input_tokens", "/responses"):
        answers = {"/responses/input_tokens": TOKENS_OK, "/responses": response()}
        answers[ending] = (429, {"error": {"code": "insufficient_quota", "message": "x"}})
        with pytest.raises(ProviderError) as error:
            OpenAIAdapter(KEY, FakeHttp(**answers)).call(SOL, PREPARED.panel, PANEL_SCHEMA)
        assert error.value.stops_the_run and str(error.value) == "openai: status 429, insufficient_quota"
    with pytest.raises(Transient):  # an ordinary rate limit is still a passing fault
        OpenAIAdapter(KEY, FakeHttp(**{"/responses/input_tokens": TOKENS_OK, "/responses": (429, {"error": {"code": "rate_limit_exceeded"}})})).call(SOL, PREPARED.panel, PANEL_SCHEMA)
    assert [ProviderError("x", status).stops_the_run for status in (400, 401, 402, 403, 404, 422)] == [False, True, True, True, True, False]


OLD_STYLE_REFUSED_KEY = (400, {"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT", "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID", "domain": "googleapis.com"}]}})


def test_the_short_code_is_found_in_each_shape_a_provider_uses():
    from reviewer.http import error_code

    assert error_code({"error": {"code": "invalid_request", "message": "x"}}) == "invalid_request"
    assert error_code(OLD_STYLE_REFUSED_KEY[1]) == "API_KEY_INVALID"  # the exact reason, where there is one
    assert error_code({"error": {"code": 404, "status": "NOT_FOUND", "message": "x"}}) == "NOT_FOUND"
    for nothing in (None, {}, {"error": "text"}, {"error": {"code": 400}}, {"error": {"code": 400, "details": "x", "status": 7}}, {"error": {"code": 400, "details": [7, {"reason": 7}]}}):
        assert error_code(nothing) is None


def test_a_refused_google_key_stops_the_run_though_it_arrives_as_a_bad_request():
    with pytest.raises(ProviderError) as error:  # the token count comes first, and it answers in Google's older shape
        GoogleAdapter(KEY, FakeHttp(**{":countTokens": OLD_STYLE_REFUSED_KEY})).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA)
    assert str(error.value) == "google: status 400, API_KEY_INVALID" and error.value.stops_the_run
    no_billing = (400, {"error": {"code": "failed_precondition", "message": "x"}})
    with pytest.raises(ProviderError) as error:
        GoogleAdapter(KEY, FakeHttp(**{":countTokens": COUNT_OK, "/interactions": no_billing})).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA)
    assert error.value.stops_the_run
    one_bad_request = (400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "x"}})
    with pytest.raises(ProviderError) as error:
        GoogleAdapter(KEY, FakeHttp(**{":countTokens": one_bad_request})).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA)
    assert str(error.value) == "google: status 400, INVALID_ARGUMENT" and not error.value.stops_the_run
    for model_gone in ((404, {"error": {"code": "model_not_found"}}), (404, {"error": {"code": 404, "status": "NOT_FOUND"}})):
        with pytest.raises(ProviderError) as error:
            GoogleAdapter(KEY, FakeHttp(**{":countTokens": model_gone})).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA)
        assert error.value.stops_the_run


def test_how_much_a_model_thought_is_kept_beside_each_call():
    google = GoogleAdapter(KEY, FakeHttp(**{":countTokens": COUNT_OK, "/interactions": interaction()})).call(HYDROGEN, PREPARED.panel, PANEL_SCHEMA)
    openai = OpenAIAdapter(KEY, FakeHttp(**{"/responses/input_tokens": TOKENS_OK, "/responses": response()})).call(SOL, PREPARED.panel, PANEL_SCHEMA)
    assert (google.thinking_tokens, openai.thinking_tokens) == (3800, 3900)
    assert (google.output_tokens, openai.output_tokens) == (4000, 4100)  # thinking is inside the output count for both
    results = []
    adapters = {"openai": OpenAIAdapter(KEY, FakeHttp(**{"/responses/input_tokens": TOKENS_OK, "/responses": response()}))}
    review_stage("panel", READY.panel, PREPARED.panel, results, adapters, READY, Limits(READY.caps), Usage(day="2026-11-20"), "2026-11-20T06:17:00Z", index_numbers=PREPARED.index.numbers)
    sol = next(slot for slot in results if slot["slot"] == "panel-2")
    assert sol["calls"][0]["thinking_tokens"] == 3900 and sol["calls"][0]["output_tokens"] == 4100

