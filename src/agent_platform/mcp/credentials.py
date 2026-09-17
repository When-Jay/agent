"""Credential resolution for MCP servers (mcp-gateway-spec.md section 9).

Credentials are declared by reference (``credential_ref``) in server
config and resolved at connection time by the composition root. The
gateway never sees material; error messages name the reference, never
the resolved values (redaction rules, section 9.3).
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Protocol

from agent_platform.errors import PlatformError


class CredentialResolutionError(PlatformError):
    """Raised when a credential reference cannot be resolved."""


@dataclass(frozen=True)
class CredentialMaterial:
    """Resolved credential material, injected at transport boundaries.

    ``headers`` ride on HTTP requests; ``env`` is reserved for stdio
    servers inside sandboxes (spec section 6, Stage D).
    """

    headers: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CredentialContext:
    """Requesting identity for per-user delegation.

    Reserved: V2-C resolves platform identity only (context=None);
    propagating user identity through ToolCallRequest is deferred.
    """

    user_id: str | None = None
    session_id: str | None = None


class CredentialResolver(Protocol):
    def resolve(
        self, ref: str, context: CredentialContext | None = None
    ) -> CredentialMaterial: ...


def credential_fingerprint(material: CredentialMaterial) -> str:
    """Stable short digest identifying resolved material.

    Used as the session cache key component (spec section 9.1); the
    fingerprint is safe to log, the material is not.
    """
    raw = json.dumps(
        {
            "headers": sorted(material.headers.items()),
            "env": sorted(material.env.items()),
        },
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


class EnvCredentialResolver:
    """Environment-backed resolver (development and self-hosted).

    Reads ``MCP_CREDENTIAL_<REF>`` (non-alphanumeric characters in the
    reference become underscores). The value is either a JSON object
    ``{"headers": {...}, "env": {...}}`` or a raw token, which becomes
    an ``Authorization: Bearer <token>`` header.
    """

    prefix = "MCP_CREDENTIAL_"

    def resolve(
        self, ref: str, context: CredentialContext | None = None
    ) -> CredentialMaterial:
        variable = self.prefix + re.sub(r"[^A-Z0-9]", "_", ref.upper())
        raw = os.getenv(variable)
        if not raw:
            raise CredentialResolutionError(
                f"credential {ref!r} not found; set the {variable} environment variable"
            )
        if raw.lstrip().startswith("{"):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                # Position/format details only; the raw value never appears.
                raise CredentialResolutionError(
                    f"credential {ref!r} ({variable}) is not valid credential JSON "
                    f"(line {exc.lineno}, column {exc.colno})"
                ) from None
            if not isinstance(data, dict):
                raise CredentialResolutionError(
                    f"credential {ref!r} ({variable}) must be a JSON object"
                )
            headers = {str(k): str(v) for k, v in data.get("headers", {}).items()}
            env = {str(k): str(v) for k, v in data.get("env", {}).items()}
            return CredentialMaterial(headers=headers, env=env)
        return CredentialMaterial(headers={"Authorization": f"Bearer {raw}"})
