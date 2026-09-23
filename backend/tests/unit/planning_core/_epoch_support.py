from __future__ import annotations

import json

from ci_coordinator.config_control import ValidatedEpochDraft, admit_policy_document


def admitted_dynamic_epoch(
    *,
    default_depth: str = "standard",
    full_depth: str = "full",
    omit_allowed: bool = True,
    fallback_timeout_seconds: int = 60,
    pretty: bool = False,
) -> ValidatedEpochDraft:
    result = admit_policy_document(
        dynamic_policy_source(
            default_depth=default_depth,
            full_depth=full_depth,
            omit_allowed=omit_allowed,
            fallback_timeout_seconds=fallback_timeout_seconds,
            pretty=pretty,
        ),
        "json",
    )
    assert isinstance(result, ValidatedEpochDraft)
    return result


def dynamic_policy_source(
    *,
    default_depth: str = "standard",
    full_depth: str = "full",
    omit_allowed: bool = True,
    fallback_timeout_seconds: int = 60,
    pretty: bool = False,
) -> bytes:
    document = {
        "schemaVersion": "ci-repository-policy/v1",
        "repository": {
            "installationId": 100,
            "repositoryId": 200,
            "owner": "example-org",
            "name": "ci-coordinator",
            "defaultBranch": "main",
            "rules": [
                {
                    "name": "main",
                    "on": {"event": "push", "branches": ["main"]},
                    "mode": "observe",
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
                }
            ],
            "dynamicCi": {
                "planningEnabled": True,
                "policyVersion": "p1",
                "riskClasses": ["risk"],
                "agentAdvice": {
                    "enabled": False,
                    "modelIdAllowlist": [],
                    "promptHashAllowlist": [],
                    "minConfidence": 0.7,
                    "promptInjectionEvalRequired": False,
                },
                "dependencyGraph": {
                    "source": "configured",
                    "globalRiskPaths": ["**"],
                },
                "fallbackTimeoutSeconds": fallback_timeout_seconds,
                "obligations": [
                    {
                        "obligationId": "repository-quality",
                        "responsibility": {
                            "paths": ["src/**"],
                            "riskClasses": ["risk"],
                        },
                        "requiredWitnessIds": ["python-quality"],
                        "defaultDepth": default_depth,
                        "fullDepth": full_depth,
                        "omitAllowed": omit_allowed,
                    }
                ],
                "witnesses": [
                    {
                        "witnessId": "python-quality",
                        "executionProfileId": "python-linux",
                        "supportedDepths": [
                            "smoke",
                            "targeted",
                            "standard",
                            "full",
                            "exhaustive",
                        ],
                    }
                ],
                "executionProfiles": [
                    {
                        "profileId": "python-linux",
                        "runnerProfileId": "ubuntu-24-04",
                        "permissionProfileId": "contents-read",
                        "credentialProfileId": "no-credentials",
                        "fixtureProfileId": "no-fixtures",
                        "serviceProfileIds": [],
                        "capacityClassId": "hosted-standard",
                        "shardingPolicy": {
                            "maxShards": 8,
                            "maxParallel": 8,
                            "maxItemsPerShard": 1000,
                            "setupSecondsPerShard": 15,
                            "cpuWeight": 1,
                            "wallWeight": 1,
                            "operatorWeight": 1,
                        },
                    }
                ],
            },
        },
    }
    return json.dumps(
        document,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ).encode("utf-8")
