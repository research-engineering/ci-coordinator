"""Bounded non-executing GitHub Actions workflow parser."""

from __future__ import annotations

from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import Node

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.workflow_discovery import _syntax_fields as syntax
from ci_coordinator.workflow_discovery._evidence_builder import EvidenceBuilder
from ci_coordinator.workflow_discovery._evidence_fields import (
    concurrency_field as _concurrency_field,
)
from ci_coordinator.workflow_discovery._evidence_fields import (
    permissions_field as _permissions_field,
)
from ci_coordinator.workflow_discovery._job_parser import parse_job
from ci_coordinator.workflow_discovery._validation import require_text
from ci_coordinator.workflow_discovery._yaml_nodes import (
    YamlMapping,
    contains_expression,
    project_yaml_node,
    scalar_text,
)
from ci_coordinator.workflow_discovery._yaml_preflight import (
    new_yaml,
    preflight_yaml_events,
)
from ci_coordinator.workflow_discovery.evidence import (
    Provenance,
    Unknown,
    YamlLocation,
)
from ci_coordinator.workflow_discovery.report import PARSER_VERSION
from ci_coordinator.workflow_discovery.source import WorkflowSource
from ci_coordinator.workflow_discovery.summary import (
    ParsedWorkflow,
    WorkflowParseFailure,
    WorkflowParseOutcome,
    WorkflowSummary,
    workflow_subject_id,
)

_MAX_YAML_EVENTS = 50_000
_MAX_YAML_NODES = 20_000
_MAX_YAML_DEPTH = 64
_MAX_YAML_SCALAR_BYTES = 4_096


def parse_workflow(
    source: WorkflowSource,
    *,
    scope: RepositoryScope,
    revision: str,
    default_branch: str,
) -> WorkflowParseOutcome:
    require_text(default_branch, "default branch", maximum_bytes=1_024)
    base = Provenance(
        scope=scope,
        revision=revision,
        workflow_path=source.path,
        blob_sha=source.blob_sha,
        parser_version=PARSER_VERSION,
        location=YamlLocation("workflow.document", 1, 1),
    )
    try:
        text = source.content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _failure(base, "invalid_utf8")
    if "\x00" in text:
        return _failure(base, "nul_forbidden")
    preflight = preflight_yaml_events(
        text,
        max_events=_MAX_YAML_EVENTS,
        max_nodes=_MAX_YAML_NODES,
        max_depth=_MAX_YAML_DEPTH,
        max_scalar_bytes=_MAX_YAML_SCALAR_BYTES,
    )
    if preflight is not None:
        return _failure(
            Provenance(
                scope=scope,
                revision=revision,
                workflow_path=source.path,
                blob_sha=source.blob_sha,
                parser_version=PARSER_VERSION,
                location=YamlLocation(
                    "workflow.document",
                    preflight.line,
                    preflight.column,
                ),
            ),
            preflight.code,
        )
    try:
        raw_root: Node | None = new_yaml().compose(text)
        if raw_root is None:
            return _failure(base, "empty_document")
        root = project_yaml_node(raw_root)
        if not isinstance(root, YamlMapping):
            return _failure(base, "workflow_root_not_mapping")
        return _parse_root(
            source,
            scope=scope,
            revision=revision,
            default_branch=default_branch,
            root=root,
            base=base,
        )
    except (MemoryError, RecursionError):
        return _failure(base, "resource_exhausted")
    except (TypeError, ValueError, YAMLError):
        return _failure(base, "unsupported_workflow_syntax")


