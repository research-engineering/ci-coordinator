"""Static job projection with one terminal outcome per expected predicate."""

from __future__ import annotations

from ci_coordinator.workflow_discovery import _syntax_fields as syntax
from ci_coordinator.workflow_discovery._evidence_builder import EvidenceBuilder
from ci_coordinator.workflow_discovery._evidence_fields import (
    concurrency_field as _concurrency_field,
)
from ci_coordinator.workflow_discovery._evidence_fields import (
    permissions_field as _permissions_field,
)
from ci_coordinator.workflow_discovery._yaml_nodes import (
    YamlMapping,
    contains_expression,
    scalar_int,
    scalar_text,
)
from ci_coordinator.workflow_discovery.evidence import EvidenceCategory
from ci_coordinator.workflow_discovery.summary import (
    JobSummary,
    MatrixDimension,
    job_subject_id,
)


def parse_job(
    *,
    workflow_subject_id: str,
    job_id: str,
    node: YamlMapping,
    evidence: EvidenceBuilder,
) -> JobSummary:
    subject_id = job_subject_id(workflow_id=workflow_subject_id, job_id=job_id)

    name_node = node.get("name")
    raw_name = scalar_text(name_node)
    name = raw_name if raw_name is not None and not contains_expression(raw_name) else None
    if name_node is None or name is not None:
        evidence.fact(
            subject_id=subject_id,
            category="identity",
            field_name="job.name",
            value=name,
            criticality="informational",
            node=name_node,
            parent=node,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="identity",
            field_name="job.name",
            reason="dynamic_or_unsupported_job_name",
            criticality="informational",
            node=name_node,
            parent=node,
            observed_syntax=raw_name,
        )

    needs = _text_set_field(evidence, subject_id, node, "needs", "job.needs", "invocation")

    uses_node = node.get("uses")
    uses = scalar_text(uses_node)
    uses_dynamic = uses is not None and contains_expression(uses)
    if uses_node is None or (uses is not None and not uses_dynamic):
        evidence.fact(
            subject_id=subject_id,
            category="invocation",
            field_name="job.uses",
            value=uses,
            criticality="informational",
            node=uses_node,
            parent=node,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="invocation",
            field_name="job.uses",
            reason="dynamic_or_unsupported_job_call",
            criticality="safety",
            node=uses_node,
            parent=node,
            observed_syntax=uses,
        )

    runs_on = _text_set_field(
        evidence,
        subject_id,
        node,
        "runs-on",
        "job.runs_on.declared",
        "execution",
    )
    declared_permissions = _permissions_field(
        evidence, subject_id, node, field_name="job.permissions.declared"
    )
    environment = _environment_field(evidence, subject_id, node)
    services = _services_field(evidence, subject_id, node)
    timeout = _timeout_field(evidence, subject_id, node)
    concurrency = _concurrency_field(evidence, subject_id, node, field_name="job.concurrency")
    matrix = _matrix_field(evidence, subject_id, node)

    condition_node = node.get("if")
    condition = scalar_text(condition_node)
    if condition_node is None or condition is not None:
        evidence.fact(
            subject_id=subject_id,
            category="semantics",
            field_name="job.condition.syntax",
            value=condition,
            criticality="informational",
            node=condition_node,
            parent=node,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="semantics",
            field_name="job.condition.syntax",
            reason="unsupported_condition_syntax",
            criticality="informational",
            node=condition_node,
            parent=node,
        )

    step_node = node.get("steps")
    step_projection = syntax.steps(step_node)
    if step_projection.reason is None and step_projection.steps is not None:
        evidence.fact(
            subject_id=subject_id,
            category="execution",
            field_name="job.step_run_presence",
            value=any(step.has_run for step in step_projection.steps),
            criticality="informational",
            node=step_node,
            parent=node,
        )
        step_uses = tuple(step.uses for step in step_projection.steps if step.uses is not None)
        if step_projection.dynamic_uses:
            evidence.unknown(
                subject_id=subject_id,
                category="invocation",
                field_name="job.step_uses",
                reason="dynamic_step_uses",
                criticality="safety",
                node=step_node,
                parent=node,
            )
        else:
            evidence.fact(
                subject_id=subject_id,
                category="invocation",
                field_name="job.step_uses",
                value=step_uses,
                criticality="informational",
                node=step_node,
                parent=node,
            )
    else:
        reason = step_projection.reason or "steps_projection_failed"
        evidence.unknown(
            subject_id=subject_id,
            category="execution",
            field_name="job.step_run_presence",
            reason=reason,
            criticality="informational",
            node=step_node,
            parent=node,
        )
        evidence.unknown(
            subject_id=subject_id,
            category="invocation",
            field_name="job.step_uses",
            reason=reason,
            criticality="safety",
            node=step_node,
            parent=node,
        )

    secret_names, secret_reason = syntax.secret_names(node)
    if secret_reason is None and secret_names is not None:
        evidence.fact(
            subject_id=subject_id,
            category="authority",
            field_name="job.secrets.static_names",
            value=secret_names,
            criticality="informational",
            node=node,
            parent=node,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="authority",
            field_name="job.secrets.static_names",
            reason=secret_reason or "secret_name_projection_failed",
            criticality="safety",
            node=node,
            parent=node,
        )

    provider_signal_name = name if name is not None and matrix == () and uses_node is None else None
    if provider_signal_name is None:
        evidence.unknown(
            subject_id=subject_id,
            category="identity",
            field_name="job.provider_signal_name",
            reason="explicit_non_matrix_native_job_name_not_proven",
            criticality="safety",
            node=name_node,
            parent=node,
        )
    else:
        evidence.fact(
            subject_id=subject_id,
            category="identity",
            field_name="job.provider_signal_name",
            value=provider_signal_name,
            criticality="safety",
            node=name_node,
            parent=node,
        )

    _effective_unknowns(evidence, subject_id, node)

    return JobSummary(
        subject_id=subject_id,
        job_id=job_id,
        name=name,
        needs=needs,
        uses=uses,
        uses_dynamic=uses_dynamic,
        runs_on=runs_on,
        permissions=declared_permissions,
        environment=environment,
        service_ids=services,
        timeout_minutes=timeout,
        concurrency=concurrency,
        matrix=matrix,
        condition=condition,
        steps=step_projection.steps,
        static_secret_names=secret_names,
        provider_signal_name=provider_signal_name,
    )


