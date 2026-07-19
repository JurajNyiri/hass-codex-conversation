"""Tests for the Codex API client and request serialization."""

from __future__ import annotations

from custom_components.codex_conversation.codex_api import (
    CODEX_ENDPOINT,
    AbstractAuth,
    CodexClient,
    CodexRequest,
    ImageGenerationCall,
)
from custom_components.codex_conversation.codex_api.models import CodexModel
from custom_components.codex_conversation.codex_api.sse import parse_event
from custom_components.codex_conversation.config_flow import (
    latest_available_model,
    reasoning_selector_options,
    recommended_options_from_models,
    service_tier_selector_options,
)
from custom_components.codex_conversation.const import CONF_MODEL, DEFAULT_MODEL


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.status = status
        self.headers = {}
        self._payload = payload
        self.released = False

    async def json(self) -> dict:
        return self._payload

    async def text(self) -> str:
        return str(self._payload)

    def release(self) -> None:
        self.released = True


class _FakeAuth(AbstractAuth):
    def __init__(self, response: _FakeResponse) -> None:
        super().__init__(session=None, endpoint=CODEX_ENDPOINT)  # type: ignore[arg-type]
        self.response = response
        self.calls: list[dict] = []

    async def async_get_access_token(self) -> str:
        return "token"

    async def request(self, method: str, endpoint: str | None = None, **kwargs):
        if endpoint is not None:
            kwargs["endpoint"] = endpoint
        self.calls.append({"method": method, **kwargs})
        return self.response


async def test_list_models_parses_available_models() -> None:
    """The client should parse the dynamic Codex model-list response."""
    response = _FakeResponse(
        {
            "models": [
                {
                    "slug": "gpt-current",
                    "display_name": "GPT Current",
                    "supported_reasoning_levels": [{"effort": "medium"}],
                    "default_reasoning_level": "medium",
                    "supports_reasoning_summaries": True,
                    "support_verbosity": True,
                    "supported_in_api": True,
                    "priority": 10,
                    "service_tiers": [
                        {"id": "priority", "name": "Fast", "description": "Faster"}
                    ],
                    "default_service_tier": "priority",
                },
                {"slug": "non-reasoning-model", "display_name": "Non Reasoning"},
            ]
        }
    )
    auth = _FakeAuth(response)

    models = await CodexClient(auth).list_models()

    assert [model.slug for model in models] == [
        "gpt-current",
        "non-reasoning-model",
    ]
    assert models[0].supports_reasoning is True
    assert models[0].support_verbosity is True
    assert models[0].supported_in_api is True
    assert models[0].priority == 10
    assert models[0].service_tiers[0].id == "priority"
    assert models[0].service_tiers[0].name == "Fast"
    assert models[0].default_service_tier == "priority"
    assert auth.calls[0]["method"] == "get"
    assert "/backend-api/codex/models" in auth.calls[0]["endpoint"]
    assert response.released is True


def test_request_uses_capabilities_for_non_codex_models() -> None:
    """Dynamic capability metadata should control optional request fields."""
    body = CodexRequest(
        model="non-reasoning-model",
        input=[],
        supports_reasoning=False,
        supports_reasoning_summaries=False,
        supports_text_verbosity=False,
    ).to_body()

    assert "reasoning" not in body
    assert "text" not in body
    assert "include" not in body


def test_parse_event_parses_image_generation_call() -> None:
    """Completed image-generation calls should expose generated image data."""
    event = parse_event(
        '{"type":"response.output_item.done","item":{'
        '"type":"image_generation_call",'
        '"id":"ig_123",'
        '"status":"completed",'
        '"revised_prompt":"A bright kitchen",'
        '"result":"aW1hZ2U="'
        "}}"
    )

    assert isinstance(event, ImageGenerationCall)
    assert event.id == "ig_123"
    assert event.status == "completed"
    assert event.revised_prompt == "A bright kitchen"
    assert event.result == "aW1hZ2U="


def test_request_omits_model_for_automatic_backend_default() -> None:
    """Automatic mode should let the Codex backend choose its current default."""
    body = CodexRequest(model=DEFAULT_MODEL, input=[]).to_body()

    assert "model" not in body
    assert "reasoning" not in body
    assert "text" not in body


def test_request_keeps_prefix_fallback_without_capabilities() -> None:
    """Existing entries without metadata should keep the previous gpt-5 behavior."""
    body = CodexRequest(model="gpt-5", input=[]).to_body()

    assert body["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert body["text"] == {"verbosity": "medium"}
    assert body["include"] == ["reasoning.encrypted_content"]


def test_request_serializes_non_default_service_tier() -> None:
    """A selected catalog speed tier should be sent to the backend."""
    body = CodexRequest(
        model="gpt-5", input=[], service_tier="priority"
    ).to_body()

    assert body["service_tier"] == "priority"


def test_dynamic_catalog_selector_options() -> None:
    """Reasoning and speed choices should come from live model metadata."""
    model = CodexModel.from_dict(
        {
            "slug": "gpt-future",
            "supported_reasoning_levels": [
                {"effort": "low"},
                {"effort": "xhigh"},
                {"effort": "ultra"},
            ],
            "service_tiers": [{"id": "priority", "name": "Fast"}],
        }
    )
    assert model is not None

    assert reasoning_selector_options([model], "medium") == [
        "low",
        "xhigh",
        "ultra",
        "medium",
    ]
    assert service_tier_selector_options([model], "default") == [
        {"value": "default", "label": "Standard"},
        {"value": "priority", "label": "Fast"},
    ]


def test_recommended_options_use_first_visible_api_model_without_priority() -> None:
    """When the API omits priority, preserve its order for maintenance-free defaults."""
    models = [
        CodexModel.from_dict({"slug": "gpt-future", "visibility": "list"}),
        CodexModel.from_dict({"slug": "gpt-current", "visibility": "list"}),
    ]

    options = recommended_options_from_models(
        {CONF_MODEL: "fallback"}, [model for model in models if model is not None]
    )

    assert options[CONF_MODEL] == "gpt-future"


def test_recommended_options_keep_automatic_when_discovery_has_no_models() -> None:
    """Without discovered models, keep automatic mode instead of hardcoding a slug."""
    options = recommended_options_from_models({CONF_MODEL: DEFAULT_MODEL}, [])

    assert options[CONF_MODEL] == DEFAULT_MODEL


def test_latest_available_model_preserves_backend_order() -> None:
    """The first visible API model is the maintenance-free default."""
    first = CodexModel.from_dict(
        {"slug": "first-model", "visibility": "list", "priority": 0}
    )
    second = CodexModel.from_dict(
        {"slug": "second-model", "visibility": "list", "priority": 99}
    )

    latest = latest_available_model(
        [model for model in [first, second] if model is not None]
    )

    assert latest is not None
    assert latest.slug == "first-model"
