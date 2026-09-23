from __future__ import annotations

from dataclasses import dataclass, field, replace

import pytest
from ci_economics.factories import ATTEMPT
from ci_economics.report_factories import measurement_report, stored_report
from control_plane_http_support import (
    StaticControlPlaneAuthenticator,
    StaticRoleAdmission,
    human_principal,
)
from fastapi.testclient import TestClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    HttpRouteDependencies,
    InvalidCredential,
    MeasurementReportReadRouteDependencies,
)
from ci_coordinator.app.ci_measurement_reports import MeasurementReportReadService
from ci_coordinator.ci_economics.catalog import MeasurementReportPage
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.ci_economics.reports import ReportMeasurement, StoredMeasurementReport
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import ControlPlanePrincipal

_ROOT = "/api/v2/economics/repositories/101/202"
_PRINCIPAL = human_principal()


@dataclass
class _Query:
    records: dict[str, StoredMeasurementReport]
    calls: list[str] = field(default_factory=list)
    unavailable: bool = False

    async def list_measurement_reports(
        self, scope: RepositoryScope, source_id: str, *, after_cursor: str | None, limit: int
    ) -> MeasurementReportPage | None:
        raise AssertionError("unexpected report catalog read")

    async def load_measurement_report(
        self, scope: RepositoryScope, report_id: str
    ) -> StoredMeasurementReport | None:
        assert scope == ATTEMPT.scope
        self.calls.append(report_id)
        if self.unavailable:
            raise CiEconomicsStoreUnavailable("offline")
        return self.records.get(report_id)


@dataclass
class _Authorizer:
    allowed: bool = True

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        assert actor == human_principal().actor_id and scope == ATTEMPT.scope
        return self.allowed


def _client(
    query: _Query,
    *,
    identity: ControlPlanePrincipal
    | InvalidCredential
    | AuthenticationDependencyUnavailable = _PRINCIPAL,
    allowed: bool = True,
) -> TestClient:
    return TestClient(
        create_app(
            dependencies=HttpRouteDependencies(
                ci_measurement_reports=MeasurementReportReadRouteDependencies(
                    authenticator=StaticControlPlaneAuthenticator(identity),
                    role_admission=StaticRoleAdmission(),
                    use_case=MeasurementReportReadService(
                        authorizer=_Authorizer(allowed), query=query
                    ),
                )
            )
        )
    )


