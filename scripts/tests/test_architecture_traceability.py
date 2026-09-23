from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path, PurePosixPath

import pytest
from scripts import architecture_traceability
from scripts.architecture_traceability import (
    PROFILE_PATH,
    REPO_ROOT,
    validate_architecture_traceability,
)
from scripts.proofkit_common import JsonObject, read_json_object
from scripts.repository_source_admission import read_bounded_repository_text


def _profile() -> JsonObject:
    return deepcopy(read_json_object(REPO_ROOT / PROFILE_PATH))


def _groups(profile: JsonObject) -> list[object]:
    groups = profile["traceGroups"]
    assert isinstance(groups, list)
    return groups


def _group(profile: JsonObject, trace_id: str) -> dict[str, object]:
    for group in _groups(profile):
        if isinstance(group, dict) and group.get("traceId") == trace_id:
            assert all(isinstance(key, str) for key in group)
            return group
    raise AssertionError(f"missing architecture trace group: {trace_id}")


def _premise_route(profile: JsonObject, route_id: str) -> dict[str, object]:
    routes = profile["premiseRoutes"]
    assert isinstance(routes, list)
    for route in routes:
        if isinstance(route, dict) and route.get("routeId") == route_id:
            assert all(isinstance(key, str) for key in route)
            return route
    raise AssertionError(f"missing architecture premise route: {route_id}")


def _strings(group: dict[str, object], key: str) -> list[str]:
    values = group[key]
    assert isinstance(values, list)
    assert all(isinstance(value, str) for value in values)
    return values


def test_repository_architecture_traceability_is_closed() -> None:
    report = validate_architecture_traceability()
    requirements = {
        requirement_id
        for group in _groups(_profile())
        if isinstance(group, dict)
        for requirement_id in _strings(group, "requirementIds")
    }

    assert report["state"] == "passed"
    assert report["summary"]["requirementCount"] == len(requirements)
    assert "REQ-CI-DEV-018" in requirements
    assert report["summary"]["lawCount"] == 12
    assert report["summary"]["premiseRouteCount"] == 6


def test_architecture_traceability_rejects_missing_requirement() -> None:
    profile = _profile()
    _strings(_group(profile, "control-plane.01-authority-separation"), "requirementIds").pop()

    with pytest.raises(ValueError, match="requirement trace is not closed"):
        validate_architecture_traceability(profile=profile)


def test_architecture_traceability_rejects_duplicate_requirement() -> None:
    profile = _profile()
    duplicate = _strings(
        _group(profile, "control-plane.01-authority-separation"), "requirementIds"
    )[0]
    requirements = _strings(_group(profile, "core.01-governing-laws"), "requirementIds")
    requirements.append(duplicate)
    requirements.sort()

    with pytest.raises(ValueError, match="multiple architecture traces"):
        validate_architecture_traceability(profile=profile)


def test_architecture_traceability_rejects_unknown_law() -> None:
    profile = _profile()
    laws = _strings(_group(profile, "core.01-governing-laws"), "lawIds")
    laws[0] = "MS-999"
    laws.sort()

    with pytest.raises(ValueError, match="references unknown laws"):
        validate_architecture_traceability(profile=profile)


def test_architecture_traceability_rejects_unbound_requirement_floor() -> None:
    profile = _profile()
    laws = _strings(_group(profile, "core.01-governing-laws"), "lawIds")
    laws.remove("MS-1")

    with pytest.raises(ValueError, match="requirement floor lacks an exact trace"):
        validate_architecture_traceability(profile=profile)


def test_architecture_traceability_rejects_missing_contract() -> None:
    profile = _profile()
    contracts = _strings(_group(profile, "control-plane.01-authority-separation"), "contractPaths")
    contracts[0] = "docs/architecture/modules/missing.md"
    contracts.sort()

    with pytest.raises(ValueError, match="not a regular docs file"):
        validate_architecture_traceability(profile=profile)


def test_architecture_traceability_rejects_stale_source_inventory() -> None:
    profile = _profile()
    paths = profile["requirementSourcePaths"]
    assert isinstance(paths, list)
    paths.pop()

    with pytest.raises(ValueError, match="complete sorted inventory"):
        validate_architecture_traceability(profile=profile)


def test_architecture_traceability_rejects_unrouted_premise() -> None:
    profile = _profile()
    _strings(_premise_route(profile, "identity.organization-control-plane"), "premiseIds").remove(
        "PA-9"
    )

    with pytest.raises(ValueError, match="premises lack a law or context route: PA-9"):
        validate_architecture_traceability(profile=profile)


def test_architecture_traceability_rejects_parent_symlink_escape(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "authority.md").write_text("outside", encoding="utf-8")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "docs").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="traverses symlink component"):
        read_bounded_repository_text(
            repo_root,
            PurePosixPath("docs/authority.md"),
            maximum_bytes=1024,
        )


def test_architecture_traceability_rejects_source_swap_to_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    source = repo_root / "docs" / "authority.md"
    source.parent.mkdir(parents=True)
    source.write_text("inside", encoding="utf-8")
    external = tmp_path / "external.md"
    external.write_text("outside", encoding="utf-8")
    real_stat = os.stat
    swapped = False

    def swap_after_metadata(
        path: os.PathLike[str] | str | bytes,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal swapped
        result = real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if path == "authority.md" and dir_fd is not None and not swapped:
            source.unlink()
            source.symlink_to(external)
            swapped = True
        return result

    monkeypatch.setattr("scripts.repository_source_admission.os.stat", swap_after_metadata)

    with pytest.raises(ValueError, match="changed during admission"):
        read_bounded_repository_text(
            repo_root,
            PurePosixPath("docs/authority.md"),
            maximum_bytes=1024,
        )


def test_architecture_traceability_bounds_aggregate_requirement_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        architecture_traceability,
        "_MAX_REQUIREMENT_SOURCE_TOTAL_BYTES",
        1,
    )

    with pytest.raises(ValueError, match="aggregate bytes"):
        validate_architecture_traceability(profile=_profile())


def test_architecture_traceability_bounds_input_before_decode(tmp_path: Path) -> None:
    source = tmp_path / "authority.md"
    source.write_bytes(b"12345")

    with pytest.raises(ValueError, match="exceeds 4 bytes"):
        read_bounded_repository_text(
            tmp_path,
            PurePosixPath("authority.md"),
            maximum_bytes=4,
        )