def _parse_root(
    source: WorkflowSource,
    *,
    scope: RepositoryScope,
    revision: str,
    default_branch: str,
    root: YamlMapping,
    base: Provenance,
) -> WorkflowParseOutcome:
    jobs_node = root.get("jobs")
    if not isinstance(jobs_node, YamlMapping) or not jobs_node.entries:
        return _failure(base, "jobs_mapping_required")
    subject_id = workflow_subject_id(scope=scope, revision=revision, path=source.path)
    evidence = EvidenceBuilder(base)

    jobs = tuple(
        sorted(
            (
                parse_job(
                    workflow_subject_id=subject_id,
                    job_id=job_id,
                    node=job_node,
                    evidence=evidence,
                )
                for job_id, job_node in jobs_node.entries
                if isinstance(job_node, YamlMapping)
            ),
            key=lambda item: utf16_sort_key(item.job_id),
        )
    )
    if len(jobs) != len(jobs_node.entries):
        return _failure(base, "job_mapping_required")

    name_node = root.get("name")
    raw_name = scalar_text(name_node)
    name = raw_name if raw_name is not None and not contains_expression(raw_name) else None
    if name_node is None or name is not None:
        evidence.fact(
            subject_id=subject_id,
            category="identity",
            field_name="workflow.name",
            value=name,
            criticality="informational",
            node=name_node,
            parent=root,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="identity",
            field_name="workflow.name",
            reason="dynamic_or_unsupported_workflow_name",
            criticality="informational",
            node=name_node,
            parent=root,
            observed_syntax=raw_name,
        )

    trigger_node = root.get("on")
    triggers, trigger_reason = syntax.triggers(trigger_node)
    if trigger_reason is None and triggers is not None:
        evidence.fact(
            subject_id=subject_id,
            category="invocation",
            field_name="workflow.triggers",
            value=triggers,
            criticality="informational",
            node=trigger_node,
            parent=root,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="invocation",
            field_name="workflow.triggers",
            reason=trigger_reason or "trigger_projection_failed",
            criticality="safety",
            node=trigger_node,
            parent=root,
        )

    default_branch_events = syntax.default_branch_ci_events(
        trigger_node,
        default_branch=default_branch,
    )
    if default_branch_events is not None:
        evidence.fact(
            subject_id=subject_id,
            category="invocation",
            field_name="workflow.default_branch_ci_events",
            value=default_branch_events,
            criticality="safety",
            node=trigger_node,
            parent=root,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="invocation",
            field_name="workflow.default_branch_ci_events",
            reason=trigger_reason or "default_branch_event_coverage_not_proven",
            criticality="safety",
            node=trigger_node,
            parent=root,
        )
    declared_permissions = _permissions_field(
        evidence, subject_id, root, field_name="workflow.permissions.declared"
    )
    declared_concurrency = _concurrency_field(
        evidence, subject_id, root, field_name="workflow.concurrency"
    )
    secret_names, secret_reason = syntax.secret_names(root)
    if secret_reason is None and secret_names is not None:
        evidence.fact(
            subject_id=subject_id,
            category="authority",
            field_name="workflow.secrets.static_names",
            value=secret_names,
            criticality="informational",
            node=root,
            parent=root,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="authority",
            field_name="workflow.secrets.static_names",
            reason=secret_reason or "secret_name_projection_failed",
            criticality="safety",
            node=root,
            parent=root,
        )

    evidence.fact(
        subject_id=subject_id,
        category="identity",
        field_name="workflow.job_ids",
        value=tuple(job.job_id for job in jobs),
        criticality="informational",
        node=jobs_node,
        parent=root,
    )
    for field_name, category, reason in (
        ("workflow.check_identity", "identity", "effective_check_identity_not_proven"),
        ("workflow.fork_exposure", "authority", "fork_exposure_not_observed"),
        ("workflow.oidc_availability", "authority", "effective_oidc_not_proven"),
        ("workflow.permissions.effective", "authority", "effective_permissions_not_proven"),
    ):
        evidence.unknown(
            subject_id=subject_id,
            category=category,  # type: ignore[arg-type]
            field_name=field_name,
            reason=reason,
            criticality="safety",
            node=None,
            parent=root,
        )

    facts, unknowns = evidence.finish()
    return ParsedWorkflow(
        summary=WorkflowSummary(
            subject_id=subject_id,
            path=source.path,
            name=name,
            triggers=triggers,
            permissions=declared_permissions,
            concurrency=declared_concurrency,
            static_secret_names=secret_names,
            jobs=jobs,
        ),
        facts=facts,
        unknowns=unknowns,
    )


def _failure(provenance: Provenance, code: str) -> WorkflowParseFailure:
    return WorkflowParseFailure(
        code,
        Unknown.create(
            subject_id="workflow-document",
            category="semantics",
            field="workflow.document",
            reason=code,
            criticality="safety",
            provenance=provenance,
        ),
    )
