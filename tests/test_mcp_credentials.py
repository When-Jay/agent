"""Credential resolution tests (mcp-gateway-spec.md section 9)."""

import pytest

from agent_platform.mcp.credentials import (
    CredentialMaterial,
    CredentialResolutionError,
    EnvCredentialResolver,
    credential_fingerprint,
)


def test_raw_token_becomes_bearer_header(monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIAL_GITHUB_MAIN", "tok-123")
    material = EnvCredentialResolver().resolve("github_main")
    assert material.headers == {"Authorization": "Bearer tok-123"}
    assert material.env == {}


def test_json_value_maps_headers_and_env(monkeypatch):
    monkeypatch.setenv(
        "MCP_CREDENTIAL_SVC",
        '{"headers": {"X-Api-Key": "k1"}, "env": {"SVC_TOKEN": "t1"}}',
    )
    material = EnvCredentialResolver().resolve("svc")
    assert material.headers == {"X-Api-Key": "k1"}
    assert material.env == {"SVC_TOKEN": "t1"}


def test_missing_reference_error_names_variable_not_value(monkeypatch):
    monkeypatch.delenv("MCP_CREDENTIAL_MISSING", raising=False)
    with pytest.raises(CredentialResolutionError, match="MCP_CREDENTIAL_MISSING"):
        EnvCredentialResolver().resolve("missing")


def test_invalid_json_error_does_not_leak_value(monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIAL_BROKEN", '{"headers": "tok-xyz')
    with pytest.raises(CredentialResolutionError) as excinfo:
        EnvCredentialResolver().resolve("broken")
    assert "tok-xyz" not in str(excinfo.value)
    assert "not valid credential JSON" in str(excinfo.value)


def test_fingerprint_is_stable_and_value_sensitive():
    a = CredentialMaterial(headers={"Authorization": "Bearer x"})
    b = CredentialMaterial(headers={"Authorization": "Bearer x"})
    c = CredentialMaterial(headers={"Authorization": "Bearer y"})
    assert credential_fingerprint(a) == credential_fingerprint(b)
    assert credential_fingerprint(a) != credential_fingerprint(c)
    assert len(credential_fingerprint(a)) == 16  # short digest, log-safe
