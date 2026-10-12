import asyncio
from dataclasses import dataclass, field, replace
from typing import Literal, cast

import pytest
from ci_economics.archive_factories import (
    ARCHIVE_TIME,
    history_dataset,
    history_discovery,
    history_scan,
)
from ci_economics.gap_recovery_factories import gap_command

from ci_coordinator.app import ci_history_administration
from ci_coordinator.app.ci_economics import CiEconomicsReadForbidden, CiEconomicsReadUnavailable
from ci_coordinator.app.ci_history_administration import CiHistoryAdministrationService
from ci_coordinator.ci_economics.archive_retention import DetailRetentionPolicy
from ci_coordinator.ci_economics.history_administration import (
    HistoryStatus,
    RepositoryCreationObserved,
    RepositoryCreationUnavailable,
)
from ci_coordinator.ci_economics.history_commands import (
    ConfigureHistory,
    HistoryConfigurationConflict,
    HistoryConfigurationInvalid,
    HistoryConfigurationResult,
    HistoryConfigured,
    HistoryPopulationSourceRefused,
)
from ci_coordinator.ci_economics.history_configuration import HistoryConfiguration, HistoryDefaults
from ci_coordinator.ci_economics.history_gap_recovery import (
    HistoryGapRepairInterval,
    HistoryGapRepairReceipt,
    HistoryGapRepairResult,
    RepairHistoryGaps,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable

DATASET = history_dataset()
COMMAND = ConfigureHistory(
    installationId=101,
    repositoryId=202,
    expectedRevision=0,
    configuration=DATASET.configuration,
    initialCreatedFrom=ARCHIVE_TIME.isoformat(),
    rescan=False,
    operationId="configure-history",
    actor="admin",
)
CATALOG_CREATED_AT = ARCHIVE_TIME.replace(hour=12, minute=34, second=56)
CATALOG_COMMAND = ConfigureHistory.model_validate(
    {
        **COMMAND.model_dump(),
        "populationBoundSource": {
            "kind": "repository_created_at",
            "repositoryCreatedAt": CATALOG_CREATED_AT.isoformat(),
        },
    }
)
RECEIPT = HistoryConfigured(DATASET, False)
STATUS = HistoryStatus(
    DATASET.scope,
    HistoryDefaults(1, DetailRetentionPolicy.default(), ARCHIVE_TIME),
    DATASET,
    history_scan(DATASET),
    0,
    ARCHIVE_TIME,
    history_discovery(DATASET),
)
GAP_COMMAND = gap_command()
GAP_RESULT = HistoryGapRepairResult(
    outcome="committed",
    operationId=GAP_COMMAND.operation_id,
    receipt=HistoryGapRepairReceipt(
        request=GAP_COMMAND.request,
        intervals=(HistoryGapRepairInterval(workflowRunId=303, fromAttempt=1, throughAttempt=1),),
    ),
)
type Operation = Literal["configure", "status", "repair"]


@dataclass
class Boundary:
    allowed: bool = True
    authorization_error: BaseException | None = None
    store_error: BaseException | None = None
    receipt: HistoryConfigurationResult = RECEIPT
    snapshot: HistoryStatus = STATUS
    gap_receipt: HistoryGapRepairResult = GAP_RESULT
    retained: HistoryConfigurationResult | None = None
    creation: RepositoryCreationObserved | RepositoryCreationUnavailable | BaseException = field(
        default_factory=lambda: RepositoryCreationObserved(COMMAND.scope, CATALOG_CREATED_AT)
    )
    calls: list[tuple[str, object]] = field(default_factory=list)

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        self.calls.append(("authorize", (actor, scope)))
        if self.authorization_error is not None:
            raise self.authorization_error
        return self.allowed

    def record(self, operation: str, argument: object) -> None:
        self.calls.append((operation, argument))
        if self.store_error is not None:
            raise self.store_error

    async def find_history_receipt(
        self, command: ConfigureHistory
    ) -> HistoryConfigurationResult | None:
        self.record("receipt", command)
        return self.retained

    async def configure_history(
        self,
        command: ConfigureHistory,
        *,
        verified_creation: RepositoryCreationObserved | None = None,
    ) -> HistoryConfigurationResult:
        self.record("configure", command if verified_creation is None else verified_creation)
        return self.receipt

    async def read_repository_created_at(
        self, scope: RepositoryScope
    ) -> RepositoryCreationObserved | RepositoryCreationUnavailable:
        self.calls.append(("provider", scope))
        if isinstance(self.creation, BaseException):
            raise self.creation
        return self.creation

    async def history_status(self, scope: RepositoryScope) -> HistoryStatus:
        self.record("status", scope)
        return self.snapshot

    async def repair_history_gaps(self, command: RepairHistoryGaps) -> HistoryGapRepairResult:
        self.record("repair", command)
        return self.gap_receipt

    def service(self) -> CiHistoryAdministrationService:
        return CiHistoryAdministrationService(authorizer=self, store=self, creation_reader=self)


async def invoke(boundary: Boundary, operation: Operation) -> object:
    service = boundary.service()
    if operation == "configure":
        return await service.configure(COMMAND)
    if operation == "repair":
        return await service.repair(GAP_COMMAND)
    return await service.status(actor=COMMAND.actor, scope=COMMAND.scope)


@pytest.mark.parametrize("operation", ["configure", "status", "repair"])
@pytest.mark.parametrize("authority", ["allow", "deny", "unavailable", "malformed"])
async def test_exact_current_authority_precedes_every_storage_effect(
    operation: Operation,
    authority: str,
) -> None:
    boundary = Boundary(
        allowed=True
        if authority == "allow"
        else cast(bool, 1)
        if authority == "malformed"
        else False,
        authorization_error=RepositoryAccessUnavailable() if authority == "unavailable" else None,
    )
    result = await invoke(boundary, operation)
    assert boundary.calls[0] == ("authorize", (COMMAND.actor, COMMAND.scope))
    if authority == "allow":
        assert result == {"configure": RECEIPT, "status": STATUS, "repair": GAP_RESULT}[operation]
        assert boundary.calls[1:] == [
            (
                operation,
                {"configure": COMMAND, "status": COMMAND.scope, "repair": GAP_COMMAND}[operation],
            )
        ]
    else:
        assert len(boundary.calls) == 1
        assert result == (
            CiEconomicsReadForbidden() if authority == "deny" else CiEconomicsReadUnavailable()
        )


@pytest.mark.parametrize("operation", ["configure", "status", "repair"])
@pytest.mark.parametrize("failure", [CiEconomicsStoreUnavailable(), TimeoutError()])
async def test_storage_unavailability_is_not_reported_as_success_or_rollback(
    operation: Operation,
    failure: BaseException,
) -> None:
    boundary = Boundary(store_error=failure)
    assert await invoke(boundary, operation) == CiEconomicsReadUnavailable()
    assert len(boundary.calls) == 2


@pytest.mark.parametrize(
    "receipt",
    [
        HistoryConfigured(replace(DATASET, scope=RepositoryScope(101, 203)), False),
        HistoryConfigured(replace(DATASET, configuration_revision=2), False),
        HistoryConfigured(
            replace(
                DATASET,
                configuration=HistoryConfiguration.model_validate(
                    {**DATASET.configuration.model_dump(), "workflowIds": [99]}
                ),
            ),
            False,
        ),
        cast(HistoryConfigurationResult, {}),
    ],
)
async def test_configuration_rejects_each_foreign_success_operand(
    receipt: HistoryConfigurationResult,
) -> None:
    assert await invoke(Boundary(receipt=receipt), "configure") == CiEconomicsReadUnavailable()


@pytest.mark.parametrize(
    "receipt",
    [
        HistoryConfigured(DATASET, True),
        HistoryConfigurationInvalid(),
        *(
            HistoryConfigurationConflict(reason)
            for reason in (
                "revision_conflict",
                "operation_conflict",
                "capacity_reached",
                "dataset_fenced",
            )
        ),
    ],
)
async def test_exact_replay_and_typed_rejections_are_preserved(
    receipt: HistoryConfigurationResult,
) -> None:
    assert await invoke(Boundary(receipt=receipt), "configure") == receipt


@pytest.mark.parametrize("foreign", [False, True])
async def test_status_rejects_a_foreign_scope_or_malformed_result(foreign: bool) -> None:
    scope = RepositoryScope(101, 203)
    result = (
        HistoryStatus(scope, STATUS.defaults, None, None, 0, ARCHIVE_TIME)
        if foreign
        else cast(HistoryStatus, {})
    )
    assert await invoke(Boundary(snapshot=result), "status") == CiEconomicsReadUnavailable()


@pytest.mark.parametrize("operation", ["configure", "status", "repair"])
@pytest.mark.parametrize("stage", ["authorize", "store"])
async def test_external_cancellation_propagates_at_each_effect_boundary(
    operation: Operation,
    stage: str,
) -> None:
    boundary = Boundary(
        authorization_error=asyncio.CancelledError() if stage == "authorize" else None,
        store_error=asyncio.CancelledError() if stage == "store" else None,
    )
    with pytest.raises(asyncio.CancelledError):
        await invoke(boundary, operation)
    assert len(boundary.calls) == (1 if stage == "authorize" else 2)


@pytest.mark.parametrize("operation", ["configure", "status", "repair"])
async def test_authorization_and_storage_share_one_deadline(
    monkeypatch: pytest.MonkeyPatch,
    operation: Operation,
) -> None:
    deadline = asyncio.Timeout(None)
    budgets: list[float] = []

    def timeout(seconds: float) -> asyncio.Timeout:
        budgets.append(seconds)
        return deadline

    class DelayedBoundary(Boundary):
        async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
            allowed = await super().allows_scope(actor=actor, scope=scope)
            deadline.reschedule(asyncio.get_running_loop().time())
            return allowed

        async def history_status(self, scope: RepositoryScope) -> HistoryStatus:
            await asyncio.Event().wait()
            raise AssertionError("operation continued past the shared deadline")

        async def configure_history(
            self,
            command: ConfigureHistory,
            *,
            verified_creation: RepositoryCreationObserved | None = None,
        ) -> HistoryConfigurationResult:
            await asyncio.Event().wait()
            raise AssertionError("operation continued past the shared deadline")

        async def repair_history_gaps(self, command: RepairHistoryGaps) -> HistoryGapRepairResult:
            await asyncio.Event().wait()
            raise AssertionError("operation continued past the shared deadline")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(asyncio, "timeout", timeout)
    assert await invoke(DelayedBoundary(), operation) == CiEconomicsReadUnavailable()
    assert budgets == [ci_history_administration.HISTORY_ADMINISTRATION_DEADLINE_SECONDS]
    assert deadline.expired()


@pytest.mark.parametrize("variant", ["malformed", "scope", "generation", "operation"])
async def test_gap_repair_rejects_foreign_or_malformed_result(variant: str) -> None:
    assert GAP_RESULT.receipt is not None
    update: dict[str, object] = (
        {"installationId": 999}
        if variant == "scope"
        else {"generation": 2}
        if variant == "generation"
        else {"operationId": "other"}
    )
    request = {**GAP_COMMAND.request.model_dump(), **update}
    result = (
        cast(HistoryGapRepairResult, {})
        if variant == "malformed"
        else HistoryGapRepairResult.model_validate(
            {
                "outcome": "committed",
                "operationId": request["operationId"],
                "receipt": {"request": request, "intervals": GAP_RESULT.receipt.intervals},
            }
        )
    )
    assert await invoke(Boundary(gap_receipt=result), "repair") == CiEconomicsReadUnavailable()


async def test_catalog_source_reads_provider_after_authorization_before_store() -> None:
    # Kills: a provider read before authorization or inside the store transaction.
    boundary = Boundary()
    assert await boundary.service().configure(CATALOG_COMMAND) == RECEIPT
    assert boundary.calls == [
        ("authorize", (COMMAND.actor, COMMAND.scope)),
        ("receipt", CATALOG_COMMAND),
        ("provider", COMMAND.scope),
        ("configure", RepositoryCreationObserved(COMMAND.scope, CATALOG_CREATED_AT)),
    ]


async def test_forbidden_actor_never_reads_provider() -> None:
    boundary = Boundary(allowed=False)
    assert await boundary.service().configure(CATALOG_COMMAND) == CiEconomicsReadForbidden()
    assert boundary.calls == [("authorize", (COMMAND.actor, COMMAND.scope))]


async def test_explicit_request_never_reads_provider() -> None:
    # Kills: a provider read added to ordinary explicit-date saves.
    boundary = Boundary(creation=AssertionError("explicit save read the provider"))
    assert await boundary.service().configure(COMMAND) == RECEIPT
    assert [call for call, _ in boundary.calls] == ["authorize", "configure"]


async def test_retained_receipt_replays_without_provider_read() -> None:
    # Kills: re-reading GitHub on retained-receipt replay.
    replay = HistoryConfigured(DATASET, True)
    boundary = Boundary(retained=replay, creation=AssertionError("replay read the provider"))
    assert await boundary.service().configure(CATALOG_COMMAND) == replay
    assert [call for call, _ in boundary.calls] == ["authorize", "receipt"]


@pytest.mark.parametrize(
    "creation,reason",
    [
        (RepositoryCreationObserved(COMMAND.scope, None), "creation_date_missing"),
        (
            RepositoryCreationObserved(COMMAND.scope, CATALOG_CREATED_AT.replace(second=57)),
            "creation_date_mismatch",
        ),
    ],
)
async def test_missing_or_mismatched_creation_date_refuses_without_store_call(
    creation: RepositoryCreationObserved, reason: str
) -> None:
    # Kills: accepting the client assertion, or substituting a fallback floor.
    boundary = Boundary(creation=creation)
    assert await boundary.service().configure(CATALOG_COMMAND) == HistoryPopulationSourceRefused(
        cast(Literal["creation_date_missing", "creation_date_mismatch"], reason)
    )
    assert [call for call, _ in boundary.calls] == ["authorize", "receipt", "provider"]


@pytest.mark.parametrize(
    "creation",
    [
        RepositoryCreationUnavailable("provider_unavailable"),
        RepositoryCreationUnavailable("provider_malformed"),
        RepositoryCreationUnavailable("provider_binding_mismatch"),
        RepositoryCreationObserved(RepositoryScope(101, 203), CATALOG_CREATED_AT),
    ],
)
async def test_provider_unavailable_or_binding_mismatch_is_unavailable_and_opens_no_transaction(
    creation: RepositoryCreationObserved | RepositoryCreationUnavailable,
) -> None:
    # Kills: treating provider failure as missing metadata (422), or proceeding to the store.
    boundary = Boundary(creation=creation)
    assert await boundary.service().configure(CATALOG_COMMAND) == CiEconomicsReadUnavailable()
    assert "configure" not in [call for call, _ in boundary.calls]


async def test_cancellation_during_provider_read_propagates_without_store_call() -> None:
    boundary = Boundary(creation=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await boundary.service().configure(CATALOG_COMMAND)
    assert [call for call, _ in boundary.calls] == ["authorize", "receipt", "provider"]


async def test_deadline_covers_provider_read(monkeypatch: pytest.MonkeyPatch) -> None:
    # Kills: reading the provider outside the shared administration deadline.
    deadline = asyncio.Timeout(None)
    monkeypatch.setattr(asyncio, "timeout", lambda _seconds: deadline)

    class SlowProvider(Boundary):
        async def read_repository_created_at(
            self, scope: RepositoryScope
        ) -> RepositoryCreationObserved | RepositoryCreationUnavailable:
            self.calls.append(("provider", scope))
            deadline.reschedule(asyncio.get_running_loop().time())
            await asyncio.Event().wait()
            raise AssertionError("provider read continued past the shared deadline")

    boundary = SlowProvider()
    assert await boundary.service().configure(CATALOG_COMMAND) == CiEconomicsReadUnavailable()
    assert "configure" not in [call for call, _ in boundary.calls]
    assert deadline.expired()


@pytest.mark.parametrize(
    "reason", ["creation_date_missing", "creation_date_mismatch", "creation_date_in_future"]
)
async def test_store_population_refusal_is_preserved(reason: str) -> None:
    refusal = HistoryPopulationSourceRefused(
        cast(
            Literal["creation_date_missing", "creation_date_mismatch", "creation_date_in_future"],
            reason,
        )
    )
    assert await Boundary(receipt=refusal).service().configure(CATALOG_COMMAND) == refusal
