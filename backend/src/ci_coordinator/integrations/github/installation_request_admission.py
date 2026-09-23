"""Closed capability set for installation-token GitHub requests."""

from __future__ import annotations

from ci_coordinator.ci_economics.discovery import DISCOVERY_PAGE_SIZE, MAX_DISCOVERY_PAGES
from ci_coordinator.integrations.github._routes import parse_workflow_run_created_query
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_MAXIMUM_INSTALLATION_ID,
)
from ci_coordinator.integrations.github.contracts import GitHubQueryParameter, GitHubRequest
from ci_coordinator.integrations.github.request_admission import (
    bounded_ascii_path_is_admitted,
    canonical_path_segment_is_admitted,
    canonical_reference_is_admitted,
    parse_canonical_page_query,
    parse_canonical_positive_integer,
)


def installation_request_is_admitted(request: GitHubRequest) -> bool:
    """Admit one exact read operation before installation credential selection."""
    if (
        request.method != "GET"
        or request.body is not None
        or not bounded_ascii_path_is_admitted(request.path)
    ):
        return False
    if request.operation in {
        "governance_observation.get_repository",
        "repositories.get_by_id",
        "workflow_authority.get_repository",
        "workflow_discovery.get_repository",
    }:
        return _positive_identity_path(request.path, "/repositories/") and not request.query
    if request.operation == "provider_inventory.list_repositories":
        return request.path == "/installation/repositories" and _page_query(request)
    if request.operation == "runner.list_visible_self_hosted_runner_groups":
        return _organization_runner_tail(request.path) == ("runner-groups",) and (
            _visible_repository_page_query(request)
        )
    if request.operation == "runner.list_group_self_hosted_runners":
        tail = _organization_runner_tail(request.path)
        return (
            tail is not None
            and len(tail) == 3
            and tail[0] == "runner-groups"
            and _positive_integer(tail[1])
            and tail[2] == "runners"
            and _page_query(request)
        )

    parsed = _repository_tail(request.path)
    if parsed is None:
        return False
    tail = parsed
    if request.operation == "actions.get_workflow_run":
        return (
            len(tail) == 3
            and tail[:2] == ("actions", "runs")
            and _positive_integer(tail[2])
            and not request.query
        )
    if request.operation == "actions.list_repository_workflow_runs":
        if tail != ("actions", "runs") or tuple(item.name for item in request.query) != (
            "created",
            "page",
            "per_page",
        ):
            return False
        page = parse_canonical_page_query(request.query[1:])
        return (
            parse_workflow_run_created_query(request.query[0].value) is not None
            and page is not None
            and page[0] <= MAX_DISCOVERY_PAGES
            and page[1] == DISCOVERY_PAGE_SIZE
        )
    if request.operation == "actions.list_workflow_runs":
        return (
            len(tail) == 4
            and tail[:2] == ("actions", "workflows")
            and canonical_path_segment_is_admitted(tail[2])
            and tail[3] == "runs"
            and _page_query(request)
        )
    if request.operation in {
        "actions.get_workflow_run_attempt",
        "actions.list_workflow_run_attempt_jobs",
    }:
        jobs = request.operation == "actions.list_workflow_run_attempt_jobs"
        return (
            len(tail) == (6 if jobs else 5)
            and tail[0:2] == ("actions", "runs")
            and _positive_integer(tail[2])
            and tail[3] == "attempts"
            and _positive_integer(tail[4])
            and (tail[5] == "jobs" and _page_query(request) if jobs else not request.query)
        )
    if request.operation == "checks.list_check_runs":
        return (
            len(tail) == 3
            and tail[0] == "commits"
            and canonical_path_segment_is_admitted(tail[1])
            and tail[2] == "check-runs"
            and _page_query(request)
        )
    if request.operation == "diff.get_pull_request":
        return _pull_request_tail(tail, files=False) and not request.query
    if request.operation == "diff.list_pull_request_files":
        return _pull_request_tail(tail, files=True) and _page_query(request)
    if request.operation == "diff.compare":
        return _compare_tail(tail) and not request.query
    if request.operation == "runner.list_self_hosted_runners":
        return tail == ("actions", "runners") and _page_query(request)
    if request.operation == "workflow_catalog.list_workflows":
        return tail == ("actions", "workflows") and _page_query(request)
    if request.operation == "workflow_catalog.get_workflow":
        return (
            len(tail) == 3
            and tail[:2] == ("actions", "workflows")
            and canonical_path_segment_is_admitted(tail[2])
            and not request.query
        )
    if request.operation == "workflow_catalog.get_content":
        return (
            len(tail) >= 2
            and tail[0] == "contents"
            and all(canonical_path_segment_is_admitted(value) for value in tail[1:])
            and _ref_query(request)
        )
    if request.operation == "workflow_discovery.get_reference":
        return (
            len(tail) >= 3
            and tail[:2] == ("git", "ref")
            and all(canonical_path_segment_is_admitted(value) for value in tail[2:])
            and not request.query
        )
    if request.operation == "governance_observation.list_effective_branch_rules":
        return governance_observation_rules_path_is_admitted(request.path) and _page_query(request)
    if request.operation == "reviewer_attestation.get_permission":
        return (
            len(tail) == 3
            and tail[0] == "collaborators"
            and canonical_path_segment_is_admitted(tail[1])
            and tail[2] == "permission"
            and not request.query
        )
    if request.operation == "workflow_discovery.get_recursive_tree":
        return (
            len(tail) == 3
            and tail[:2] == ("git", "trees")
            and canonical_path_segment_is_admitted(tail[2])
            and request.query == (GitHubQueryParameter("recursive", "1"),)
        )
    git_object_leaf = {
        "workflow_authority.get_blob": "blobs",
        "workflow_authority.get_commit": "commits",
        "workflow_authority.get_tree": "trees",
        "workflow_discovery.get_blob": "blobs",
        "workflow_discovery.get_commit": "commits",
        "workflow_discovery.get_tree": "trees",
    }.get(request.operation)
    return (
        git_object_leaf is not None
        and len(tail) == 3
        and tail[:2] == ("git", git_object_leaf)
        and canonical_path_segment_is_admitted(tail[2])
        and not request.query
    )


