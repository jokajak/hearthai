"""Keep the independently packaged worker pinned to the control-plane wire fixtures."""

import json
from pathlib import Path

FIXTURES = Path(__file__).parents[2] / "ai_jobs" / "tests" / "fixtures" / "web_research"


def test_worker_contract_fixtures_have_expected_shape():
    request = json.loads((FIXTURES / "valid-request-minimal.json").read_text())
    result = json.loads((FIXTURES / "valid-result.json").read_text())
    assert set(request) == {"question"}
    assert set(result) == {"summary", "findings", "sources", "conflicts", "limitations"}
    source_ids = {source["id"] for source in result["sources"]}
    assert all(
        evidence["source_id"] in source_ids
        for finding in result["findings"]
        for evidence in finding["evidence"]
    )
