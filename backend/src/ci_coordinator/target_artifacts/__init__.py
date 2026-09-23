"""Deterministic target-repository artifact generation."""

from ci_coordinator.target_artifacts.model import (
    DependencyGraphDefinition,
    GeneratorIdentity,
    ManifestTestDefinition,
    RenderedTargetArtifacts,
    TargetArtifactsSource,
)
from ci_coordinator.target_artifacts.renderer import (
    TargetArtifactsRenderError,
    render_target_artifacts,
)
from ci_coordinator.target_artifacts.source_codec import (
    TargetArtifactsAdmissionError,
    parse_target_artifacts_source,
)
from ci_coordinator.target_artifacts.trust_root import (
    PLAN_TRUST_ROOT_FILENAME,
    check_plan_trust_root,
    render_plan_trust_root,
    write_plan_trust_root,
)
from ci_coordinator.target_artifacts.workflow import check_artifacts, write_artifacts

__all__ = [
    "PLAN_TRUST_ROOT_FILENAME",
    "DependencyGraphDefinition",
    "GeneratorIdentity",
    "ManifestTestDefinition",
    "RenderedTargetArtifacts",
    "TargetArtifactsAdmissionError",
    "TargetArtifactsRenderError",
    "TargetArtifactsSource",
    "check_artifacts",
    "check_plan_trust_root",
    "parse_target_artifacts_source",
    "render_plan_trust_root",
    "render_target_artifacts",
    "write_artifacts",
    "write_plan_trust_root",
]
