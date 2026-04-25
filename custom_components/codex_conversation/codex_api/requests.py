"""
Request model for the Codex Responses API.

Mirrors ``ResponsesApiRequest`` from codex-api/src/endpoint/responses.rs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CodexRequest:
    """
    Typed request for the Codex ``/responses`` endpoint.

    ``input`` is a list of message dicts in the OpenAI Responses API format::

        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "..."}]}
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "..."}]}

    ``instructions`` maps to the top-level field of the same name (system prompt).

    Reasoning parameters (``reasoning``, ``text``, ``include``) are only
    included in the serialised body for models that support them
    (``gpt-5*`` / ``o*`` series).
    """

    model: str
    input: list[dict[str, Any]]
    instructions: str = ""
    store: bool = False
    reasoning_effort: str = "medium"
    reasoning_summary: str = "auto"
    text_verbosity: str = "medium"
    tools: list[dict[str, Any]] = field(default_factory=list)
    supports_reasoning: bool | None = None
    supports_reasoning_summaries: bool | None = None
    supports_text_verbosity: bool | None = None

    def _is_reasoning_model(self) -> bool:
        if self.supports_reasoning is not None:
            return self.supports_reasoning
        return self.model.startswith(("gpt-5", "o"))

    def _supports_reasoning_summaries(self) -> bool:
        if self.supports_reasoning_summaries is not None:
            return self.supports_reasoning_summaries
        return self._is_reasoning_model()

    def _supports_text_verbosity(self) -> bool:
        if self.supports_text_verbosity is not None:
            return self.supports_text_verbosity
        return self._is_reasoning_model()

    def to_body(self) -> dict[str, Any]:
        """Serialise to the JSON body expected by the Codex endpoint."""
        body: dict[str, Any] = {
            "stream": True,
            "store": self.store,
            "input": self.input,
        }
        if self.model:
            body["model"] = self.model
        if self.instructions:
            body["instructions"] = self.instructions
        if self.tools:
            body["tools"] = self.tools
        if self._is_reasoning_model():
            body["reasoning"] = {"effort": self.reasoning_effort}
            if self._supports_reasoning_summaries():
                body["reasoning"]["summary"] = self.reasoning_summary
            body["include"] = ["reasoning.encrypted_content"]
        if self._supports_text_verbosity():
            body["text"] = {"verbosity": self.text_verbosity}
        return body
