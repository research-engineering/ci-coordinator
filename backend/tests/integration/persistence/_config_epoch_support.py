from __future__ import annotations

import json

from ci_coordinator.config_control import (
    PolicySourceFormat,
    ValidatedEpochDraft,
    admit_policy_document,
)


def config_epoch_draft(
    source_format: PolicySourceFormat = "json",
    *,
    installation_id: int = 1,
    repository_id: int = 2,
    name: str = "repo",
) -> ValidatedEpochDraft:
    source = json.dumps(
        {
            "schemaVersion": "ci-repository-policy/v1",
            "repository": {
                "installationId": installation_id,
                "repositoryId": repository_id,
                "owner": "acme",
                "name": name,
                "defaultBranch": "main",
                "dynamicCi": None,
                "rules": [
                    {
                        "name": "main",
                        "on": {"event": "push", "branches": ["main"]},
                        "mode": "observe",
                        "timing": {
                            "expectedSignalTimeoutSeconds": 3600,
                            "absenceVerificationWindowSeconds": 300,
                            "absencePollLookbackSeconds": 3600,
                            "lateFindingWindowSeconds": 86400,
                            "mutableDecisionWindowSeconds": 300,
                        },
                        "expectedSignals": [
                            {
                                "kind": "workflow",
                                "name": "CI",
                                "workflowFile": "ci.yml",
                                "source": "native",
                                "requiredConclusion": "success",
                                "required": True,
                            }
                        ],
                        "omittedSignals": [],
                    }
                ],
            },
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    admitted = admit_policy_document(source, source_format)
    assert type(admitted) is ValidatedEpochDraft
    return admitted
