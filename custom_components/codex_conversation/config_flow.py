"""Config flow — Codex Device Code Auth."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import callback
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    TemplateSelector,
)
import voluptuous as vol

from .codex_api import CODEX_ENDPOINT, CodexAuth, CodexClient, CodexModel
from .codex_api.auth import VERIFICATION_URL, CodexDeviceFlow, OAuthToken
from .const import (
    CONF_MODEL,
    CONF_MODEL_SUPPORTS_REASONING,
    CONF_MODEL_SUPPORTS_REASONING_SUMMARIES,
    CONF_MODEL_SUPPORTS_TEXT_VERBOSITY,
    CONF_PROMPT,
    CONF_REASONING_EFFORT,
    CONF_REASONING_SUMMARY,
    CONF_RECOMMENDED,
    CONF_SERVICE_TIER,
    CONF_TEXT_VERBOSITY,
    DEFAULT_MODEL,
    DOMAIN,
    RECOMMENDED_AI_TASK_OPTIONS,
    RECOMMENDED_CONVERSATION_OPTIONS,
    RECOMMENDED_REASONING_EFFORT,
    RECOMMENDED_REASONING_SUMMARY,
    RECOMMENDED_SERVICE_TIER,
    RECOMMENDED_TEXT_VERBOSITY,
)
from .oauth import CodexHAAuth

_LOGGER = logging.getLogger(__name__)
AUTO_MODEL_LABEL = "Automatic (Codex default)"


def recommended_options_from_models(
    defaults: dict[str, Any], models: list[CodexModel]
) -> dict[str, Any]:
    """Return recommended options using live model discovery when available."""
    data = defaults.copy()
    if model := latest_available_model(models):
        data[CONF_MODEL] = model.slug
        apply_model_capabilities(data, model)
        apply_model_defaults(data, model)
    return data


def latest_available_model(models: list[CodexModel]) -> CodexModel | None:
    """Return the first visible model in the backend-provided order."""
    return next(
        (model for model in models if model.visibility.lower() not in {"hide", "none"}),
        models[0] if models else None,
    )


def sort_models_by_recommendation(models: list[CodexModel]) -> list[CodexModel]:
    """Preserve the backend-provided model order."""
    return models


def apply_model_capabilities(data: dict[str, Any], model: CodexModel) -> None:
    """Persist selected model capabilities for request serialization."""
    data[CONF_MODEL_SUPPORTS_REASONING] = model.supports_reasoning
    data[CONF_MODEL_SUPPORTS_REASONING_SUMMARIES] = model.supports_reasoning_summaries
    data[CONF_MODEL_SUPPORTS_TEXT_VERBOSITY] = model.support_verbosity


def apply_model_defaults(data: dict[str, Any], model: CodexModel) -> None:
    """Apply defaults advertised by a discovered model."""
    if model.default_reasoning_level:
        data[CONF_REASONING_EFFORT] = model.default_reasoning_level
    if model.default_verbosity:
        data[CONF_TEXT_VERBOSITY] = model.default_verbosity
    data[CONF_SERVICE_TIER] = model.default_service_tier or RECOMMENDED_SERVICE_TIER


def reasoning_selector_options(models: list[CodexModel], current: str) -> list[str]:
    """Return live catalog reasoning efforts, preserving configured values."""
    values = [effort for model in models for effort in model.supported_reasoning_levels]
    if not values:
        values = ["low", "medium", "high"]
    return list(dict.fromkeys([*values, current]))


def service_tier_selector_options(
    models: list[CodexModel], current: str
) -> list[dict[str, str]]:
    """Return live catalog speed tiers, preserving configured values."""
    options = {"default": "Standard"}
    for model in models:
        for tier in model.service_tiers:
            options.setdefault(tier.id, tier.name)
    options.setdefault(current, current.replace("_", " ").title())
    return [{"value": value, "label": label} for value, label in options.items()]


class CodexConversationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow: request device code, show URL + code, wait for approval."""

    VERSION = 1

    def __init__(self) -> None:
        self._flow: CodexDeviceFlow | None = None
        self._user_code: str = ""
        self._auth_task: asyncio.Task[OAuthToken] | None = None
        self._token: OAuthToken | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Request a device code from Codex API."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        try:
            session = async_get_clientsession(self.hass)
            self._flow = CodexDeviceFlow(session)
            info = await self._flow.initialize()
            self._user_code = info.user_code
        except Exception:
            _LOGGER.exception("Failed to request device code")
            return self.async_show_form(
                step_id="user",
                errors={"base": "token_exchange_failed"},
                data_schema=vol.Schema({}),
            )

        return await self.async_step_activate()

    async def async_step_activate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show verification URL/code and wait for authorization."""
        if not self._auth_task:
            self._auth_task = self._flow.wait_authorization(timeout=900)

        if not self._auth_task.done():
            return self.async_show_progress(
                step_id="activate",
                progress_action="waiting_for_auth",
                description_placeholders={
                    "url": VERIFICATION_URL,
                    "code": self._user_code or "—",
                },
                progress_task=self._auth_task,
            )

        try:
            self._token = self._auth_task.result()
            _LOGGER.info("Device code flow succeeded.")
        except Exception:
            _LOGGER.exception("Device code flow failed")
            return self.async_abort(reason="oauth_error")

        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create entry with auth data only and default subentries."""
        models = await self._async_get_initial_models()

        return self.async_create_entry(
            title="OpenAI Codex",
            data={"auth_implementation": DOMAIN, "token": self._token.as_dict()},
            subentries=[
                {
                    "subentry_type": "conversation",
                    "data": recommended_options_from_models(
                        RECOMMENDED_CONVERSATION_OPTIONS, models
                    ),
                    "title": "Codex Conversation",
                    "unique_id": None,
                },
                {
                    "subentry_type": "ai_task_data",
                    "data": recommended_options_from_models(
                        RECOMMENDED_AI_TASK_OPTIONS, models
                    ),
                    "title": "Codex AI Task",
                    "unique_id": None,
                },
            ],
        )

    async def _async_get_initial_models(self) -> list[CodexModel]:
        """Fetch live models after OAuth so initial entries use the latest model."""
        if self._token is None:
            return []

        try:
            auth = CodexAuth(
                async_get_clientsession(self.hass),
                CODEX_ENDPOINT,
                self._token.access_token,
                self._token.account_id,
            )
            return await CodexClient(auth).list_models()
        except Exception:
            _LOGGER.exception("Failed to fetch initial Codex models")
            return []

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {
            "conversation": CodexConversationSubentryFlow,
            "ai_task_data": CodexAITaskSubentryFlow,
        }


