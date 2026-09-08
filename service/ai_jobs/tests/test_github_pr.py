import pytest

from ai_jobs.contracts import ContractError
from ai_jobs.tools.git_change import GitChangeDefinition, GitChangeRequest


def test_request_accepts_an_opaque_authorised_repository_id():
    request = GitChangeRequest.from_dict(
        {"repository_id": "home-ops", "instruction": "Add a test."}
    )
    assert request.to_dict() == {
        "repository_id": "home-ops",
        "instruction": "Add a test.",
    }


@pytest.mark.parametrize("repository_id", ["../evil", "owner/..", "a b", "x\ny", "-ssh"])
def test_request_rejects_remote_or_path_shapes(repository_id):
    with pytest.raises(ContractError):
        GitChangeRequest.from_dict(
            {"repository_id": repository_id, "instruction": "Do work"}
        )


@pytest.mark.parametrize("field", ["branch", "command", "image", "base", "token", "url"])
def test_request_rejects_operational_fields(field):
    with pytest.raises(ContractError):
        GitChangeRequest.from_dict(
            {"repository_id": "home-ops", "instruction": "Do work", field: "v"}
        )


def test_profile_and_grants_are_fixed():
    definition = GitChangeDefinition()
    request = definition.validate_request(
        {"repository_id": "home-ops", "instruction": "Do work"}
    )
    assert definition.key == "git_change"
    assert definition.worker_profile == "git-change-v1"
    assert definition.grants_for(request) == frozenset(
        {"repository_materialize", "repository_publish"}
    )


def test_results_fail_closed_until_the_publisher_protocol_exists():
    with pytest.raises(NotImplementedError):
        GitChangeDefinition().validate_result({})
