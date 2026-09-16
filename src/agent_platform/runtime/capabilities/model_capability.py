"""Model capability interface.

Runtime execution logic depends on this abstraction only; concrete
provider adapters (OpenAI, Anthropic, LiteLLM, ...) implement it.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from agent_platform.runtime.capabilities.model import ModelMessage, ModelResponse
from agent_platform.runtime.capabilities.tool import ToolSpec


class ModelCapability(ABC):
    """Chat-completion abstraction used by execution runtimes."""

    @property
    @abstractmethod
    def metadata(self) -> dict[str, Any]:
        """Provider/model descriptors for observability and routing."""

    @abstractmethod
    def chat(
        self,
        messages: Sequence[ModelMessage],
        *,
        tools: Sequence[ToolSpec] = (),
    ) -> ModelResponse:
        """Run one completion turn and return the model response."""

    # Reserved extension points (see runtime-capabilities-spec.md):
    # stream, structured_output, embeddings, provider routing, fallback.
