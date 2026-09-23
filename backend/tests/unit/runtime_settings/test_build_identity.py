from __future__ import annotations

from pathlib import Path

import pytest

from ci_coordinator.kernel import canonical_json
from ci_coordinator.runtime_settings import (
    BUILD_IDENTITY_SCHEMA,
    BuildIdentity,
    load_bundled_build_identity,
    parse_build_identity,
    render_build_identity,
)


def test_bundled_identity_is_an_explicit_non_production_development_build() -> None:
    identity = load_bundled_build_identity()

    assert identity == BuildIdentity("0" * 64, "0" * 40, False)


def test_production_identity_rejects_sentinels_and_noncanonical_json() -> None:
    with pytest.raises(ValueError, match="development sentinels"):
        BuildIdentity("0" * 64, "0" * 40, True)

    mapping = {
        "schemaVersion": BUILD_IDENTITY_SCHEMA,
        "releaseIdentity": "a" * 64,
        "sourceCommit": "b" * 40,
        "productionEligible": True,
    }
    canonical = canonical_json(mapping) + b"\n"
    assert parse_build_identity(canonical) == BuildIdentity("a" * 64, "b" * 40, True)
    with pytest.raises(ValueError, match="canonical JSON"):
        parse_build_identity(b"  " + canonical)


def test_renderer_round_trips_the_exact_build_identity(tmp_path: Path) -> None:
    output = tmp_path / "build-identity.json"

    render_build_identity(
        output,
        release_identity="a" * 64,
        source_commit="b" * 40,
        production_eligible=True,
    )

    assert parse_build_identity(output.read_bytes()) == BuildIdentity("a" * 64, "b" * 40, True)
