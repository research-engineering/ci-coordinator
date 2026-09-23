from __future__ import annotations

from ci_coordinator.config_control import (
    PolicySourceFormat,
    ValidatedEpochDraft,
    admit_policy_document,
)

CONFIG_SOURCE = (
    b'{"schemaVersion":"ci-repository-policy/v1","repository":{'
    b'"installationId":1,"repositoryId":2,"owner":"example-org",'
    b'"name":"ci-coordinator","defaultBranch":"main","dynamicCi":null,"rules":['
    b'{"name":"main","on":{"event":"push","branches":["main"]},'
    b'"mode":"observe","timing":{"expectedSignalTimeoutSeconds":3600,'
    b'"absenceVerificationWindowSeconds":300,"absencePollLookbackSeconds":3600,'
    b'"lateFindingWindowSeconds":86400,"mutableDecisionWindowSeconds":300},'
    b'"expectedSignals":[{"kind":"workflow","name":"CI","workflowFile":"ci.yml",'
    b'"source":"native","requiredConclusion":"success","required":true}],'
    b'"omittedSignals":[]}]}}'
)


def admitted_config_epoch(
    source_format: PolicySourceFormat = "json",
    source: bytes = CONFIG_SOURCE,
) -> ValidatedEpochDraft:
    admitted = admit_policy_document(source, source_format)
    assert type(admitted) is ValidatedEpochDraft
    return admitted