def _text_set_field(
    evidence: EvidenceBuilder,
    subject_id: str,
    parent: YamlMapping,
    key: str,
    field_name: str,
    category: EvidenceCategory,
) -> tuple[str, ...] | None:
    node = parent.get(key)
    value, reason = syntax.text_set(node)
    if reason is None and value is not None:
        evidence.fact(
            subject_id=subject_id,
            category=category,
            field_name=field_name,
            value=value,
            criticality="informational",
            node=node,
            parent=parent,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category=category,
            field_name=field_name,
            reason=reason or "sequence_projection_failed",
            criticality="safety",
            node=node,
            parent=parent,
        )
    return value


def _environment_field(
    evidence: EvidenceBuilder,
    subject_id: str,
    parent: YamlMapping,
) -> str | None:
    node = parent.get("environment")
    value, reason = syntax.environment(node)
    if reason is None:
        evidence.fact(
            subject_id=subject_id,
            category="authority",
            field_name="job.environment.declared",
            value=value,
            criticality="informational",
            node=node,
            parent=parent,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="authority",
            field_name="job.environment.declared",
            reason=reason,
            criticality="safety",
            node=node,
            parent=parent,
        )
    return value


def _services_field(
    evidence: EvidenceBuilder,
    subject_id: str,
    parent: YamlMapping,
) -> tuple[str, ...] | None:
    node = parent.get("services")
    value, reason = syntax.service_ids(node)
    if reason is None and value is not None:
        evidence.fact(
            subject_id=subject_id,
            category="execution",
            field_name="job.service_ids",
            value=value,
            criticality="informational",
            node=node,
            parent=parent,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="execution",
            field_name="job.service_ids",
            reason=reason or "services_projection_failed",
            criticality="safety",
            node=node,
            parent=parent,
        )
    return value


def _timeout_field(
    evidence: EvidenceBuilder,
    subject_id: str,
    parent: YamlMapping,
) -> int | None:
    node = parent.get("timeout-minutes")
    value = scalar_int(node)
    if node is None or (value is not None and value > 0):
        evidence.fact(
            subject_id=subject_id,
            category="execution",
            field_name="job.timeout_minutes",
            value=value,
            criticality="informational",
            node=node,
            parent=parent,
        )
        return value
    evidence.unknown(
        subject_id=subject_id,
        category="execution",
        field_name="job.timeout_minutes",
        reason="dynamic_or_invalid_timeout",
        criticality="informational",
        node=node,
        parent=parent,
    )
    return None


def _matrix_field(
    evidence: EvidenceBuilder,
    subject_id: str,
    parent: YamlMapping,
) -> tuple[MatrixDimension, ...] | None:
    strategy = parent.get("strategy")
    matrix_node = strategy.get("matrix") if isinstance(strategy, YamlMapping) else None
    value: tuple[MatrixDimension, ...] | None
    reason: str | None
    if strategy is not None and not isinstance(strategy, YamlMapping):
        value, reason = None, "unsupported_strategy_syntax"
    else:
        value, reason = syntax.matrix(matrix_node)
    if reason is None and value is not None:
        evidence.fact(
            subject_id=subject_id,
            category="execution",
            field_name="job.matrix",
            value=tuple((item.name, item.values) for item in value),
            criticality="informational",
            node=matrix_node or strategy,
            parent=parent,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="execution",
            field_name="job.matrix",
            reason=reason or "matrix_projection_failed",
            criticality="safety",
            node=matrix_node or strategy,
            parent=parent,
        )
    return value


def _effective_unknowns(
    evidence: EvidenceBuilder,
    subject_id: str,
    parent: YamlMapping,
) -> None:
    for field_name, category, reason in (
        ("job.condition.effective", "semantics", "runtime_condition_result_not_evaluated"),
        ("job.environment.protection", "authority", "environment_policy_not_fetched"),
        ("job.oidc_availability", "authority", "effective_oidc_not_proven"),
        ("job.permissions.effective", "authority", "effective_permissions_not_proven"),
        ("job.runner_availability", "execution", "runner_availability_not_observed"),
        ("job.secrets.existence", "authority", "secret_existence_not_observed"),
    ):
        evidence.unknown(
            subject_id=subject_id,
            category=category,  # type: ignore[arg-type]
            field_name=field_name,
            reason=reason,
            criticality="safety",
            node=None,
            parent=parent,
        )
