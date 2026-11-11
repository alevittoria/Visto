"""Anthropic and Gemini wire checks with fakes only; these do not evaluate model judgement."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO

import pytest

from visto.adapters.ai import (
    AGENTS,
    PROVIDERS,
    AnthropicAgent,
    GeminiAgent,
    OpenAIAgent,
    http_worker,
)
from visto.adapters.ai.agent import INSTRUCTION_VERSION
from visto.adapters.ai.anthropic import OUTPUT_SCHEMA as ANTHROPIC_SCHEMA
from visto.adapters.ai.gemini import OUTPUT_SCHEMA as GEMINI_SCHEMA
from visto.adapters.ai.imaging import CLAUDE_HIGH_LIMITS, CLAUDE_STANDARD_LIMITS
from visto.adapters.ai.schema import SCHEMA, without_keywords
from visto.application.context import AgentContext, PresentedImage
from visto.application.ports import PortError
from visto.domain.commands import CommandKind
from visto.domain.models import (
    ArtifactRef,
    Conclude,
    Goal,
    ModelIdentity,
    Observation,
    RunConfiguration,
    RunLimits,
)
from visto.domain.run import Run


def make_context(provider, model, width=100, height=200):
    now = datetime.now(UTC)
    run = Run(
        "r",
        "serial",
        Goal("Open the fictional notes app", ()),
        RunConfiguration(
            RunLimits(30000, 4, 6, 1000, 500),
            tuple(CommandKind),
            ModelIdentity(provider, model, INSTRUCTION_VERSION),
            "appium-v1",
        ),
        now,
        now,
    ).start(now)
    content = b"fake screenshot"
    artifact = ArtifactRef(
        "a", "r", "image/png", len(content), hashlib.sha256(content).hexdigest(), "owned/key"
    )
    o = Observation("o", "r", 1, now, "serial", "session", "lease", artifact, width, height, 0)
    return AgentContext(run, (PresentedImage(o, content),), "o")


DECISION = {
    "rationale": "Visible evidence",
    "working_plan": "Inspect",
    "previous_effect": None,
    "next": {
        "kind": "conclude",
        "verdict": "inconclusive",
        "explanation": "Insufficient evidence",
        "observation_ids": ["o"],
        "findings": [],
    },
}


def claude_reply(stop="end_turn", text=None):
    return {
        "stop_reason": stop,
        "content": [
            {"type": "thinking", "thinking": ""},
            {"type": "text", "text": json.dumps(DECISION) if text is None else text},
        ],
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }


def gemini_reply(status="completed", parts=None):
    text = json.dumps(DECISION)
    parts = [{"type": "text", "text": text}] if parts is None else parts
    return {
        "status": status,
        "steps": [{"type": "thought"}, {"type": "model_output", "content": parts}],
        "usage": {"total_input_tokens": 13, "total_output_tokens": 5},
    }


def run_agent(agent_type, model, reply, context=None):
    sent = []

    def wire(key, body, timeout):
        sent.append(json.loads(body))
        return reply

    context = context or make_context(agent_type.provider, model)
    result = asyncio.run(agent_type("secret", model, transport=wire).decide(context, 1000))
    return result, sent[0]


def keywords(schema):
    found = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties":
                for child in value.values():
                    found |= keywords(child)
            else:
                found.add(key)
                found |= keywords(value)
    elif isinstance(schema, list):
        for item in schema:
            found |= keywords(item)
    return found


def test_every_catalog_provider_has_an_agent_and_valid_defaults():
    assert set(AGENTS) == set(PROVIDERS)
    for entry in PROVIDERS.values():
        assert entry.default_model == entry.models[0]
        assert len(set(entry.models)) == len(entry.models)


def test_anthropic_request_is_stateless_structured_and_carries_images():
    reply, sent = run_agent(AnthropicAgent, "claude-opus-5-5", claude_reply())
    assert isinstance(reply.decision.next, Conclude)
    assert reply.decision.usage.input_tokens == 11 and reply.decision.usage.output_tokens == 7
    assert sent["model"] == "claude-opus-5-5" and sent["max_tokens"] == 16000
    assert "Open the fictional notes app" in sent["messages"][0]["content"][0]["text"]
    image = sent["messages"][0]["content"][2]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/png"
    assert sent["output_config"]["format"] == {"type": "json_schema", "schema": ANTHROPIC_SCHEMA}
    assert sent["system"] and "temperature" not in sent and "thinking" not in sent


def test_provider_schemas_drop_only_unsupported_bounds():
    assert keywords(ANTHROPIC_SCHEMA).isdisjoint({"minimum", "maximum", "maxLength", "maxItems"})
    assert "minItems" in keywords(ANTHROPIC_SCHEMA)
    assert "maxLength" not in keywords(GEMINI_SCHEMA) and "maximum" in keywords(GEMINI_SCHEMA)
    assert without_keywords(SCHEMA, frozenset()) == SCHEMA
    # Property names are data: a property called like a keyword survives.
    shaped = {"type": "object", "properties": {"maximum": {"type": "integer", "maximum": 3}}}
    assert without_keywords(shaped, frozenset({"maximum"})) == {
        "type": "object",
        "properties": {"maximum": {"type": "integer"}},
    }


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (claude_reply("refusal"), "provider: refusal"),
        (claude_reply("max_tokens"), "provider: incomplete_or_failed_response"),
        ({"stop_reason": "end_turn", "content": []}, "provider: missing_or_multiple_decisions"),
        (claude_reply(text="not json"), "provider: malformed_response"),
    ],
)
def test_anthropic_failures_are_sanitized(reply, code):
    with pytest.raises(PortError, match=code):
        run_agent(AnthropicAgent, "claude-opus-5-5", reply)


def test_anthropic_images_fit_the_model_resolution_tier():
    assert AnthropicAgent("k", "claude-haiku-4-5").image_limits() == CLAUDE_STANDARD_LIMITS
    assert AnthropicAgent("k", "claude-sonnet-5-5").image_limits() == CLAUDE_HIGH_LIMITS
    # A screenshot above the tier is resized locally; these fake bytes cannot be.
    big = make_context("anthropic", "claude-haiku-4-5", 1080, 2400)
    with pytest.raises(PortError, match="unreadable_screenshot"):
        run_agent(AnthropicAgent, "claude-haiku-4-5", claude_reply(), big)


def test_gemini_request_is_stateless_structured_and_carries_images():
    reply, sent = run_agent(GeminiAgent, "gemini-4-argon", gemini_reply())
    assert isinstance(reply.decision.next, Conclude)
    assert reply.decision.usage.input_tokens == 13 and reply.decision.usage.output_tokens == 5
    assert sent["model"] == "gemini-4-argon" and sent["store"] is False
    assert sent["generation_config"] == {"max_output_tokens": 16000}
    assert sent["response_format"]["mime_type"] == "application/json"
    assert sent["response_format"]["schema"] == GEMINI_SCHEMA
    image = sent["input"][2]
    assert image["type"] == "image" and image["mime_type"] == "image/png" and image["data"]
    assert sent["system_instruction"]


def test_gemini_joins_split_text_and_rejects_incomplete_replies():
    text = json.dumps(DECISION)
    parts = [{"type": "text", "text": text[:10]}, {"type": "text", "text": text[10:]}]
    reply, _ = run_agent(GeminiAgent, "gemini-4-argon", gemini_reply(parts=parts))
    assert isinstance(reply.decision.next, Conclude)
    for bad, code in (
        (gemini_reply("incomplete"), "incomplete_or_failed_response"),
        (gemini_reply(parts=[]), "missing_or_multiple_decisions"),
    ):
        with pytest.raises(PortError, match=code):
            run_agent(GeminiAgent, "gemini-4-argon", bad)


@pytest.mark.parametrize("agent_type", [OpenAIAgent, AnthropicAgent, GeminiAgent])
def test_run_identity_must_name_the_agent_provider(agent_type):
    other = "gemini" if agent_type is not GeminiAgent else "openai"
    context = make_context(other, "model-a")
    agent = agent_type("k", "model-a", transport=lambda *a: pytest.fail("contacted"))
    with pytest.raises(PortError, match="identity_mismatch"):
        asyncio.run(agent.decide(context, 1000))


@pytest.mark.parametrize(
    ("provider", "url", "header", "value", "extra"),
    [
        ("openai", "https://api.openai.com/v1/responses", "Authorization", "Bearer k", None),
        (
            "anthropic",
            "https://api.anthropic.com/v1/messages",
            "X-api-key",
            "k",
            ("Anthropic-version", "2026-10-21"),
        ),
        (
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            "X-goog-api-key",
            "k",
            ("Api-revision", "2026-05-20"),
        ),
    ],
)
def test_worker_uses_fixed_endpoint_and_auth_per_provider(
    monkeypatch, provider, url, header, value, extra
):
    seen = []

    def fake(request, *, timeout):
        seen.append(request)
        return BytesIO(b"{}")

    monkeypatch.setattr(http_worker, "urlopen", fake)
    packet = {"provider": provider, "api_key": "k", "payload": "e30=", "timeout_ms": 1000}
    assert http_worker.handle_packet(json.dumps(packet).encode()) == {"response": {}}
    request = seen[0]
    assert request.full_url == url and request.get_header(header) == value
    if extra:
        assert request.get_header(extra[0]) == extra[1]
    others = {"Authorization", "X-api-key", "X-goog-api-key"} - {header}
    assert not any(request.has_header(name) for name in others)


def test_worker_probes_each_provider_model_endpoint(monkeypatch):
    seen = []

    def fake(request, *, timeout):
        seen.append(request)
        return BytesIO(b"{}")

    monkeypatch.setattr(http_worker, "urlopen", fake)
    for provider, model in (("anthropic", "claude-opus-5-5"), ("gemini", "gemini-4-argon")):
        packet = {"provider": provider, "api_key": "k", "probe_model": model, "timeout_ms": 1000}
        http_worker.handle_packet(json.dumps(packet).encode())
    assert [r.full_url for r in seen] == [
        "https://api.anthropic.com/v1/models/claude-opus-5-5",
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-4-argon",
    ]
    assert all(r.get_method() == "GET" for r in seen)


def test_worker_refuses_unknown_provider_and_non_openai_transcription(monkeypatch):
    monkeypatch.setattr(http_worker, "urlopen", lambda *a, **k: pytest.fail("network"))
    for packet in (
        {"provider": "other", "api_key": "k", "payload": "e30=", "timeout_ms": 1000},
        {"api_key": "k", "payload": "e30=", "timeout_ms": 1000},
        {
            "provider": "anthropic",
            "api_key": "k",
            "payload": "e30=",
            "timeout_ms": 1000,
            "media_type": "audio/webm",
        },
    ):
        result = http_worker.handle_packet(json.dumps(packet).encode())
        assert result == {"error": "provider: invalid_request"}


def attached_context(agent_type, model):
    from dataclasses import replace

    from visto.application.context import PresentedAttachment
    from visto.domain.models import AttachmentPage

    context = make_context(agent_type.provider, model)
    content = b"fictional layout"
    image = ArtifactRef(
        "img", "r", "image/png", len(content), hashlib.sha256(content).hexdigest(), "owned/img"
    )
    pages = (
        AttachmentPage("att-1", "fictional-flow.pdf", 1, 2, 100, 200, image),
        AttachmentPage("att-2", "fictional-flow.pdf", 2, 2, 100, 200, image),
    )
    run = replace(context.run, goal=replace(context.run.goal, attachments=pages))
    return replace(context, run=run, attachments=(PresentedAttachment(pages[1], content),))


@pytest.mark.parametrize(
    ("agent_type", "model", "reply"),
    [
        (OpenAIAgent, "gpt-6-sol", None),
        (AnthropicAgent, "claude-opus-5-5", claude_reply()),
        (GeminiAgent, "gemini-4-argon", gemini_reply()),
    ],
)
def test_request_lists_attachments_and_sends_presented_pages(agent_type, model, reply):
    if reply is None:
        reply = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(DECISION)}],
                }
            ],
        }
    result, sent = run_agent(agent_type, model, reply, attached_context(agent_type, model))
    assert result.decision.presented_attachment_ids == ("att-2",)
    body = json.dumps(sent)
    assert "owned/img" not in body  # Storage keys never leave the computer.
    metadata = json.loads(
        {
            OpenAIAgent: lambda: sent["input"][0]["content"][0]["text"],
            AnthropicAgent: lambda: sent["messages"][0]["content"][0]["text"],
            GeminiAgent: lambda: sent["input"][0]["text"],
        }[agent_type]()
    )
    assert [a["attachment_id"] for a in metadata["attachments"]] == ["att-1", "att-2"]
    assert metadata["attachments"][1] == {
        "attachment_id": "att-2",
        "name": "fictional-flow.pdf",
        "page": 2,
        "page_count": 2,
        "width": 100,
        "height": 200,
    }
    assert "attachments" not in metadata["original_goal"]
    # Observation image first, then the presented page with its own description.
    assert body.count('\\"attachment_id\\": \\"att-2\\"') >= 1
    assert body.count("base64") + body.count('"data"') >= 2


def test_view_attachments_and_findings_decode():
    from visto.domain.models import ViewAttachments

    context = attached_context(AnthropicAgent, "claude-opus-5-5")
    view = {**DECISION, "next": {"kind": "view_attachments", "attachment_ids": ["att-1"]}}
    reply, _ = run_agent(
        AnthropicAgent, "claude-opus-5-5", claude_reply(text=json.dumps(view)), context
    )
    assert reply.decision.next == ViewAttachments(("att-1",))
    concluded = json.loads(json.dumps(DECISION))
    concluded["next"]["findings"] = [
        {
            "explanation": "Share action missing in the top bar",
            "observation_ids": ["o"],
            "attachment_ids": ["att-2"],
        }
    ]
    reply, _ = run_agent(
        AnthropicAgent, "claude-opus-5-5", claude_reply(text=json.dumps(concluded)), context
    )
    (finding,) = reply.decision.next.assessment.findings
    assert finding.attachment_ids == ("att-2",) and finding.observation_ids == ("o",)


def test_conclusion_without_findings_key_is_rejected():
    # The schema is strict: every conclusion states its findings, possibly none.
    reply, _ = run_agent(AnthropicAgent, "claude-opus-5-5", claude_reply(text=json.dumps(DECISION)))
    assert reply.decision.next.assessment.findings == ()
    missing = json.loads(json.dumps(DECISION))
    del missing["next"]["findings"]
    with pytest.raises(PortError, match="malformed_response"):
        run_agent(AnthropicAgent, "claude-opus-5-5", claude_reply(text=json.dumps(missing)))


def test_schema_variants_stay_within_anthropic_union_limit():
    def unions(node):
        if isinstance(node, dict):
            return int("anyOf" in node) + sum(unions(v) for v in node.values())
        if isinstance(node, list):
            return sum(unions(v) for v in node)
        return 0

    assert unions(ANTHROPIC_SCHEMA) <= 16
