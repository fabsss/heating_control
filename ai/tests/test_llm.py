import json
from dataclasses import replace

import pytest

from analysis.llm import (CostLimitExceeded, InvalidResponse, LlmError, ZdrUnavailable, complete,
                          estimate_cost, fetch_pricing, parse_candidates_json, request_candidates,
                          with_live_prices)
from analysis.prompts import build_messages

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
VALID = json.dumps({"candidates": [{"room": "Wohnzimmer"}]})


async def no_sleep(_seconds):
    return None


def config(acfg, fake, **overrides):
    return replace(acfg, openrouter_url=fake.url, retry_backoff_s=0.0, **overrides)


# --- Prompts -------------------------------------------------------------------

def test_build_messages_contains_data_vocabulary_and_limits():
    messages = build_messages("weekly", {"a": 1}, "VOKABULAR", 20, presets=["comfort", "eco"],
                              flat_states=["home"], rooms=["Wohnzimmer"])
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "VOKABULAR" in messages[0]["content"] and "höchstens 20" in messages[0]["content"]
    assert "JSON" in messages[0]["content"]
    user = messages[1]["content"]
    assert '{"a":1}' in user
    assert "comfort" in user and "eco" in user and "home" in user and "Wohnzimmer" in user


def test_modes_have_different_instructions_and_unknown_mode_raises():
    weekly = build_messages("weekly", {}, "V", 5, presets=[], flat_states=[], rooms=[])
    clusters = build_messages("clusters", {}, "V", 5, presets=[], flat_states=[], rooms=[])
    assert weekly[1]["content"].splitlines()[0] != clusters[1]["content"].splitlines()[0]
    assert weekly[0]["content"] == clusters[0]["content"]
    with pytest.raises(ValueError):
        build_messages("anders", {}, "V", 5, presets=[], flat_states=[], rooms=[])


# --- Antwort lesen -----------------------------------------------------------------

def test_parse_candidates_json_strips_markdown_fences():
    fenced = "```json\n" + VALID + "\n```"
    assert parse_candidates_json(fenced, 20) == [{"room": "Wohnzimmer"}]


def test_parse_candidates_json_accepts_bare_list_and_truncates():
    text = json.dumps([{"n": i} for i in range(5)])
    assert parse_candidates_json(text, 3) == [{"n": 0}, {"n": 1}, {"n": 2}]


@pytest.mark.parametrize("text", ["", "kein json", "{}", '{"candidates": "x"}', '{"candidates": [1]}', "42"])
def test_parse_candidates_json_rejects_bad_shapes(text):
    with pytest.raises(InvalidResponse):
        parse_candidates_json(text, 20)


# --- Aufruf ----------------------------------------------------------------------------

async def test_complete_sends_zdr_provider_key_and_settings(acfg, fake_llm):
    fake_llm.responses = [{"content": VALID}]
    result = await complete(config(acfg, fake_llm), MESSAGES, "geheim", sleep=no_sleep)
    assert (result.text, result.tokens_in, result.tokens_out, result.cost_usd) == (VALID, 100, 50, 0.01)
    (request,) = fake_llm.requests
    payload = request["payload"]
    assert request["auth"] == "Bearer geheim"
    assert payload["provider"] == {"zdr": True, "data_collection": "deny"}
    assert payload["model"] == acfg.model and payload["temperature"] == acfg.temperature
    assert payload["max_tokens"] == acfg.max_output_tokens and payload["messages"] == MESSAGES


async def test_cost_falls_back_to_estimate_from_tokens(acfg, fake_llm):
    fake_llm.responses = [{"content": VALID, "tokens_in": 1000, "tokens_out": 500, "cost": None}]
    result = await complete(config(acfg, fake_llm), MESSAGES, "k", sleep=no_sleep)
    assert result.cost_usd == pytest.approx(1000 * 3.0 / 1e6 + 500 * 15.0 / 1e6)


async def test_missing_zdr_provider_raises_zdr_unavailable(acfg, fake_llm):
    fake_llm.responses = [{"status": 404, "message": "No endpoints found matching your data policy"}]
    with pytest.raises(ZdrUnavailable, match="Zero Data Retention"):
        await complete(config(acfg, fake_llm), MESSAGES, "k", sleep=no_sleep)
    assert len(fake_llm.requests) == 1


async def test_server_errors_are_retried(acfg, fake_llm):
    fake_llm.responses = [{"status": 503}, {"status": 429}, {"content": VALID}]
    result = await complete(config(acfg, fake_llm), MESSAGES, "k", sleep=no_sleep)
    assert result.text == VALID and len(fake_llm.requests) == 3


async def test_gives_up_after_the_configured_retries(acfg, fake_llm):
    fake_llm.responses = [{"status": 503}] * 10
    with pytest.raises(LlmError, match="fehlgeschlagen"):
        await complete(config(acfg, fake_llm, retries=2), MESSAGES, "k", sleep=no_sleep)
    assert len(fake_llm.requests) == 3


