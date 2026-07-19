"""AI Task platform for the Codex integration."""

from __future__ import annotations

import base64
import binascii
from json import JSONDecodeError
import logging

from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.json import json_loads

from .codex_api import CodexClient, ImageGenerationCall
from .const import (
    CONF_MODEL,
    CONF_MODEL_SUPPORTS_REASONING,
    CONF_MODEL_SUPPORTS_REASONING_SUMMARIES,
    CONF_MODEL_SUPPORTS_TEXT_VERBOSITY,
    CONF_REASONING_EFFORT,
    CONF_REASONING_SUMMARY,
    CONF_SERVICE_TIER,
    CONF_TEXT_VERBOSITY,
    DEFAULT_MODEL,
    DOMAIN,
    RECOMMENDED_REASONING_EFFORT,
    RECOMMENDED_REASONING_SUMMARY,
    RECOMMENDED_SERVICE_TIER,
    RECOMMENDED_TEXT_VERBOSITY,
)
from .conversation import async_run_chat_log
from .oauth import CodexHAAuth

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Codex AI task entities."""
    session: config_entry_oauth2_flow.OAuth2Session = hass.data[DOMAIN][entry.entry_id]
    for subentry in entry.subentries.values():
        if subentry.subentry_type != "ai_task_data":
            continue
        async_add_entities(
            [CodexAITaskEntity(hass, entry, session, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class CodexAITaskEntity(ai_task.AITaskEntity):
    """AI Task entity backed by OpenAI Codex."""

    _attr_has_entity_name = True
    _attr_name = "AI Task"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        oauth_session: config_entry_oauth2_flow.OAuth2Session,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialize the entity."""
        self.hass = hass
        self._entry = entry
        self._subentry = subentry
        self._oauth_session = oauth_session
        self._attr_unique_id = subentry.subentry_id
        self._attr_name = subentry.title
        self._attr_supported_features = (
            ai_task.AITaskEntityFeature.GENERATE_DATA
            | ai_task.AITaskEntityFeature.GENERATE_IMAGE
            | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
        )

    @property
    def _options(self) -> dict:
        return self._subentry.data

    @property
    def device_info(self) -> dict:
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "OpenAI Codex",
            "manufacturer": "OpenAI",
            "model": self._options.get(CONF_MODEL, DEFAULT_MODEL) or "Automatic",
        }

    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Handle a generate data task."""
        auth = CodexHAAuth(
            session=async_get_clientsession(self.hass),
            oauth_session=self._oauth_session,
        )
        client = CodexClient(auth)

        await async_run_chat_log(
            chat_log=chat_log,
            client=client,
            model=self._options.get(CONF_MODEL, DEFAULT_MODEL),
            entity_id=self.entity_id,
            reasoning_effort=self._options.get(
                CONF_REASONING_EFFORT, RECOMMENDED_REASONING_EFFORT
            ),
            reasoning_summary=self._options.get(
                CONF_REASONING_SUMMARY, RECOMMENDED_REASONING_SUMMARY
            ),
            text_verbosity=self._options.get(
                CONF_TEXT_VERBOSITY, RECOMMENDED_TEXT_VERBOSITY
            ),
            service_tier=self._options.get(CONF_SERVICE_TIER, RECOMMENDED_SERVICE_TIER),
            supports_reasoning=self._options.get(CONF_MODEL_SUPPORTS_REASONING),
            supports_reasoning_summaries=self._options.get(
                CONF_MODEL_SUPPORTS_REASONING_SUMMARIES
            ),
            supports_text_verbosity=self._options.get(
                CONF_MODEL_SUPPORTS_TEXT_VERBOSITY
            ),
            instructions_suffix=_format_structure_instruction(task),
            max_iterations=100,
        )

        if not isinstance(chat_log.content[-1], conversation.AssistantContent):
            raise HomeAssistantError(
                "Last content in chat log is not an AssistantContent"
            )

        text = chat_log.content[-1].content or ""
        if not task.structure:
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id,
                data=text,
            )

        try:
            data = json_loads(text)
        except JSONDecodeError as err:
            _LOGGER.error("Failed to parse JSON response: %s. Response: %s", err, text)
            raise HomeAssistantError("Error with Codex structured response") from err

        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id,
            data=data,
        )

    async def _async_generate_image(
        self,
        task: ai_task.GenImageTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenImageTaskResult:
        """Handle a generate image task."""
        auth = CodexHAAuth(
            session=async_get_clientsession(self.hass),
            oauth_session=self._oauth_session,
        )
        client = CodexClient(auth)
        image_calls: list[ImageGenerationCall] = []

        await async_run_chat_log(
            chat_log=chat_log,
            client=client,
            model=self._options.get(CONF_MODEL, DEFAULT_MODEL),
            entity_id=self.entity_id,
            reasoning_effort=self._options.get(
                CONF_REASONING_EFFORT, RECOMMENDED_REASONING_EFFORT
            ),
            reasoning_summary=self._options.get(
                CONF_REASONING_SUMMARY, RECOMMENDED_REASONING_SUMMARY
            ),
            text_verbosity=self._options.get(
                CONF_TEXT_VERBOSITY, RECOMMENDED_TEXT_VERBOSITY
            ),
            service_tier=self._options.get(CONF_SERVICE_TIER, RECOMMENDED_SERVICE_TIER),
            supports_reasoning=self._options.get(CONF_MODEL_SUPPORTS_REASONING),
            supports_reasoning_summaries=self._options.get(
                CONF_MODEL_SUPPORTS_REASONING_SUMMARIES
            ),
            supports_text_verbosity=self._options.get(
                CONF_MODEL_SUPPORTS_TEXT_VERBOSITY
            ),
            extra_tools=[{"type": "image_generation", "output_format": "png"}],
            image_generation_calls=image_calls,
            instructions_suffix="Use the image_generation tool to generate the requested image.",
            max_iterations=100,
        )

        image_call = _latest_image_call_with_result(image_calls)
        if image_call is None:
            raise HomeAssistantError("No image returned")

        try:
            image_data = base64.b64decode(image_call.result, validate=True)
        except (binascii.Error, ValueError) as err:
            raise HomeAssistantError("Invalid image data returned by Codex") from err

        return ai_task.GenImageTaskResult(
            image_data=image_data,
            conversation_id=chat_log.conversation_id,
            mime_type=_detect_image_mime_type(image_data),
            model=self._options.get(CONF_MODEL, DEFAULT_MODEL) or None,
            revised_prompt=image_call.revised_prompt,
        )


def _format_structure_instruction(task: ai_task.GenDataTask) -> str:
    """Build extra instructions for structured output."""
    if not task.structure:
        return ""

    field_names = [str(key) for key in task.structure.schema]
    if not field_names:
        return "Return valid JSON."

    fields = ", ".join(field_names)
    return (
        f"Return only valid JSON. The JSON object must contain these fields: {fields}."
    )


def _latest_image_call_with_result(
    image_calls: list[ImageGenerationCall],
) -> ImageGenerationCall | None:
    """Return the newest image call with result data."""
    for image_call in reversed(image_calls):
        if image_call.result:
            return image_call
    return None


def _detect_image_mime_type(image_data: bytes) -> str:
    """Return the image MIME type from magic bytes."""
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
        return "image/webp"
    if image_data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return "application/octet-stream"
