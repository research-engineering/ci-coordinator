from ci_coordinator.workflow_discovery import _syntax_fields as syntax
from ci_coordinator.workflow_discovery._evidence_builder import EvidenceBuilder
from ci_coordinator.workflow_discovery._yaml_nodes import YamlMapping
from ci_coordinator.workflow_discovery.evidence import FactValue
from ci_coordinator.workflow_discovery.summary import ConcurrencySummary, DeclaredPermissions


def permissions_field(
    evidence: EvidenceBuilder, subject_id: str, parent: YamlMapping, *, field_name: str
) -> DeclaredPermissions | None:
    node = parent.get("permissions")
    value, reason = syntax.permissions(node)
    if reason is None and value is not None:
        evidence.fact(
            subject_id=subject_id,
            category="authority",
            field_name=field_name,
            value=_permissions_value(value),
            criticality="informational",
            node=node,
            parent=parent,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="authority",
            field_name=field_name,
            reason=reason or "permissions_projection_failed",
            criticality="safety",
            node=node,
            parent=parent,
        )
    return value


def concurrency_field(
    evidence: EvidenceBuilder, subject_id: str, parent: YamlMapping, *, field_name: str
) -> ConcurrencySummary | None:
    node = parent.get("concurrency")
    value, reason = syntax.concurrency(node)
    if reason is None and value is not None:
        evidence.fact(
            subject_id=subject_id,
            category="execution",
            field_name=field_name,
            value=(value.group, value.cancel_in_progress),
            criticality="informational",
            node=node,
            parent=parent,
        )
    else:
        evidence.unknown(
            subject_id=subject_id,
            category="execution",
            field_name=field_name,
            reason=reason or "concurrency_projection_failed",
            criticality="safety",
            node=node,
            parent=parent,
        )
    return value


def _permissions_value(value: DeclaredPermissions) -> FactValue:
    if value.kind == "all":
        return (value.kind, value.all_level)
    return (value.kind, tuple((item.name, item.level) for item in value.entries))