async def test_client_errors_are_not_retried(acfg, fake_llm):
    fake_llm.responses = [{"status": 401, "message": "bad key"}]
    with pytest.raises(LlmError, match="401"):
        await complete(config(acfg, fake_llm), MESSAGES, "k", sleep=no_sleep)
    assert len(fake_llm.requests) == 1


async def test_unreachable_server_raises_llm_error(acfg):
    cfg = replace(acfg, openrouter_url="http://127.0.0.1:9/x", retries=1, retry_backoff_s=0.0)
    with pytest.raises(LlmError, match="fehlgeschlagen"):
        await complete(cfg, MESSAGES, "k", sleep=no_sleep)


# --- Kandidaten anfragen -----------------------------------------------------------------

async def test_invalid_answer_is_retried_once_with_the_error(acfg, fake_llm):
    fake_llm.responses = [{"content": "kein json"}, {"content": VALID}]
    items, total = await request_candidates(config(acfg, fake_llm), MESSAGES, "k", sleep=no_sleep)
    assert items == [{"room": "Wohnzimmer"}]
    assert len(fake_llm.requests) == 2
    second = fake_llm.requests[1]["payload"]["messages"]
    assert second[:2] == MESSAGES
    assert second[2] == {"role": "assistant", "content": "kein json"}
    assert "ungültig" in second[3]["content"] and "JSON" in second[3]["content"]
    assert (total.tokens_in, total.tokens_out) == (200, 100)
    assert total.cost_usd == pytest.approx(0.02)


async def test_second_invalid_answer_raises(acfg, fake_llm):
    fake_llm.responses = [{"content": "kein json"}, {"content": "immer noch nicht"}]
    with pytest.raises(InvalidResponse):
        await request_candidates(config(acfg, fake_llm), MESSAGES, "k", sleep=no_sleep)
    assert len(fake_llm.requests) == 2


async def test_cost_limit_blocks_the_call(acfg, fake_llm):
    with pytest.raises(CostLimitExceeded):
        await request_candidates(config(acfg, fake_llm, max_cost_usd=0.0001), MESSAGES, "k", sleep=no_sleep)
    assert fake_llm.requests == []


async def test_cost_limit_counts_what_the_first_attempt_already_cost(acfg, fake_llm):
    fake_llm.responses = [{"content": "kein json", "cost": 1.9}]
    with pytest.raises(CostLimitExceeded):
        await request_candidates(config(acfg, fake_llm, max_cost_usd=2.0), MESSAGES, "k", sleep=no_sleep)
    assert len(fake_llm.requests) == 1


def test_estimate_cost_uses_prices_and_output_limit(acfg):
    cost = estimate_cost([{"role": "user", "content": "x" * 3000}], acfg)
    assert cost == pytest.approx(1000 * 3.0 / 1e6 + acfg.max_output_tokens * 15.0 / 1e6)


# --- Preise von OpenRouter ----------------------------------------------------------

def listing(model="test/model", prompt="0.000002", completion="0.00001"):
    return [{"id": "other/m", "pricing": {"prompt": "1", "completion": "1"}},
            {"id": model, "pricing": {"prompt": prompt, "completion": completion}}]


async def test_fetch_pricing_reads_usd_per_million_tokens(acfg, fake_llm):
    fake_llm.models = listing("test/model")
    cfg = config(acfg, fake_llm, model="test/model")
    assert await fetch_pricing(cfg) == pytest.approx((2.0, 10.0))


async def test_fetch_pricing_returns_none_for_unknown_model_or_errors(acfg, fake_llm):
    fake_llm.models = listing("test/model")
    assert await fetch_pricing(config(acfg, fake_llm, model="gibt/es-nicht")) is None
    fake_llm.models_status = 500
    assert await fetch_pricing(config(acfg, fake_llm, model="test/model")) is None
    assert await fetch_pricing(replace(acfg, openrouter_url="http://127.0.0.1:9/api/v1/chat/completions")) is None


async def test_fetch_pricing_ignores_unusable_prices(acfg, fake_llm):
    fake_llm.models = listing("test/model", prompt="-1", completion="abc")
    assert await fetch_pricing(config(acfg, fake_llm, model="test/model")) is None


async def test_with_live_prices_uses_openrouter_unless_env_set(acfg, fake_llm):
    fake_llm.models = listing("test/model")
    live = await with_live_prices(config(acfg, fake_llm, model="test/model"))
    assert (live.price_input, live.price_output, live.price_source) == (pytest.approx(2.0), pytest.approx(10.0), "openrouter")
    pinned = config(acfg, fake_llm, model="test/model", price_source="env", price_input=9.0)
    assert await with_live_prices(pinned) == pinned


async def test_with_live_prices_falls_back_to_file_values(acfg, fake_llm):
    fake_llm.models = []
    cfg = config(acfg, fake_llm, model="test/model")
    result = await with_live_prices(cfg)
    assert (result.price_input, result.price_output, result.price_source) == (cfg.price_input, cfg.price_output, "datei")