class _BaseCodexSubentryFlow(ConfigSubentryFlow):
    """Base flow for Codex subentries using model settings."""

    options: dict[str, Any]
    _init_data: dict[str, Any]
    _models: list[CodexModel]

    @property
    def _is_new(self) -> bool:
        """Return if this is a new subentry."""
        return self.source == "user"

    @property
    def _default_data(self) -> dict[str, Any]:
        """Default data for a new subentry."""
        raise NotImplementedError

    @property
    def _supports_prompt_and_apis(self) -> bool:
        """Whether this subentry has prompt and Home Assistant controls."""
        return False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle creation of a new subentry."""
        self.options = self._default_data.copy()
        self._init_data = {}
        self._models = []
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguration of an existing subentry."""
        self.options = self._get_reconfigure_subentry().data.copy()
        self._init_data = {}
        self._models = []
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Manage initial options."""
        if self._get_entry().state != ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        options = self.options

        if user_input is not None:
            if user_input[CONF_RECOMMENDED]:
                data = await self._async_recommended_data()
                if self._supports_prompt_and_apis:
                    data[CONF_PROMPT] = user_input.get(
                        CONF_PROMPT, llm.DEFAULT_INSTRUCTIONS_PROMPT
                    )
                    data[CONF_LLM_HASS_API] = user_input.get(CONF_LLM_HASS_API) or []
                return self._finalize_subentry(data)

            self._init_data = user_input
            return await self.async_step_advanced()

        if self._supports_prompt_and_apis:
            hass_apis = [
                {"value": api.id, "label": api.name}
                for api in llm.async_get_apis(self.hass)
            ]
            step_schema: dict = {
                vol.Optional(
                    CONF_PROMPT,
                    description={
                        "suggested_value": options.get(
                            CONF_PROMPT, llm.DEFAULT_INSTRUCTIONS_PROMPT
                        )
                    },
                ): TemplateSelector(),
                vol.Optional(CONF_LLM_HASS_API): SelectSelector(
                    SelectSelectorConfig(options=hass_apis, multiple=True)
                ),
                vol.Required(
                    CONF_RECOMMENDED,
                    default=options.get(CONF_RECOMMENDED, True),
                ): bool,
            }
        else:
            step_schema = {
                vol.Required(
                    CONF_RECOMMENDED,
                    default=options.get(CONF_RECOMMENDED, True),
                ): bool,
            }

        return self.async_show_form(step_id="init", data_schema=vol.Schema(step_schema))

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Manage advanced options."""
        options = self.options

        if user_input is not None:
            data = self._with_model_capabilities({**self._init_data, **user_input})
            return self._finalize_subentry(data)

        models = await self._async_get_models()
        if not options.get(CONF_MODEL) and (model := latest_available_model(models)):
            options[CONF_MODEL] = model.slug
            apply_model_defaults(options, model)
        model_options = self._model_selector_options(models)
        reasoning_default = options.get(
            CONF_REASONING_EFFORT, RECOMMENDED_REASONING_EFFORT
        )
        service_tier_default = options.get(CONF_SERVICE_TIER, RECOMMENDED_SERVICE_TIER)

        return self.async_show_form(
            step_id="advanced",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MODEL,
                        default=options.get(CONF_MODEL, DEFAULT_MODEL),
                    ): SelectSelector(SelectSelectorConfig(options=model_options)),
                    vol.Required(
                        CONF_REASONING_EFFORT,
                        default=reasoning_default,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=reasoning_selector_options(
                                models, reasoning_default
                            )
                        )
                    ),
                    vol.Required(
                        CONF_REASONING_SUMMARY,
                        default=options.get(
                            CONF_REASONING_SUMMARY, RECOMMENDED_REASONING_SUMMARY
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=["auto", "short", "detailed", "off"]
                        )
                    ),
                    vol.Required(
                        CONF_TEXT_VERBOSITY,
                        default=options.get(
                            CONF_TEXT_VERBOSITY, RECOMMENDED_TEXT_VERBOSITY
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(options=["low", "medium", "high"])
                    ),
                    vol.Required(
                        CONF_SERVICE_TIER,
                        default=service_tier_default,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=service_tier_selector_options(
                                models, service_tier_default
                            )
                        )
                    ),
                }
            ),
        )

    async def _async_recommended_data(self) -> dict[str, Any]:
        """Return recommended data using the latest available account model."""
        data = self._default_data.copy()
        models = await self._async_get_models()
        if model := latest_available_model(models):
            data[CONF_MODEL] = model.slug
            apply_model_defaults(data, model)
        return self._with_model_capabilities(data)

    async def _async_get_models(self) -> list[CodexModel]:
        """Fetch live model metadata, falling back to the bundled defaults."""
        models = getattr(self, "_models", [])
        if models:
            return models
        self._models = []

        entry = self._get_entry()
        oauth_session = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if oauth_session is not None:
            try:
                auth = CodexHAAuth(
                    session=async_get_clientsession(self.hass),
                    oauth_session=oauth_session,
                )
                self._models = await CodexClient(auth).list_models()
            except Exception:
                _LOGGER.exception("Failed to fetch Codex models; using fallback list")

        if not self._models:
            return []

        return self._models

    def _model_selector_options(self, models: list[CodexModel]) -> list[dict[str, str]]:
        """Build selector options and preserve the currently configured model."""
        current = self.options.get(CONF_MODEL, DEFAULT_MODEL)
        by_slug = {model.slug: model for model in models}
        if current and current not in by_slug:
            by_slug[current] = CodexModel(slug=current, display_name=current)

        visible_models = [
            model
            for model in by_slug.values()
            if model.slug == current or model.visibility.lower() not in {"hide", "none"}
        ]

        options = [
            {"value": model.slug, "label": model.display_name or model.slug}
            for model in sort_models_by_recommendation(visible_models)
        ]
        if not current:
            return [{"value": DEFAULT_MODEL, "label": AUTO_MODEL_LABEL}, *options]
        return options or [{"value": DEFAULT_MODEL, "label": AUTO_MODEL_LABEL}]

    def _with_model_capabilities(self, data: dict[str, Any]) -> dict[str, Any]:
        """Persist selected model capabilities for request serialization."""
        model = next(
            (
                item
                for item in getattr(self, "_models", [])
                if item.slug == data.get(CONF_MODEL)
            ),
            None,
        )
        if model is None:
            return data
        if not model.capabilities_known:
            for key in (
                CONF_MODEL_SUPPORTS_REASONING,
                CONF_MODEL_SUPPORTS_REASONING_SUMMARIES,
                CONF_MODEL_SUPPORTS_TEXT_VERBOSITY,
            ):
                data.pop(key, None)
            return data

        apply_model_capabilities(data, model)
        return data

    def _finalize_subentry(self, data: dict[str, Any]) -> SubentryFlowResult:
        """Create or update subentry depending on source."""
        model = data.get(CONF_MODEL, DEFAULT_MODEL)
        title = f"Codex ({model or AUTO_MODEL_LABEL})"

        if self._is_new:
            return self.async_create_entry(title=title, data=data)

        return self.async_update_and_abort(
            self._get_entry(),
            self._get_reconfigure_subentry(),
            data=data,
            title=title,
        )


class CodexConversationSubentryFlow(_BaseCodexSubentryFlow):
    """Flow for Codex conversation subentries."""

    @property
    def _default_data(self) -> dict[str, Any]:
        return RECOMMENDED_CONVERSATION_OPTIONS

    @property
    def _supports_prompt_and_apis(self) -> bool:
        return True


class CodexAITaskSubentryFlow(_BaseCodexSubentryFlow):
    """Flow for Codex AI task subentries."""

    @property
    def _default_data(self) -> dict[str, Any]:
        return RECOMMENDED_AI_TASK_OPTIONS