def _repository_tail(path: str) -> tuple[str, ...] | None:
    segments = tuple(path.split("/"))
    if (
        len(segments) < 5
        or segments[:2] != ("", "repos")
        or not canonical_path_segment_is_admitted(segments[2])
        or not canonical_path_segment_is_admitted(segments[3])
    ):
        return None
    return segments[4:]


def _organization_runner_tail(path: str) -> tuple[str, ...] | None:
    segments = tuple(path.split("/"))
    if (
        len(segments) < 5
        or segments[:2] != ("", "orgs")
        or not canonical_path_segment_is_admitted(segments[2])
        or segments[3] != "actions"
    ):
        return None
    return segments[4:]


def governance_observation_rules_path_is_admitted(path: str) -> bool:
    """Admit the complete encoded path owned by effective-governance reads."""
    if not bounded_ascii_path_is_admitted(path):
        return False
    tail = _repository_tail(path)
    return (
        tail is not None
        and len(tail) == 3
        and tail[:2] == ("rules", "branches")
        and canonical_reference_is_admitted(tail[2])
    )


def _pull_request_tail(tail: tuple[str, ...], *, files: bool) -> bool:
    expected_length = 3 if files else 2
    return (
        len(tail) == expected_length
        and tail[0] == "pulls"
        and _positive_integer(tail[1])
        and (not files or tail[2] == "files")
    )


def _compare_tail(tail: tuple[str, ...]) -> bool:
    if len(tail) != 2 or tail[0] != "compare" or tail[1].count("...") != 1:
        return False
    base, head = tail[1].split("...")
    return all(canonical_reference_is_admitted(value) for value in (base, head))


def _positive_identity_path(path: str, prefix: str) -> bool:
    return path.startswith(prefix) and _positive_integer(path.removeprefix(prefix))


def _positive_integer(value: str) -> bool:
    return (
        parse_canonical_positive_integer(
            value,
            maximum=GITHUB_MAXIMUM_INSTALLATION_ID,
        )
        is not None
    )


def _page_query(request: GitHubRequest) -> bool:
    return parse_canonical_page_query(request.query) is not None


def _visible_repository_page_query(request: GitHubRequest) -> bool:
    if tuple(parameter.name for parameter in request.query) != (
        "visible_to_repository",
        "page",
        "per_page",
    ):
        return False
    repository = request.query[0].value
    return _bounded_query_text(repository, maximum_bytes=100) and (
        parse_canonical_page_query(request.query[1:]) is not None
    )


def _bounded_query_text(value: object, *, maximum_bytes: int) -> bool:
    return (
        type(value) is str
        and bool(value)
        and not any(
            ord(character) < 32 or ord(character) == 127 or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
        and len(value.encode("utf-8")) <= maximum_bytes
    )


def _ref_query(request: GitHubRequest) -> bool:
    if tuple(parameter.name for parameter in request.query) != ("ref",):
        return False
    value = request.query[0].value
    try:
        byte_length = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    return bool(value) and "\0" not in value and byte_length <= 1_024
