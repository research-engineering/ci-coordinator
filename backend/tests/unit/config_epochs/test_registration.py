from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest
from config_epoch_support import admitted_config_epoch
from repository_activation_support import ACTOR, SCOPE

from ci_coordinator.config_epochs import (
    CONFIG_EPOCH_REGISTRATION_EVENT_TYPE,
    ConfigEpochRegistrationCommand,
    ConfigEpochRegistrationCommitted,
    ConfigEpochRegistrationOperationConflict,
    ConfigEpochRegistrationRecord,
    ConfigEpochRegistrationReplay,
    PreparedConfigEpochRegistration,
    is_pair_owned_config_registration_event_type,
    prepare_config_epoch_registration,
)

_OCCURRED_AT = "2026-09-01T10:00:00.000Z"


def test_prepared_registration_binds_every_client_owned_fact_to_one_audit_input() -> None:
    draft = admitted_config_epoch()
    prepared = prepare_config_epoch_registration(_command())

    assert prepared.command == _command()
    event = prepared._take_audit_event()
    assert event.event_type == CONFIG_EPOCH_REGISTRATION_EVENT_TYPE
    assert event.actor == ACTOR
    assert event.created_at == _OCCURRED_AT
    assert json.loads(event.payload_canonical_bytes) == {
        "documentHash": draft.document_hash,
        "epochHash": draft.epoch_hash,
        "epochId": draft.epoch_id,
        "operationId": "register-1",
        "schemaVersion": "config-epoch-registration-audit/v1",
        "sourceFormat": "json",
        "sourceHash": draft.source_hash,
    }


def test_prepared_registration_is_single_use_and_cannot_be_forged() -> None:
    prepared = prepare_config_epoch_registration(_command())

    prepared._take_audit_event()

    with pytest.raises(RuntimeError, match="already been consumed"):
        prepared._take_audit_event()
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        PreparedConfigEpochRegistration(
            object(),
            command=_command(),
            audit_event=prepare_config_epoch_registration(_command())._take_audit_event(),
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda command: replace(command, operation_id="register-2"),
        lambda command: replace(command, draft=admitted_config_epoch("yaml-1.2")),
    ],
)
def test_each_idempotent_registration_field_changes_the_audit_input_hash(
    mutate: Callable[[ConfigEpochRegistrationCommand], ConfigEpochRegistrationCommand],
) -> None:
    baseline = prepare_config_epoch_registration(_command())
    mutated = prepare_config_epoch_registration(mutate(_command()))

    assert mutated.audit_input_hash != baseline.audit_input_hash


@pytest.mark.parametrize(
    "mutate",
    [
        lambda command: replace(command, actor="keycloak-human:v1:" + "b" * 64),
        lambda command: replace(command, occurred_at="2026-09-01T10:00:00.001Z"),
    ],
)
def test_registration_provenance_does_not_change_the_idempotent_input_hash(
    mutate: Callable[[ConfigEpochRegistrationCommand], ConfigEpochRegistrationCommand],
) -> None:
    baseline = prepare_config_epoch_registration(_command())
    mutated = prepare_config_epoch_registration(mutate(_command()))

    assert mutated.audit_input_hash == baseline.audit_input_hash


@pytest.mark.parametrize(
    "mutate",
    [
        lambda command: replace(command, operation_id=""),
        lambda command: replace(command, operation_id="x" * 257),
        lambda command: replace(command, actor="actor\x00suffix"),
        lambda command: replace(command, occurred_at=""),
    ],
)
def test_registration_command_rejects_unpersistable_text(
    mutate: Callable[[ConfigEpochRegistrationCommand], ConfigEpochRegistrationCommand],
) -> None:
    with pytest.raises(ValueError, match="bounded Unicode scalar text"):
        mutate(_command())


def test_registration_receipt_requires_exact_scope_and_hash_identities() -> None:
    draft = admitted_config_epoch()
    record = ConfigEpochRegistrationRecord(
        scope=SCOPE,
        operation_id="register-1",
        epoch_id=draft.epoch_id,
        audit_event_id="audit_" + "a" * 32,
        audit_input_hash="b" * 64,
    )

    assert record.scope == draft.scope
    assert is_pair_owned_config_registration_event_type(CONFIG_EPOCH_REGISTRATION_EVENT_TYPE)
    assert not is_pair_owned_config_registration_event_type("config-epoch-activation/v1")

    with pytest.raises(ValueError, match="audit event identity"):
        replace(record, audit_event_id="audit_invalid")
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        replace(record, audit_input_hash="B" * 64)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ConfigEpochRegistrationCommitted(cast(ConfigEpochRegistrationRecord, object())),
        lambda: ConfigEpochRegistrationReplay(cast(ConfigEpochRegistrationRecord, object())),
        lambda: ConfigEpochRegistrationOperationConflict(
            cast(ConfigEpochRegistrationRecord, object())
        ),
    ],
)
def test_registration_results_require_exact_records(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(TypeError, match="exact record"):
        factory()


def _command() -> ConfigEpochRegistrationCommand:
    return ConfigEpochRegistrationCommand(
        draft=admitted_config_epoch(),
        operation_id="register-1",
        actor=ACTOR,
        occurred_at=_OCCURRED_AT,
    )
