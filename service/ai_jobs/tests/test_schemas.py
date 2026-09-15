"""Keep the language-neutral schemas and the Python contracts from drifting.

The schemas in `service/web_fetch/schemas` are what a future Go or Rust
implementation would read. Nothing validates them at runtime, so this test is
the thing that notices when a limit or an enum changes on one side only.
"""

import json
from pathlib import Path

from ai_jobs.envelope import ENVELOPE_VERSION, ERROR_MESSAGES, ErrorCode, InspectionStatus, Status, Trust
from ai_jobs.tools.web_fetch import (
    FORMATS,
    MAX_CONTENT_CHARS,
    MAX_CONTENT_TYPE_CHARS,
    MAX_URL_CHARS,
    ConversionMethod,
)

SCHEMAS = Path(__file__).parents[2] / "web_fetch/schemas"
OPENAPI = json.loads((Path(__file__).parents[1] / "openapi.json").read_text())


def schema(name):
    return json.loads((SCHEMAS / name).read_text())


def test_envelope_schema_matches_the_python_envelope():
    envelope = schema("envelope-v1.schema.json")
    properties = envelope["properties"]
    assert properties["envelope_version"]["const"] == ENVELOPE_VERSION
    assert set(properties["status"]["enum"]) == {status.value for status in Status}
    assert set(properties["trust"]["enum"]) == {trust.value for trust in Trust}

    inspection = envelope["$defs"]["Inspection"]["properties"]
    assert set(inspection["status"]["enum"]) == {status.value for status in InspectionStatus}

    error = envelope["$defs"]["ToolError"]["properties"]
    assert set(error["code"]["enum"]) == {code.value for code in ErrorCode}
    assert set(error["message"]["enum"]) == set(ERROR_MESSAGES.values())


def test_request_and_result_schemas_match_the_python_limits():
    request = schema("web-fetch-v1-request.schema.json")["properties"]
    assert request["url"]["maxLength"] == MAX_URL_CHARS
    assert set(request["format"]["enum"]) == set(FORMATS)

    result = schema("web-fetch-v1-result.schema.json")["properties"]
    assert result["content"]["maxLength"] == MAX_CONTENT_CHARS
    assert result["content_type"]["maxLength"] == MAX_CONTENT_TYPE_CHARS
    assert set(result["format"]["enum"]) == set(FORMATS)
    assert set(result["conversion_method"]["enum"]) == {method.value for method in ConversionMethod}


def test_stage_and_artifact_schemas_use_the_same_fixed_codes():
    stage = schema("stage-outcome-v1.schema.json")["properties"]
    assert set(stage["code"]["enum"]) == {None} | {code.value for code in ErrorCode}
    artifact = schema("fetch-artifact-v1.schema.json")["properties"]
    assert set(artifact["format"]["enum"]) == set(FORMATS)
    assert artifact["artifact_version"]["const"] == 1


def test_openapi_exposes_the_tool_with_the_same_shapes():
    operation = OPENAPI["paths"]["/v1/tools/web-fetch"]["post"]
    assert operation["operationId"] == "webFetch"
    assert any(parameter["name"] == "Idempotency-Key" for parameter in operation["parameters"])

    components = OPENAPI["components"]["schemas"]
    assert components["WebFetchRequest"]["properties"]["url"]["maxLength"] == MAX_URL_CHARS
    assert components["WebFetchRequest"]["additionalProperties"] is False
    assert components["WebFetchEnvelope"]["properties"]["tool"]["const"] == "web_fetch"
    assert set(components["EnvelopeError"]["properties"]["code"]["enum"]) == {code.value for code in ErrorCode}
    # The published result schema is the envelope, not a bare payload: a caller
    # cannot be handed content without the status and trust that came with it.
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response["$ref"].endswith("WebFetchEnvelope")
