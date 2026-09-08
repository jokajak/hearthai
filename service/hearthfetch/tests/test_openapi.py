"""The published contract is what OpenWebUI registers. It has to stay narrow."""

import json
import pathlib

import pytest

from importlib.resources import files

# Read the way the service reads it, so the test fails if packaging breaks.
SPEC = json.loads((files("hearthfetch") / "openapi.json").read_text(encoding="utf-8"))
BLOB = json.dumps(SPEC).lower()

INFRASTRUCTURE_WORDS = [
    "image", "command", "namespace", "pod", "credential", "mount",
    "workspace", "kubernetes", "container", "env", "shell",
]


def test_only_the_two_tools_are_published():
    assert set(SPEC["paths"]) == {"/v1/tools/search_web", "/v1/tools/fetch_result"}


def test_no_operation_accepts_a_url():
    """The whole point of handles. A url property here would undo it."""
    for path, spec in SPEC["paths"].items():
        schema = spec["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert "url" not in schema["properties"], path


@pytest.mark.parametrize("word", INFRASTRUCTURE_WORDS)
def test_the_contract_is_not_infrastructure_shaped(word):
    for spec in SPEC["paths"].values():
        properties = spec["post"]["requestBody"]["content"]["application/json"]["schema"][
            "properties"
        ]
        assert word not in properties


def test_every_request_schema_refuses_unknown_fields():
    for path, spec in SPEC["paths"].items():
        schema = spec["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert schema["additionalProperties"] is False, path


def test_bounds_are_published_so_the_model_does_not_discover_them_by_failing():
    search = SPEC["paths"]["/v1/tools/search_web"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["properties"]
    assert search["query"]["maxLength"] == 512
    assert (search["count"]["minimum"], search["count"]["maximum"]) == (1, 10)


def test_descriptions_tell_the_model_a_handle_is_not_a_url():
    fetch = SPEC["paths"]["/v1/tools/fetch_result"]["post"]
    assert "not a url" in fetch["requestBody"]["content"]["application/json"]["schema"][
        "properties"
    ]["handle"]["description"].lower()


def test_operation_ids_match_the_tool_names_openwebui_will_show():
    ids = {spec["post"]["operationId"] for spec in SPEC["paths"].values()}
    assert ids == {"search_web", "fetch_result"}
