import dataclasses
import datetime as dt
import decimal
import json
import uuid
from dataclasses import replace

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

import analysis.prompt_registry as registry
from analysis import langfuse
from analysis.llm import CostLimitExceeded
from analysis.prompts import MODE_INTROS, PROMPT_SPECS, SYSTEM_PROMPT, build_messages
from analysis.runner import run_analysis
from tests.analysis_fixture import ROOM, build_habit_db
from tests.fake_langfuse import FakeLangfuse

HABIT_TIME = {"room": "Wohnzimmer", "hypothesis": "Feierabend",
              "trigger": {"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"},
              "action": {"type": "setpoint", "value": 21}}
BROKEN = {"room": "Wohnzimmer", "trigger": {"type": "magic"}, "action": {"type": "setpoint", "value": 21}}


def answer(*items):
    return {"content": json.dumps({"candidates": list(items)}, ensure_ascii=False)}


@pytest_asyncio.fixture
async def lf(monkeypatch):
    fake = FakeLangfuse()
    app = web.Application()
    app.router.add_post("/api/public/otel/v1/traces", fake.otlp)
    app.router.add_post("/api/public/scores", fake.score)
    app.router.add_get("/api/public/v2/prompts/{name}", fake.get_prompt)
    app.router.add_post("/api/public/v2/prompts", fake.create_prompt)
    server = TestServer(app)
    await server.start_server()
    fake.url = f"http://127.0.0.1:{server.port}"
    monkeypatch.setenv("LANGFUSE_BASE_URL", fake.url)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-1")
    monkeypatch.setattr(registry, "_seeded", False)
    try:
        yield fake
    finally:
        await server.close()


@pytest.fixture
def run_cfg(db, acfg, fake_llm):
    build_habit_db(db)
    return replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=(),
                   openrouter_url=fake_llm.url, retry_backoff_s=0.0)


@pytest.fixture(autouse=True)
def langfuse_off_by_default(monkeypatch):
    for name in ("LANGFUSE_BASE_URL", "LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
                 "APP_VERSION"):
        monkeypatch.delenv(name, raising=False)


# --- Einrichtung -------------------------------------------------------------------------------

def test_both_host_names_work(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "p")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "s")
    assert not langfuse.enabled()
    monkeypatch.setenv("LANGFUSE_HOST", "http://alt/")
    assert langfuse.enabled() and langfuse.host() == "http://alt"
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://neu")
    assert langfuse.host() == "http://neu"   # der SDK-Name hat Vorrang


async def test_without_langfuse_nothing_is_sent_and_the_run_works(db, run_cfg, fake_llm):
    fake_llm.responses = [answer(HABIT_TIME)]
    async with langfuse.trace_scope("x") as trace:
        assert trace.active is False
    summary = await run_analysis(db, run_cfg, "weekly", api_key="geheim")
    assert summary.candidates == 1


# --- Trace eines Laufs -------------------------------------------------------------------------

async def test_run_is_one_trace_with_steps_generation_and_scores(db, run_cfg, fake_llm, lf, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "abc123")
    fake_llm.responses = [answer(HABIT_TIME, BROKEN)]
    summary = await run_analysis(db, run_cfg, "weekly", api_key="geheim")

    assert len(lf.requests) == 1                       # einmal gesendet und abgewartet
    assert lf.headers[0]["x-langfuse-ingestion-version"] == "4"
    assert lf.headers[0]["Authorization"].startswith("Basic ")
    root = lf.root()
    assert root["name"] == "analysis_run" and root["attrs"]["langfuse.release"] == "abc123"
    assert root["attrs"]["langfuse.trace.metadata.run_id"] == summary.run_id
    output = lf.parsed(root, "langfuse.observation.output")
    assert output["kandidaten"] == 2 and output["kosten_usd"] == pytest.approx(0.01)
    # alle Spans eines Laufs teilen Trace-ID und tragen Sitzung und Name des Traces
    assert len({s["traceId"] for s in lf.spans()}) == 1
    assert all(s["attrs"]["langfuse.session.id"].startswith("analyse-weekly-") for s in lf.spans())
    assert all(s["attrs"]["langfuse.trace.name"] == "analysis_run" for s in lf.spans())

    steps = {s["name"]: s for s in lf.observations("span")}
    assert {"prepare", "parse_candidates", "backtest"} <= set(steps)
    assert lf.parsed(steps["prepare"], "langfuse.observation.output")["eingriffe"] > 0
    verdicts = lf.parsed(steps["backtest"], "langfuse.observation.output")
    assert [v["status"] for v in verdicts] == ["bestanden", "nicht_pruefbar"]

    (generation,) = lf.observations("generation")
    attrs = generation["attrs"]
    assert generation["name"] == "llm" and attrs["langfuse.observation.model.name"] == run_cfg.model
    assert lf.parsed(generation, "langfuse.observation.usage_details") == {"input": 100, "output": 50, "total": 150}
    assert lf.parsed(generation, "langfuse.observation.cost_details") == {"total": 0.01}
    assert lf.parsed(generation, "langfuse.observation.input")[0]["role"] == "system"
    assert "candidates" in attrs["langfuse.observation.output"]
    assert generation["parentSpanId"] == root["spanId"]

    assert {s["name"]: s["value"] for s in lf.scores} == {"kandidaten_bestanden": 0.5, "kandidaten_pruefbar": 0.5}
    assert all(s["traceId"] == root["traceId"] for s in lf.scores)


