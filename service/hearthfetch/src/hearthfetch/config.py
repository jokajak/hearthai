"""Deployment configuration.

Every open decision is a value here rather than a code change: which model backs
the distiller and the classifier, and how aggressive the CSS-hidden rule is.

Deliberately NOT configurable: whether a literal-URL tool exists. It does not,
and no environment variable can bring it back.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from hearthfetch.classify import ClassifierConfig
from hearthfetch.distill import DistillerConfig
from hearthfetch.fetch import FetchPolicy
from hearthfetch.llm import ModelConfig
from hearthfetch.scrub_in import ScrubPolicy

PREFIX = "HEARTHFETCH_"


def _env(name: str, default: str) -> str:
    return os.environ.get(PREFIX + name, default)


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{PREFIX}{name} must be an integer") from None


def _env_float(name: str, default: float) -> float:
    raw = _env(name, str(default))
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{PREFIX}{name} must be a number") from None


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "true" if default else "false").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{PREFIX}{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class Config:
    distiller: DistillerConfig
    classifier: ClassifierConfig
    fetch_policy: FetchPolicy = field(default_factory=FetchPolicy)
    scrub_policy: ScrubPolicy = field(default_factory=ScrubPolicy)
    litellm_base_url: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            distiller=DistillerConfig(
                model=ModelConfig(
                    name=_env("DISTILLER_MODEL", "gpt-5.4-mini"),
                    max_output_tokens=_env_int("DISTILLER_MAX_OUTPUT_TOKENS", 800),
                    timeout_seconds=_env_float("DISTILLER_TIMEOUT_SECONDS", 30.0),
                ),
                max_input_chars=_env_int("DISTILLER_MAX_INPUT_CHARS", 40_000),
            ),
            classifier=ClassifierConfig(
                model=ModelConfig(
                    name=_env("CLASSIFIER_MODEL", "gpt-5.4-mini"),
                    # A one-word answer needs no room. Capping it tightly also
                    # bounds what a compromised model can spend.
                    max_output_tokens=_env_int("CLASSIFIER_MAX_OUTPUT_TOKENS", 8),
                    timeout_seconds=_env_float("CLASSIFIER_TIMEOUT_SECONDS", 15.0),
                ),
                max_input_chars=_env_int("CLASSIFIER_MAX_INPUT_CHARS", 12_000),
            ),
            fetch_policy=FetchPolicy(
                max_bytes=_env_int("FETCH_MAX_BYTES", 2_000_000),
                max_redirects=_env_int("FETCH_MAX_REDIRECTS", 5),
                timeout_seconds=_env_float("FETCH_TIMEOUT_SECONDS", 10.0),
            ),
            scrub_policy=ScrubPolicy(
                css_hidden_rejects=_env_bool("CSS_HIDDEN_REJECTS", True),
            ),
            litellm_base_url=_env("LITELLM_BASE_URL", ""),
        )
