"""Re-export of the shared dataclass <-> JSON encoding.

The dump/load implementation is generic (evaluation/serialization.py);
evolution imports it rather than duplicating the encoder, keeping API
responses and store payloads on one encoding.
"""

from agent_platform.evaluation.serialization import dump, load

__all__ = ["dump", "load"]
