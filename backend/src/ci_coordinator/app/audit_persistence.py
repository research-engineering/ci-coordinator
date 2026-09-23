from __future__ import annotations

from ci_coordinator.app.persistence import UnitOfWorkFactory
from ci_coordinator.audit_replay import (
    AuditAppendResult,
    AuditEventInput,
    prepare_audit_event,
)


async def append_audit_event(
    event_input: AuditEventInput,
    *,
    unit_of_work_factory: UnitOfWorkFactory,
) -> AuditAppendResult:
    prepared = prepare_audit_event(event_input)
    async with unit_of_work_factory() as unit_of_work:
        result = await unit_of_work.audit_events.append(prepared)
        await unit_of_work.commit()
        return result
