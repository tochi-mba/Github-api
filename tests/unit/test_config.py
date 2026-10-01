"""Configuration rules."""

from __future__ import annotations

import os

import pytest

from github_api.core.config import Settings, check_for_unknown_env_vars, load_settings
from tests.conftest import SERVICE_TOKEN, build_settings


def test_unknown_ghapi_variables_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHAPI_POLL_SECOND", "5")
    with pytest.raises(RuntimeError, match="GHAPI_POLL_SECOND"):
        check_for_unknown_env_vars()


def test_actions_own_github_api_url_is_not_ours_to_refuse() -> None:
    check_for_unknown_env_vars({"GITHUB_API_URL": "https://api.github.com", "GHAPI_PORT": "1"})


def test_a_bad_audience_is_refused() -> None:
    with pytest.raises(ValueError, match="GHAPI_AUDIENCE"):
        build_settings(audience="github.api")


def test_a_default_lifetime_longer_than_the_cap_is_refused() -> None:
    with pytest.raises(ValueError, match="GHAPI_SUBSCRIPTION_DEFAULT_SECONDS"):
        build_settings(subscription_default_seconds=10, subscription_max_seconds=5)


def test_a_placeholder_service_token_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 32 characters"):
        build_settings(keyring_service_token="short")


def test_load_settings_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("GHAPI_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GHAPI_KEYRING_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("GHAPI_ENVIRONMENT", "test")
    monkeypatch.setenv("GHAPI_SIGNAL_URL_PREFIXES", '["https://lucy.test/"]')
    monkeypatch.chdir(os.path.dirname(__file__))  # noqa: PTH120 - no .env here
    settings = load_settings()
    assert isinstance(settings, Settings)
    assert settings.environment == "test"
    assert settings.port == 8011
    assert settings.audience == "github-api"
    assert settings.github_base_url == "https://api.github.com"
    assert settings.signal_url_prefixes == ["https://lucy.test/"]
    assert SERVICE_TOKEN not in repr(settings)
