"""
Typed event models for the Codex Responses API SSE stream.

Mirrors the ``ResponseEvent`` enum from codex-api/src/sse/responses.rs.
Each dataclass corresponds to one SSE event type emitted by the server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union


@dataclass(frozen=True)
class CodexModel:
    """Model metadata returned by the Codex model-discovery endpoint."""

    slug: str
    display_name: str = ""
    description: str = ""
    visibility: str = ""
    supported_in_api: bool | None = None
    priority: int = 0
    supported_reasoning_levels: tuple[str, ...] = ()
    default_reasoning_level: str | None = None
    supports_reasoning_summaries: bool | None = None
    support_verbosity: bool | None = None
    default_verbosity: str | None = None
    service_tiers: tuple["CodexServiceTier", ...] = ()
    default_service_tier: str | None = None
    capabilities_known: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CodexModel | None":
        """Build a model entry from the loose upstream JSON shape."""
        if not isinstance(data, dict):
            return None

        slug = data.get("slug") or data.get("id") or data.get("model")
        if not isinstance(slug, str) or not slug:
            return None

        reasoning_levels = []
        for level in data.get("supported_reasoning_levels") or []:
            if isinstance(level, dict) and isinstance(level.get("effort"), str):
                reasoning_levels.append(level["effort"])
            elif isinstance(level, str):
                reasoning_levels.append(level)

        service_tiers = []
        for tier in data.get("service_tiers") or []:
            if isinstance(tier, dict) and isinstance(tier.get("id"), str):
                service_tiers.append(
                    CodexServiceTier(
                        id=tier["id"],
                        name=str(tier.get("name") or tier["id"]),
                        description=str(tier.get("description") or ""),
                    )
                )
            elif isinstance(tier, str):
                service_tiers.append(CodexServiceTier(id=tier, name=tier))

        return cls(
            slug=slug,
            display_name=str(
                data.get("display_name") or data.get("displayName") or slug
            ),
            description=str(data.get("description") or ""),
            visibility=str(data.get("visibility") or ""),
            supported_in_api=_bool_or_none(data.get("supported_in_api")),
            priority=_int_or_default(data.get("priority")),
            supported_reasoning_levels=tuple(reasoning_levels),
            default_reasoning_level=data.get("default_reasoning_level"),
            supports_reasoning_summaries=_bool_or_none(
                data.get("supports_reasoning_summaries")
            ),
            support_verbosity=_bool_or_none(data.get("support_verbosity")),
            default_verbosity=data.get("default_verbosity"),
            service_tiers=tuple(service_tiers),
            default_service_tier=data.get("default_service_tier"),
            capabilities_known=True,
        )

    @property
    def supports_reasoning(self) -> bool:
        """Return whether the endpoint says this model supports reasoning controls."""
        return bool(self.supported_reasoning_levels)


@dataclass(frozen=True)
class CodexServiceTier:
    """A selectable request-speed tier advertised by the model catalog."""

    id: str
    name: str
    description: str = ""


def _bool_or_none(value: Any) -> bool | None:
    """Return booleans from loose JSON without treating missing values as false."""
    return value if isinstance(value, bool) else None


def _int_or_default(value: Any) -> int:
    """Return integer metadata from loose JSON."""
    return value if isinstance(value, int) else 0


@dataclass
class ResponseCreated:
    """Response object has been created; stream is starting."""

    response_id: str


@dataclass
class OutputItemAdded:
    """A new output item has been added to the response."""

    item: dict[str, Any]


@dataclass
class OutputTextDelta:
    """Incremental text chunk from the model — the main streaming payload."""

    delta: str
    content_index: int = 0


@dataclass
class ReasoningContentDelta:
    """Incremental encrypted reasoning content chunk."""

    delta: str


@dataclass
class ReasoningSummaryDelta:
    """Incremental reasoning-summary text chunk (human-readable chain-of-thought)."""

    delta: str
    summary_index: int = 0


@dataclass
class OutputItemDone:
    """An output item has finished streaming."""

    item: dict[str, Any]


@dataclass
class ResponseCompleted:
    """The full response has completed successfully."""

    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class RateLimits:
    """Rate-limit metadata received from the server."""

    data: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FunctionCallAdded:
    """A function-call output item has been added to the response."""

    call_id: str
    name: str
    item_id: str


@dataclass
class FunctionCallArgumentsDelta:
    """Incremental chunk of function-call arguments JSON."""

    delta: str
    item_id: str


@dataclass
class FunctionCallArgumentsDone:
    """Function-call arguments streaming completed; ``arguments`` is the full JSON string."""

    arguments: str
    item_id: str


@dataclass
class ImageGenerationCall:
    """Completed image-generation tool call returned by the Responses API."""

    id: str
    status: str
    result: str
    revised_prompt: str | None = None


# Convenience union — use with isinstance() checks
ResponseEvent = Union[
    ResponseCreated,
    OutputItemAdded,
    OutputTextDelta,
    ReasoningContentDelta,
    ReasoningSummaryDelta,
    OutputItemDone,
    ResponseCompleted,
    RateLimits,
    FunctionCallAdded,
    FunctionCallArgumentsDelta,
    FunctionCallArgumentsDone,
    ImageGenerationCall,
]
