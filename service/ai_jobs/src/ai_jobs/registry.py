"""Closed startup-time registry for reviewed tool definitions."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Protocol

from ai_jobs.contracts import BrokerBudgets, ToolFailure


class RegistryError(ValueError):
    pass


class UnknownTool(RegistryError):
    pass


class ToolDefinition(Protocol):
    key: str
    version: int
    worker_profile: str

    def validate_request(self, value: object) -> object: ...
    def validate_result(self, value: object) -> object: ...
    def grants_for(self, request: object) -> frozenset[str]: ...
    def budgets_for(self, request: object) -> BrokerBudgets: ...
    def public_failure(self, failure: ToolFailure) -> ToolFailure: ...


class ToolRegistry:
    def __init__(self, definitions: tuple[ToolDefinition, ...]):
        items: dict[tuple[str, int], ToolDefinition] = {}
        for definition in definitions:
            identity = (definition.key, definition.version)
            if identity in items:
                raise RegistryError(f"duplicate tool definition: {definition.key}:v{definition.version}")
            items[identity] = definition
        self._items: Mapping[tuple[str, int], ToolDefinition] = MappingProxyType(items)

    def resolve(self, key: str, version: int) -> ToolDefinition:
        try:
            return self._items[(key, version)]
        except KeyError as exc:
            raise UnknownTool(f"unknown tool: {key}:v{version}") from exc
