import pytest

from ai_jobs.contracts import BrokerBudgets
from ai_jobs.registry import RegistryError, ToolRegistry, UnknownTool
from ai_jobs.tools.web_research import WebResearchDefinition


def test_registry_resolves_only_registered_tool_and_is_closed():
    definition = WebResearchDefinition()
    registry = ToolRegistry((definition,))
    assert registry.resolve("web_research", 1) is definition
    with pytest.raises(UnknownTool):
        registry.resolve("code_workspace", 1)


def test_registry_rejects_duplicate_key_and_version():
    with pytest.raises(RegistryError, match="duplicate"):
        ToolRegistry((WebResearchDefinition(), WebResearchDefinition()))


class ExampleExtension:
    """Test-only proof that lifecycle registration is not research-shaped."""

    key = "example_extension"
    version = 1
    worker_profile = "example-v1"

    def validate_request(self, value):
        if set(value) != {"input"}:
            raise ValueError("invalid example request")
        return value

    def validate_result(self, value):
        return value

    def grants_for(self, request):
        return frozenset({"example_read"})

    def budgets_for(self, request):
        return BrokerBudgets(0, 0, 0, 0, 0, 1)

    def public_failure(self, failure):
        return failure


def test_second_typed_definition_reuses_registry_without_becoming_public():
    extension = ExampleExtension()
    registry = ToolRegistry((WebResearchDefinition(), extension))
    resolved = registry.resolve("example_extension", 1)
    request = resolved.validate_request({"input": "fixture"})
    assert resolved.grants_for(request) == frozenset({"example_read"})
    assert resolved.budgets_for(request).elapsed_seconds == 1
