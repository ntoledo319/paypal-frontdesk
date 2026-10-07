"""Extraction tests: rules-based fallback, LLM adapter, env behavior."""

from __future__ import annotations

from datetime import date

from paypal_frontdesk.llm import (
    HybridExtractor,
    LLMExtractor,
    RulesExtractor,
    extract_intent,
    make_extractor,
)

from conftest import TODAY


def test_rules_extract_all_slots(business):
    rules = RulesExtractor(business.services, TODAY)
    found = rules(
        "Hi, I'm Dana Lee. I'd like a full interior detail next Friday at 2 pm. "
        "My number is 555-214-8690 and my email is dana.lee@example.com."
    )
    assert found["name"] == "Dana Lee"
    assert found["service"] == "Full Interior Detail"
    assert found["date"] == "2026-10-16"  # next Friday after Wed 2026-10-07
    assert found["time"] == "14:00"
    assert found["phone"] == "(555) 214-8690"
    assert found["email"] == "dana.lee@example.com"


def test_rules_spoken_phone(business):
    rules = RulesExtractor(business.services, TODAY)
    found = rules("call me at five five five, two one four, eight six nine zero")
    assert found["phone"] == "(555) 214-8690"


def test_rules_date_words(business):
    rules = RulesExtractor(business.services, TODAY)
    assert rules("tomorrow please")["date"] == "2026-10-08"
    assert rules("October 20th")["date"] == "2026-10-20"
    assert rules("10/20")["date"] == "2026-10-20"


def test_rules_time_words(business):
    rules = RulesExtractor(business.services, TODAY)
    assert rules("2:30 pm")["time"] == "14:30"
    assert rules("noon")["time"] == "12:00"
    assert rules("two in the afternoon")["time"] == "14:00"


def test_rules_service_alias_matching(business):
    rules = RulesExtractor(business.services, TODAY)
    assert rules("I need my engine bay cleaned")["service"] == "Engine Bay Cleaning"
    assert rules("wash and wax please")["service"] == "Exterior Wash & Wax"


def test_intent_keywords():
    assert extract_intent("I need to cancel my appointment") == "cancel"
    assert extract_intent("can you send me the invoice?") == "invoice"
    assert extract_intent("I just paid") == "paid"
    assert extract_intent("yes that works") == "confirm"
    assert extract_intent("no, change the time") == "deny"
    assert extract_intent("I'd like to book a wash") == "book"


def test_make_extractor_rules_without_key(business, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    extractor, description = make_extractor(business.services, "Test", TODAY)
    assert isinstance(extractor, RulesExtractor)
    assert "rules-based" in description
    assert "no OPENAI_API_KEY" in description  # honestly described fallback


def test_make_extractor_hybrid_with_key(business):
    env = {
        "OPENAI_API_KEY": "sk-test",
        "OPENAI_BASE_URL": "http://127.0.0.1:9",
        "OPENAI_MODEL": "test-model",
    }
    extractor, description = make_extractor(
        business.services, "Test", TODAY, environ=env
    )
    assert isinstance(extractor, HybridExtractor)
    assert "test-model" in description


def test_llm_extractor_parses_json_response(business, monkeypatch):
    import io
    import json
    import urllib.request

    payload = {
        "choices": [
            {
                "message": {
                    "content": '{"intent": "book", "name": "Sam Oaks", '
                    '"service": "Exterior Wash & Wax"}'
                }
            }
        ]
    }

    class FakeResponse:
        status = 200

        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization")
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    llm = LLMExtractor(
        "http://llm.test", "sk-test", "m", business.services, "Test", TODAY
    )
    found = llm("sam oaks wants a wax")
    assert found == {"intent": "book", "name": "Sam Oaks", "service": "Exterior Wash & Wax"}
    assert captured["url"] == "http://llm.test/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"


def test_llm_extractor_returns_empty_on_failure(business, monkeypatch):
    import urllib.request

    def boom(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    llm = LLMExtractor("http://llm.test", "sk", "m", business.services, "Test", TODAY)
    assert llm("anything") == {}


def test_llm_extractor_drops_garbage_fields(business, monkeypatch):
    import json
    import urllib.request

    payload = {"choices": [{"message": {"content": '{"intent": "fly", "name": 42}'}}]}

    class FakeResponse:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda r, timeout=None: FakeResponse())
    llm = LLMExtractor("http://llm.test", "sk", "m", business.services, "Test", TODAY)
    assert llm("hi") == {}  # bad intent dropped, non-string name dropped


def test_hybrid_rules_win_ties_llm_fills_gaps(business):
    class StubLLM:
        def __call__(self, text):
            return {"name": "Wrong Name", "email": "stub@example.com"}

    rules = RulesExtractor(business.services, TODAY)
    hybrid = HybridExtractor(rules, StubLLM())
    found = hybrid("I'm Dana Lee")
    assert found["name"] == "Dana Lee"          # rules win the tie
    assert found["email"] == "stub@example.com"  # LLM fills the gap
