import pytest

from ai_jobs.contracts import ContractError
from ai_jobs.tools.github_pr import GitHubPRDefinition, GitHubPRRequest


def test_request_accepts_only_repository_and_instruction():
    request = GitHubPRRequest.from_dict(
        {"repository": "jokajak/hearthai", "instruction": "Add a test."}
    )
    assert request.to_dict() == {
        "repository": "jokajak/hearthai",
        "instruction": "Add a test.",
    }


@pytest.mark.parametrize("repository", ["hearthai", "owner/", "/repo", "a/b/c"])
def test_request_rejects_non_repository_identifiers(repository):
    with pytest.raises(ContractError):
        GitHubPRRequest.from_dict({"repository": repository, "instruction": "Do work"})


def test_profile_and_grants_are_fixed():
    definition = GitHubPRDefinition()
    request = definition.validate_request(
        {"repository": "jokajak/hearthai", "instruction": "Do work"}
    )
    assert definition.worker_profile == "github-pr-v1"
    assert definition.grants_for(request) == frozenset(
        {"repository_materialize", "repository_publish"}
    )
