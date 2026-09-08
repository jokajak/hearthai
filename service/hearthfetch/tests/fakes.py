"""Scripted models. No network, no provider, deterministic."""

from __future__ import annotations

from hearthfetch.llm import ChatModel, ModelConfig, ModelUnavailable


class ScriptedModel:
    """Returns a fixed answer, or raises. Records what it was asked."""

    def __init__(self, answer: str = "A distilled summary of the page.", *, raises=None):
        self.answer = answer
        self.raises = raises
        self.calls: list[tuple[str, str, ModelConfig]] = []

    def complete(self, *, system: str, user: str, config: ModelConfig) -> str:
        self.calls.append((system, user, config))
        if self.raises is not None:
            raise self.raises
        return self.answer


class EchoModel:
    """Returns the page content it was given: a model that has been captured."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, *, system: str, user: str, config: ModelConfig) -> str:
        self.calls.append(user)
        return user


def unavailable(message: str = "model unreachable") -> ScriptedModel:
    return ScriptedModel(raises=ModelUnavailable(message))
