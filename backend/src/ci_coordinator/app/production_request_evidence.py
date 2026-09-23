"""Fresh provider acquisition joins the active database epoch with receipt authority."""

import asyncio

from ci_coordinator.production_admission import ProductionAdmissionGrant, ProductionCandidateSubject
from ci_coordinator.production_admission.current_evidence import (
    CURRENT_PRODUCTION_OBSERVATION_SECONDS,
    CurrentActivationEvidence,
    admit_current_activation_evidence,
    admit_current_production_evidence,
)
from ci_coordinator.production_admission.ports import (
    CurrentProductionSourcesReader,
    ProductionAuthorityReader,
    ProductionReceiptVerifier,
    RetainedProductionAuthority,
)
from ci_coordinator.production_admission.request_authority import PreparedProductionAuthority


class ProductionRequestAuthorityService:
    def __init__(
        self,
        *,
        authorities: ProductionAuthorityReader,
        verifier: ProductionReceiptVerifier,
        sources: CurrentProductionSourcesReader,
    ) -> None:
        self._authorities = authorities
        self._verifier = verifier
        self._sources = sources

    async def prepare(
        self, candidate: ProductionCandidateSubject
    ) -> PreparedProductionAuthority | None:
        async with asyncio.timeout(CURRENT_PRODUCTION_OBSERVATION_SECONDS):
            retained = await self._authorities.load_authority(candidate.scope, purpose="active")
            if retained is None:
                return None
            grant = self._verifier(retained.envelope_canonical_json)
            if (
                type(grant) is not ProductionAdmissionGrant
                or grant.authority_id != retained.state.active_authority_id
                or not grant.preauthorizes(candidate)
            ):
                return None
            scope_grant = grant.scope_grant(candidate.scope)
            if scope_grant is None:
                return None
            revisions = tuple(
                sorted(
                    {
                        sha
                        for ref, sha in (
                            (candidate.workflow_ref, candidate.workflow_sha),
                            (candidate.job_workflow_ref, candidate.job_workflow_sha),
                        )
                        if ref is not None and sha is not None
                    }
                )
            )
            if not revisions:
                return None
            sources = await self._sources.read(retained.lookup, revisions=revisions)
            if sources is None:
                return None
            current = admit_current_production_evidence(
                candidate=candidate,
                scope_grant=scope_grant,
                scope_revision=retained.state.revision,
                database_started_at=retained.database_observed_at,
                workflow_sources=sources.workflows,
                provider_sources=sources.provider,
            )
            if not retained.state.admits_current(
                current,
                authority_id=grant.authority_id,
                database_now=retained.database_observed_at,
            ):
                return None
            return PreparedProductionAuthority(grant, current)

    async def observe_activation(
        self,
        retained: RetainedProductionAuthority,
        grant: ProductionAdmissionGrant,
    ) -> CurrentActivationEvidence | None:
        scope_grant = grant.scope_grant(retained.state.scope)
        if scope_grant is None or grant.authority_id != retained.state.staged_authority_id:
            return None
        async with asyncio.timeout(CURRENT_PRODUCTION_OBSERVATION_SECONDS):
            sources = await self._sources.read(
                retained.lookup, revisions=(retained.lookup.source_commit,)
            )
            if sources is None or len(sources.workflows) != 1:
                return None
            return admit_current_activation_evidence(
                scope_grant=scope_grant,
                scope_revision=retained.state.revision,
                database_started_at=retained.database_observed_at,
                lookup=retained.lookup,
                workflow_source=sources.workflows[0],
                provider_sources=sources.provider,
            )
