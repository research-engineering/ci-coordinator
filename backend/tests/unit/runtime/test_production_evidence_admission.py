from __future__ import annotations

import asyncio
from dataclasses import fields

import pytest
from anyio import BrokenWorkerProcess, CapacityLimiter, to_process
from production_cutover_support import ProductionCutoverFixture, production_cutover_fixture

from ci_coordinator.production_admission.ports import ProductionEvidenceAdmissionUnavailable
from ci_coordinator.runtime import production_evidence_admission as worker


@pytest.fixture(scope="module")
def evidence_seed() -> ProductionCutoverFixture:
    return production_cutover_fixture()


@pytest.fixture
def fixture(evidence_seed: ProductionCutoverFixture) -> ProductionCutoverFixture:
    return evidence_seed.isolated_copy()


@pytest.mark.parametrize("operand", ("staged", "policy", "envelope"))
def test_fixture_copy_preserves_seed_and_later_cases(
    evidence_seed: ProductionCutoverFixture, operand: str
) -> None:
    copied = evidence_seed.isolated_copy()
    _assert_isolated_fixture(copied, evidence_seed)
    target, seed_target, field = {
        "staged": (copied.staged, evidence_seed.staged, "canonical_bytes"),
        "policy": (copied.draft, evidence_seed.draft, "source_bytes"),
        "envelope": (copied.signed, evidence_seed.signed, "content"),
    }[operand]
    before = getattr(seed_target, field)
    object.__setattr__(target, field, b"corrupted-copy")
    assert getattr(seed_target, field) == before
    assert getattr(target, field) == b"corrupted-copy"
    _assert_isolated_fixture(evidence_seed.isolated_copy(), evidence_seed)


def _assert_isolated_fixture(
    copied: ProductionCutoverFixture, seed: ProductionCutoverFixture
) -> None:
    assert copied is not seed
    for field in fields(seed):
        if field.name != "signed":
            assert getattr(copied, field.name) == getattr(seed, field.name)
    assert copied.signing_key is seed.signing_key
    assert copied.signed is not seed.signed
    assert copied.signed.content == seed.signed.content
    assert copied.signed.public_key_pem == seed.signed.public_key_pem
    assert copied.signed.receipt == seed.signed.receipt
    grant, original = copied.signed.grant, seed.signed.grant
    assert grant is not original
    assert grant.authority_id == original.authority_id
    assert grant.registration is not original.registration
    assert (
        grant.registration.envelope_canonical_json == original.registration.envelope_canonical_json
    )


def _arguments(fixture: ProductionCutoverFixture) -> tuple[bytes, object, object, tuple[str, ...]]:
    return (
        fixture.staged.canonical_bytes,
        fixture.staged.scope_grant,
        fixture.draft,
        fixture.staged.lookup.provider_paths,
    )


async def _run(fixture: ProductionCutoverFixture) -> object:
    return await worker.ProcessProductionEvidenceAdmission()(
        fixture.staged.canonical_bytes,
        fixture.staged.scope_grant,
        fixture.draft,
        fixture.staged.lookup.provider_paths,
    )


def test_real_worker_preserves_opaque_admission_and_all_bound_operands(
    fixture: ProductionCutoverFixture,
) -> None:
    assert asyncio.run(_run(fixture)) == fixture.staged


@pytest.mark.parametrize("content", (b"", b"not-json", b"{}", b"[]"))
def test_real_worker_rejects_unreplayable_bytes(
    fixture: ProductionCutoverFixture, content: bytes
) -> None:
    async def invalid() -> None:
        with pytest.raises(ValueError, match=r"^production evidence is invalid$") as rejected:
            await worker.ProcessProductionEvidenceAdmission()(
                content,
                fixture.staged.scope_grant,
                fixture.draft,
                fixture.staged.lookup.provider_paths,
            )
        assert type(rejected.value) is ValueError
        assert rejected.value.__cause__ is None
        assert await _run(fixture) == fixture.staged

    asyncio.run(invalid())


def test_no_queue_is_process_wide_and_cancellation_releases_it(
    fixture: ProductionCutoverFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        calls = 0

        async def run_sync(
            function: object,
            *args: object,
            cancellable: bool = False,
            limiter: CapacityLimiter | None = None,
        ) -> object:
            nonlocal calls
            assert function is worker._admit
            assert args == _arguments(fixture)
            assert cancellable and limiter is not None and limiter.total_tokens == 1
            calls += 1
            if calls == 1:
                started.set()
                await asyncio.Future()
            return fixture.staged

        monkeypatch.setattr(to_process, "run_sync", run_sync)
        first = asyncio.create_task(_run(fixture))
        await asyncio.wait_for(started.wait(), timeout=2)
        try:
            with pytest.raises(ProductionEvidenceAdmissionUnavailable):
                await _run(fixture)
            assert calls == 1
        finally:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        assert await _run(fixture) == fixture.staged

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [BrokenWorkerProcess, OSError, TimeoutError])
def test_worker_failures_are_unavailability_and_do_not_leak_capacity(
    fixture: ProductionCutoverFixture,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[Exception],
) -> None:
    async def scenario() -> None:
        async def broken(*_: object, **__: object) -> object:
            raise failure

        monkeypatch.setattr(to_process, "run_sync", broken)
        with pytest.raises(ProductionEvidenceAdmissionUnavailable):
            await _run(fixture)

        async def completed(*_: object, **__: object) -> object:
            return fixture.staged

        monkeypatch.setattr(to_process, "run_sync", completed)
        assert await _run(fixture) == fixture.staged

    asyncio.run(scenario())


def test_worker_deadline_cancels_instead_of_waiting_forever(
    fixture: ProductionCutoverFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        cancelled = asyncio.Event()

        async def never_complete(*_: object, **__: object) -> object:
            try:
                return await asyncio.Future[object]()
            finally:
                cancelled.set()

        monkeypatch.setattr(to_process, "run_sync", never_complete)
        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(worker, "PRODUCTION_STAGE_TIMEOUT_SECONDS", 0.01)
        with pytest.raises(ProductionEvidenceAdmissionUnavailable):
            await _run(fixture)
        assert cancelled.is_set()

    asyncio.run(scenario())


def test_successful_but_foreign_worker_result_never_becomes_admitted(
    fixture: ProductionCutoverFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        foreign = production_cutover_fixture(generation=2).staged

        async def substituted(*_: object, **__: object) -> object:
            return foreign

        monkeypatch.setattr(to_process, "run_sync", substituted)
        with pytest.raises(ProductionEvidenceAdmissionUnavailable):
            await _run(fixture)

    asyncio.run(scenario())
