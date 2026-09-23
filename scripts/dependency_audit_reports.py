from __future__ import annotations

from collections import Counter
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from scripts.proofkit_common import parse_json_object

Count = Annotated[int, Field(ge=0)]
Text = Annotated[str, Field(min_length=1)]
PackageIdentity = tuple[str, str]


class AuditFindingsError(ValueError):
    pass


class AuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class UvSchema(AuditRecord):
    version: Literal["preview"]


class UvSummary(AuditRecord):
    audited_packages: Count
    vulnerabilities: Count
    adverse_statuses: Count


class UvDependency(AuditRecord):
    name: Text
    version: Text


class UvVulnerability(AuditRecord):
    dependency: UvDependency
    id: Text
    display_id: Text
    aliases: list[str]
    summary: str | None
    description: str | None
    link: str | None
    fix_versions: list[str]
    published: str | None
    modified: str | None


class UvAdverseStatus(AuditRecord):
    name: Text
    status: Text
    reason: str | None


class UvAuditReport(AuditRecord):
    report_schema: UvSchema = Field(alias="schema")
    summary: UvSummary
    vulnerabilities: list[UvVulnerability]
    adverse_statuses: list[UvAdverseStatus]


class NpmCounts(AuditRecord):
    info: Count
    low: Count
    moderate: Count
    high: Count
    critical: Count


class NpmMetadata(AuditRecord):
    vulnerabilities: NpmCounts
    dependencies: Count
    devDependencies: Count
    optionalDependencies: Count
    totalDependencies: Count


class NpmFinding(AuditRecord):
    version: Text
    paths: Annotated[list[Text], Field(min_length=1)]
    dev: bool
    optional: bool
    bundled: bool


class NpmAdvisory(AuditRecord):
    findings: Annotated[list[NpmFinding], Field(min_length=1)]
    id: Count
    title: str
    module_name: Text
    vulnerable_versions: Text
    patched_versions: str | None = None
    patched_versions_unpublished: bool | None = None
    severity: Literal["info", "low", "moderate", "high", "critical"]
    cwe: str
    github_advisory_id: str
    url: str


class NpmAuditReport(AuditRecord):
    advisories: dict[str, NpmAdvisory]
    metadata: NpmMetadata


def admit_uv_report(content: str, packages: frozenset[PackageIdentity]) -> UvAuditReport:
    report = UvAuditReport.model_validate(parse_json_object(content, "uv audit"))
    if not packages or report.summary.audited_packages != len(packages):
        raise ValueError("uv audit package cardinality differs from the admitted lock inventory")
    if report.summary.vulnerabilities != len(
        report.vulnerabilities
    ) or report.summary.adverse_statuses != len(report.adverse_statuses):
        raise ValueError("uv audit finding counters are inconsistent")
    identifiers = [
        (item.dependency.name, item.dependency.version, item.id) for item in report.vulnerabilities
    ]
    if len(identifiers) != len(set(identifiers)) or any(
        (name, version) not in packages for name, version, _identifier in identifiers
    ):
        raise ValueError("uv audit contains duplicate or foreign findings")
    names = {name for name, _version in packages}
    if any(item.name not in names for item in report.adverse_statuses):
        raise ValueError("uv audit adverse status is outside the admitted lock inventory")
    if report.vulnerabilities:
        raise AuditFindingsError("uv audit reports unaccepted vulnerabilities")
    return report


def admit_npm_report(content: str, packages: frozenset[PackageIdentity]) -> NpmAuditReport:
    report = NpmAuditReport.model_validate(parse_json_object(content, "pnpm audit"))
    metadata = report.metadata
    if not packages or metadata.totalDependencies != len(packages):
        raise ValueError("pnpm audit package cardinality differs from the admitted lock inventory")
    if any(
        value > metadata.totalDependencies
        for value in (
            metadata.dependencies,
            metadata.devDependencies,
            metadata.optionalDependencies,
        )
    ):
        raise ValueError("pnpm audit dependency counters are inconsistent")
    severities: Counter[str] = Counter()
    for key, advisory in report.advisories.items():
        if key != str(advisory.id) or advisory.severity not in {"high", "critical"}:
            raise ValueError("pnpm audit advisory identity or severity differs from its command")
        severities[advisory.severity] += 1
        for finding in advisory.findings:
            if (advisory.module_name, finding.version) not in packages:
                raise ValueError("pnpm audit finding is outside the admitted lock inventory")
    if (
        severities["high"] != metadata.vulnerabilities.high
        or severities["critical"] != metadata.vulnerabilities.critical
    ):
        raise ValueError("pnpm audit high-severity findings were omitted or counted inconsistently")
    if report.advisories:
        raise AuditFindingsError("pnpm audit reports unaccepted high or critical vulnerabilities")
    return report
