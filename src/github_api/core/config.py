"""Application configuration.

Every knob is an environment variable prefixed ``GHAPI_``. Unknown variables under the
prefix are rejected rather than ignored, so a typo fails at startup instead of being a
setting that silently never applied.

The prefix is ``GHAPI_`` and not ``GITHUB_API_`` on purpose: GitHub Actions runners export
``GITHUB_API_URL`` into every job, and a service that refused unknown ``GITHUB_API_*``
variables would refuse to start in its own CI.
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Self

from keyring_client import check_service_token
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from collections.abc import Mapping

ENV_PREFIX = "GHAPI_"

PositiveInt = Annotated[int, Field(gt=0)]
PositiveFloat = Annotated[float, Field(gt=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]

WEEK_SECONDS = 7 * 24 * 3_600.0

# Parity looks for these string literals in source.
ROUTES = ("/healthy", "/ready")


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class Settings(BaseSettings):
    """The complete runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        hide_input_in_errors=True,
    )

    app_name: str = "github-api"
    environment: str = "local"
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.JSON
    host: str = "127.0.0.1"
    port: PositiveInt = 8011

    # -- who may call: keyring tokens minted for this audience
    keyring_jwks_url: str = "http://127.0.0.1:8001/.well-known/jwks.json"
    keyring_issuer: str = "http://127.0.0.1:8001"
    audience: str = "github-api"
    jwks_cache_seconds: PositiveFloat = 3_600.0
    jwks_min_refetch_seconds: PositiveFloat = 30.0
    keyring_timeout_seconds: PositiveFloat = 5.0

    # -- the caller's GitHub credential, resolved from keyring per request
    keyring_base_url: str = "http://127.0.0.1:8001"
    keyring_service_token: SecretStr
    keyring_credential_service: str = "github"
    credential_refresh_margin_seconds: NonNegativeFloat = 60.0
    credential_cache_seconds: PositiveFloat = 300.0
    credential_cache_entries: PositiveInt = 1_024

    # -- GitHub
    github_base_url: str = "https://api.github.com"
    github_graphql_url: str = "https://api.github.com/graphql"
    request_timeout_seconds: PositiveFloat = 15.0
    log_lines_max: PositiveInt = 500
    file_chars_max: PositiveInt = 100_000
    tree_entries_max: PositiveInt = 1_000
    patch_chars_max: PositiveInt = 6_000

    # -- subscriptions and the signals that end them
    database_path: str = "var/github-api.sqlite3"
    poll_seconds: NonNegativeFloat = 60.0
    subscription_default_seconds: PositiveFloat = 3_600.0
    subscription_max_seconds: PositiveFloat = WEEK_SECONDS
    subscriptions_per_account: PositiveInt = 50
    subscription_retention_seconds: PositiveFloat = WEEK_SECONDS
    credential_hold_seconds: PositiveFloat = 3_600.0
    signal_url_prefixes: list[str] = Field(default_factory=list)
    signal_timeout_seconds: PositiveFloat = 10.0

    @field_validator("keyring_service_token")
    @classmethod
    def _service_token_is_usable(cls, value: SecretStr) -> SecretStr:
        check_service_token(value.get_secret_value())
        return value

    @model_validator(mode="after")
    def _audience_is_usable(self) -> Self:
        value = self.audience
        if not value or value.strip() != value or "." in value:
            msg = "GHAPI_AUDIENCE must be non-empty, trimmed, and contain no dot"
            raise ValueError(msg)
        if self.subscription_default_seconds > self.subscription_max_seconds:
            msg = (
                "GHAPI_SUBSCRIPTION_DEFAULT_SECONDS must not exceed GHAPI_SUBSCRIPTION_MAX_SECONDS"
            )
            raise ValueError(msg)
        return self


def check_for_unknown_env_vars(environ: Mapping[str, str] | None = None) -> None:
    """Refuse unknown ``GHAPI_*`` variables so a typo fails at startup."""
    known = {ENV_PREFIX + name.upper() for name in Settings.model_fields}
    source = environ if environ is not None else os.environ
    unknown = sorted(key for key in source if key.startswith(ENV_PREFIX) and key not in known)
    if unknown:
        msg = "unknown environment variables: " + ", ".join(unknown)
        raise RuntimeError(msg)


def load_settings() -> Settings:
    """Load settings and refuse unknown env vars under the prefix."""
    check_for_unknown_env_vars()
    return Settings()  # type: ignore[call-arg]  # the service token comes from the environment
