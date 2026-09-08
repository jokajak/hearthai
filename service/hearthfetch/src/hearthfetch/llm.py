"""The model seam.

Everything that talks to a model goes through `ChatModel`. Tests drive scripted
fakes; production drives LiteLLM. Which model answers is a deployment value, not
a code change — the distiller and the classifier are configured independently
because they are different jobs with different cost profiles.

Nothing here is model-influenced. The endpoint, credential, model name, and
limits all come from configuration the operator sets.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


class ModelUnavailable(Exception):
    """The model could not be reached, refused, or returned nothing usable.

    Carries no provider body: an upstream error can quote the prompt back, and
    the prompt contains page content.
    """


@dataclass(frozen=True, slots=True)
class ModelConfig:
    name: str
    max_output_tokens: int
    timeout_seconds: float
    temperature: float = 0.0


class ChatModel(Protocol):
    def complete(self, *, system: str, user: str, config: ModelConfig) -> str: ...


@dataclass(frozen=True, slots=True)
class LiteLLMModel:
    """Thin adapter over LiteLLM's OpenAI-compatible chat completions endpoint.

    Deliberately not routed through the fetch policy in `fetch.py`: that policy
    exists to constrain destinations chosen from untrusted content, and this
    destination is an operator-configured cluster service. Conflating the two
    would mean either weakening the policy or being unable to reach LiteLLM.
    """

    base_url: str
    api_key: str

    def complete(self, *, system: str, user: str, config: ModelConfig) -> str:
        body = json.dumps({
            "model": config.name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": config.max_output_tokens,
            "temperature": config.temperature,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # Status only. The body may echo the prompt, and the prompt is a page.
            raise ModelUnavailable(f"model returned status {exc.code}") from None
        except Exception as exc:
            raise ModelUnavailable(f"model unreachable: {type(exc).__name__}") from None
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ModelUnavailable("model returned an unexpected shape") from None
        if not isinstance(content, str) or not content.strip():
            raise ModelUnavailable("model returned empty content")
        return content
