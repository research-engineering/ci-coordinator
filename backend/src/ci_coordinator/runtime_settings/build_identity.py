"""Strict packaged build identity and deterministic image-build renderer."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Final, cast

from ci_coordinator.kernel import StrictJsonError, canonical_json, load_strict_json

BUILD_IDENTITY_RESOURCE: Final = "build-identity.v1.json"
BUILD_IDENTITY_SCHEMA: Final = "ci-coordinator-build-identity/v1"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_GIT_SHA = re.compile(r"[0-9a-f]{40,64}")


@dataclass(frozen=True, slots=True)
class BuildIdentity:
    release_identity: str
    source_commit: str
    production_eligible: bool

    def __post_init__(self) -> None:
        if _DIGEST.fullmatch(self.release_identity) is None:
            raise ValueError("build release identity must be lowercase SHA-256 hexadecimal")
        if _GIT_SHA.fullmatch(self.source_commit) is None:
            raise ValueError("build source commit must be a canonical Git object id")
        if type(self.production_eligible) is not bool:
            raise TypeError("build production eligibility must be an exact boolean")
        if self.production_eligible and (
            self.release_identity == "0" * 64 or self.source_commit == "0" * 40
        ):
            raise ValueError("production build identity cannot use development sentinels")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": BUILD_IDENTITY_SCHEMA,
            "releaseIdentity": self.release_identity,
            "sourceCommit": self.source_commit,
            "productionEligible": self.production_eligible,
        }


def parse_build_identity(content: bytes) -> BuildIdentity:
    try:
        value = load_strict_json(content, max_bytes=4_096)
    except StrictJsonError as error:
        raise ValueError("build identity must be bounded duplicate-free JSON") from error
    if type(value) is not dict or set(value) != {
        "productionEligible",
        "releaseIdentity",
        "schemaVersion",
        "sourceCommit",
    }:
        raise ValueError("build identity shape is invalid")
    record = cast(dict[str, object], value)
    if record["schemaVersion"] != BUILD_IDENTITY_SCHEMA:
        raise ValueError("build identity schema is unsupported")
    try:
        identity = BuildIdentity(
            release_identity=cast(str, record["releaseIdentity"]),
            source_commit=cast(str, record["sourceCommit"]),
            production_eligible=cast(bool, record["productionEligible"]),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("build identity values are invalid") from error
    if canonical_json(identity.to_mapping()) + b"\n" != content:
        raise ValueError("build identity must use canonical JSON with one trailing LF")
    return identity


def load_bundled_build_identity() -> BuildIdentity:
    content = (
        files("ci_coordinator.runtime_settings.resources")
        .joinpath(BUILD_IDENTITY_RESOURCE)
        .read_bytes()
    )
    return parse_build_identity(content)


def render_build_identity(
    output: Path,
    *,
    release_identity: str,
    source_commit: str,
    production_eligible: bool,
) -> None:
    identity = BuildIdentity(release_identity, source_commit, production_eligible)
    output.write_bytes(canonical_json(identity.to_mapping()) + b"\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-identity", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--production-eligible", action="store_true")
    arguments = parser.parse_args()
    render_build_identity(
        arguments.output,
        release_identity=arguments.release_identity,
        source_commit=arguments.source_commit,
        production_eligible=arguments.production_eligible,
    )


if __name__ == "__main__":
    main()