async def test_failed_call_is_recorded_and_still_sent(db, run_cfg, fake_llm, lf):
    fake_llm.responses = [{"status": 400, "message": "kaputt"}]
    with pytest.raises(Exception, match="HTTP 400"):
        await run_analysis(db, run_cfg, "weekly", api_key="geheim")
    (generation,) = lf.observations("generation")
    assert generation["attrs"]["langfuse.observation.level"] == "ERROR"
    assert "LlmError" in generation["attrs"]["langfuse.observation.status_message"]
    assert generation["status"]["code"] == 2
    assert lf.root()["attrs"]["langfuse.trace.metadata.error"] == "LlmError"


async def test_cost_limit_is_marked_as_error_step(db, run_cfg, fake_llm, lf):
    with pytest.raises(CostLimitExceeded):
        await run_analysis(db, replace(run_cfg, max_cost_usd=0.0), "weekly", api_key="geheim")
    (span,) = [s for s in lf.observations("span") if s["name"] == "cost_check"]
    assert span["attrs"]["langfuse.observation.level"] == "ERROR" and "Limit" in span["status"]["message"]


async def test_langfuse_outage_does_not_disturb_the_run(db, run_cfg, fake_llm, lf):
    lf.otlp_status = 500
    fake_llm.responses = [answer(HABIT_TIME)]
    assert (await run_analysis(db, run_cfg, "weekly", api_key="geheim")).candidates == 1


async def test_unreachable_langfuse_does_not_disturb_the_run(db, run_cfg, fake_llm, monkeypatch):
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "p")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "s")
    monkeypatch.setattr(registry, "_seeded", False)
    fake_llm.responses = [answer(HABIT_TIME)]
    assert (await run_analysis(db, run_cfg, "weekly", api_key="geheim")).candidates == 1


async def test_values_that_are_not_json_are_sent(lf):
    async with langfuse.trace_scope("x", input={"id": uuid.UUID(int=1)}) as trace:
        trace.span("s", output={"d": dt.date(2026, 1, 2), "n": decimal.Decimal("1.5"),
                                "t": dt.datetime(2026, 1, 2, tzinfo=dt.UTC)})
    (span,) = lf.observations("span")
    assert lf.parsed(span, "langfuse.observation.output")["n"] == "1.5"


async def test_otlp_message_has_valid_ids_and_times(lf):
    async with langfuse.trace_scope("x") as trace:
        trace.span("s")
    for span in lf.spans():
        assert len(span["traceId"]) == 32 and len(span["spanId"]) == 16
        assert int(span["endTimeUnixNano"]) >= int(span["startTimeUnixNano"]) > 10**18


# --- Prompt-Verwaltung -------------------------------------------------------------------------

def specs():
    return {s.key: s for s in PROMPT_SPECS}


def test_four_prompts_are_registered():
    assert [s.name for s in PROMPT_SPECS] == [
        "heating/system", "heating/mode-weekly", "heating/mode-scan", "heating/mode-clusters"]


async def test_code_prompts_give_the_same_messages_as_before(monkeypatch):
    resolved = await registry.resolve_all(PROMPT_SPECS)       # Langfuse aus
    kwargs = dict(presets=["eco"], flat_states=["home"], rooms=["Wohnzimmer"])
    for mode in MODE_INTROS:
        assert (build_messages(mode, {"a": 1}, "V", 7, prompts=resolved, **kwargs)
                == build_messages(mode, {"a": 1}, "V", 7, **kwargs))
    assert all(r.source == "code" and r.version is None for r in resolved.values())


