"""Tests for the Codex API client and request serialization."""

from __future__ import annotations

from custom_components.codex_conversation.codex_api import (
    CODEX_ENDPOINT,
    AbstractAuth,
    CodexClient,
    CodexRequest,
)
from custom_components.codex_conversation.codex_api.models import CodexModel
from custom_components.codex_conversation.config_flow import (
    latest_available_model,
    recommended_options_from_models,
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
                    "supports_reasoning_summaries": True,
                    "support_verbosity": True,
                    "supported_in_api": True,
                    "priority": 10,
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


def test_latest_available_model_prefers_backend_priority() -> None:
    """Backend priority should override raw order when provided."""
    older = CodexModel.from_dict(
        {"slug": "gpt-older", "visibility": "list", "priority": 1}
    )
    newer = CodexModel.from_dict(
        {"slug": "gpt-newer", "visibility": "list", "priority": 10}
    )

    latest = latest_available_model(
        [model for model in [older, newer] if model is not None]
    )

    assert latest is not None
    assert latest.slug == "gpt-newer"