@pytest.mark.parametrize("maximum,outcome", [(999, "breached"), (1000, "within_budget")])
def test_budget_read_discloses_caller_threshold_and_exact_window(
    maximum: int, outcome: str
) -> None:
    record = stored_report()
    query = _Query({record.report.report_id: record})
    response = _client(query).get(
        f"{_ROOT}/reports/{record.report.report_id}/budget",
        params={"counter": "cpu_user", "maximumUs": maximum},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["schemaVersion"] == "ci-economics-report-budget/v1"
    assert (body["outcome"], body["maximumUs"]) == (outcome, maximum)
    assert body["thresholdAuthority"] == "caller_supplied"
    assert body["evaluationWindow"] == "exact_report"
    assert body["report"]["reportId"] == record.report.report_id
    assert body["measurement"]["counter"] == "cpu_user"
    assert body["measurement"]["scope"] == "waited_children"
    assert query.calls == [record.report.report_id]


@pytest.mark.parametrize(
    "counter,maximum", [("cpu_total", "1"), ("cpu_user", "-1"), ("cpu_user", "1.2")]
)
def test_invalid_budget_does_not_reach_storage(counter: str, maximum: str) -> None:
    query = _Query({})
    response = _client(query).get(
        f"{_ROOT}/reports/{'a' * 64}/budget", params={"counter": counter, "maximumUs": maximum}
    )
    assert response.status_code == 422 and not query.calls


def test_retained_report_read_preserves_canonical_payload_and_receiver_provenance() -> None:
    record = stored_report()
    query = _Query({record.report.report_id: record})
    response = _client(query).get(f"{_ROOT}/reports/{record.report.report_id}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["payload"] == record.report.canonical_mapping()
    assert data["reportDigest"] == record.report.report_digest
    assert data["source"]["sourceId"] == record.source.source_id
    assert data["origin"] == {"producerClaimHash": "a" * 64, "providerBindingDigest": "b" * 64}
    assert data["receivedAt"] and data["retainUntil"]
    assert query.calls == [record.report.report_id]


@pytest.mark.parametrize("mismatch", (False, True))
def test_comparison_discloses_matched_assertions_and_never_claims_verified_saving(
    mismatch: bool,
) -> None:
    baseline = stored_report()
    treatment = stored_report(
        replace(
            measurement_report(attempt=replace(ATTEMPT, run_attempt=3)),
            measurements=(
                ReportMeasurement("cpu_user", 1_200),
                ReportMeasurement("cpu_system", 100),
                ReportMeasurement("elapsed", 700),
            ),
        )
    )
    if mismatch:
        treatment = stored_report(
            replace(
                treatment.report,
                workload=replace(treatment.report.workload, cache_class_digest="e" * 64),
            )
        )
    query = _Query({record.report.report_id: record for record in (baseline, treatment)})
    response = _client(query).get(
        f"{_ROOT}/report-comparisons",
        params={
            "baselineReportId": baseline.report.report_id,
            "treatmentReportId": treatment.report.report_id,
        },
    )
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["coverageStatus"] == "not_verified" and data["causalStatus"] == "not_established"
    assert data["mismatches"] == (["cache_class"] if mismatch else [])
    if mismatch:
        assert data["differences"] == []
    else:
        assert {row["counter"]: row["reduction"] for row in data["differences"]} == {
            "cpu_system": 100,
            "cpu_user": -200,
            "elapsed": 100,
        }
        assert data["differences"][1]["relativeReduction"] == {
            "numerator": -200,
            "denominator": 1_000,
        }
        assert {row["unit"] for row in data["differences"]} == {"microsecond"}
    assert query.calls == [baseline.report.report_id, treatment.report.report_id]


@pytest.mark.parametrize("route", ("report", "pair"))
@pytest.mark.parametrize(
    ("identity", "allowed", "expected"),
    [
        (InvalidCredential(), True, 401),
        (AuthenticationDependencyUnavailable(), True, 503),
        (human_principal(roles=frozenset({"configure"})), True, 403),
        (human_principal(), False, 403),
    ],
)
def test_report_routes_reject_unauthorized_reads_before_store(
    route: str,
    identity: ControlPlanePrincipal | InvalidCredential | AuthenticationDependencyUnavailable,
    allowed: bool,
    expected: int,
) -> None:
    query = _Query({})
    url = (
        f"{_ROOT}/reports/{'a' * 64}"
        if route == "report"
        else f"{_ROOT}/report-comparisons?baselineReportId={'a' * 64}&treatmentReportId={'b' * 64}"
    )
    response = _client(query, identity=identity, allowed=allowed).get(url)
    assert response.status_code == expected and response.headers["cache-control"] == "no-store"
    assert query.calls == []
    if expected == 401:
        assert "www-authenticate" in response.headers


@pytest.mark.parametrize("unavailable", (False, True))
def test_report_absence_is_distinct_from_store_failure(unavailable: bool) -> None:
    response = _client(_Query({}, unavailable=unavailable)).get(f"{_ROOT}/reports/{'a' * 64}")
    assert response.status_code == (503 if unavailable else 404)
    assert response.json() == {"ok": False, "error": "unavailable" if unavailable else "not_found"}


@pytest.mark.parametrize(
    "suffix",
    (
        "/reports/invalid",
        "/report-comparisons",
        f"/report-comparisons?baselineReportId={'A' * 64}&treatmentReportId={'b' * 64}",
    ),
)
def test_report_path_and_pair_contracts_reject_invalid_identity(suffix: str) -> None:
    query = _Query({})
    response = _client(query).get(_ROOT + suffix)
    assert response.status_code == 422 and query.calls == []