async def test_missing_prompts_are_created_with_variables_as_double_braces(lf):
    resolved = await registry.resolve_all(PROMPT_SPECS)
    assert sorted(c["name"] for c in lf.created) == sorted(s.name for s in PROMPT_SPECS)
    system = next(c for c in lf.created if c["name"] == "heating/system")
    assert "{{max_candidates}}" in system["prompt"] and "{{vocabulary}}" in system["prompt"]
    assert '{"candidates":' in system["prompt"]                # JSON-Klammern bleiben
    assert system["labels"] == ["production"]
    assert all(r.source == "langfuse" and r.version == 1 for r in resolved.values())


async def test_existing_prompts_are_not_touched_and_a_newer_version_wins(lf):
    lf.prompts["heating/mode-weekly"] = {"type": "text", "prompt": "NEUE WOCHEN-ANWEISUNG", "version": 4}
    resolved = await registry.resolve_all(PROMPT_SPECS)
    assert "heating/mode-weekly" not in [c["name"] for c in lf.created]
    assert resolved["weekly"].text == "NEUE WOCHEN-ANWEISUNG" and resolved["weekly"].version == 4
    messages = build_messages("weekly", {}, "V", 3, presets=[], flat_states=[], rooms=[], prompts=resolved)
    assert messages[1]["content"].startswith("NEUE WOCHEN-ANWEISUNG")


async def test_edited_system_prompt_is_compiled_with_known_variables_only(lf):
    lf.prompts["heating/system"] = {
        "type": "text", "version": 2,
        "prompt": 'Max {{max_candidates}}. {{vocabulary}} Format: {"candidates": [{{unbekannt}}]}'}
    resolved = await registry.resolve_all(PROMPT_SPECS)
    text = resolved["system"].compile(max_candidates=5, vocabulary="Wort {{max_candidates}}")
    assert text == 'Max 5. Wort {{max_candidates}} Format: {"candidates": [{{unbekannt}}]}'


@pytest.mark.parametrize("prompt", [
    {"type": "text", "prompt": "  ", "version": 2},
    {"type": "chat", "prompt": [], "version": 2},
    {"type": "text", "prompt": "ohne Variablen", "version": 2},       # Variablen gelöscht
])
async def test_unusable_prompts_fall_back_to_code(lf, prompt):
    lf.prompts["heating/system"] = prompt
    resolved = await registry.resolve_all(PROMPT_SPECS)
    assert resolved["system"].source == "code" and "{{max_candidates}}" in resolved["system"].text


async def test_langfuse_prompt_errors_fall_back_to_code(lf):
    lf.prompts_status = 500
    resolved = await registry.resolve_all(PROMPT_SPECS)
    assert all(r.source == "code" for r in resolved.values())


async def test_run_uses_langfuse_prompt_and_records_its_version(db, run_cfg, fake_llm, lf):
    lf.prompts["heating/mode-weekly"] = {"type": "text", "prompt": "LANGFUSE-ANWEISUNG", "version": 3}
    lf.prompts["heating/system"] = {"type": "text", "version": 7, "prompt": SYSTEM_PROMPT.replace(
        "{max_candidates}", "{{max_candidates}}").replace("{vocabulary}", "{{vocabulary}}")}
    fake_llm.responses = [answer(HABIT_TIME)]
    summary = await run_analysis(db, run_cfg, "weekly", api_key="geheim")

    sent = fake_llm.requests[0]["payload"]["messages"]
    assert sent[1]["content"].startswith("LANGFUSE-ANWEISUNG")
    (generation,) = lf.observations("generation")
    assert generation["attrs"]["langfuse.observation.prompt.name"] == "heating/system"
    assert generation["attrs"]["langfuse.observation.prompt.version"] == 7
    from analysis.store import Store
    row = Store(db).conn.execute("SELECT params FROM analysis_runs WHERE id = ?", (summary.run_id,)).fetchone()
    assert json.loads(row[0])["prompt_versionen"] == {"system": 7, "weekly": 3}


def test_dataclass_info_marks_only_langfuse_prompts_as_linked():
    code = registry._from_code(specs()["system"])
    assert dataclasses.is_dataclass(code) and code.info()["link"] is False
