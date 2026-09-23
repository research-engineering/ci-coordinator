from __future__ import annotations

WORKFLOW_PATH = ".github/workflows/full-check.yml"


def dynamic_policy(commit: str) -> dict[str, object]:
    return {
        "schemaVersion": "dynamic-ci-policy-fragment/v1",
        "generator": {"id": "consumer-test", "version": commit},
        "workflowPath": WORKFLOW_PATH,
        "dynamicCi": {
            "planningEnabled": True,
            "policyVersion": "consumer-test-v1",
            "riskClasses": ["docs", "source"],
            "agentAdvice": {
                "enabled": False,
                "modelIdAllowlist": [],
                "promptHashAllowlist": [],
                "minConfidence": 0.7,
                "promptInjectionEvalRequired": False,
            },
            "dependencyGraph": {
                "source": "configured",
                "globalRiskPaths": [".github/workflows/**"],
            },
            "fallbackTimeoutSeconds": 60,
            "obligations": [
                {
                    "obligationId": "source-quality",
                    "responsibility": {"paths": ["src/**"], "riskClasses": ["source"]},
                    "requiredWitnessIds": ["lint-witness", "test-witness"],
                    "defaultDepth": "standard",
                    "fullDepth": "full",
                    "omitAllowed": True,
                },
                {
                    "obligationId": "docs-quality",
                    "responsibility": {"paths": ["docs/**"], "riskClasses": ["docs"]},
                    "requiredWitnessIds": ["lint-witness"],
                    "defaultDepth": "standard",
                    "fullDepth": "full",
                    "omitAllowed": True,
                },
            ],
            "witnesses": [
                {
                    "witnessId": "lint-witness",
                    "executionProfileId": "native-lint",
                    "supportedDepths": ["standard", "full"],
                },
                {
                    "witnessId": "test-witness",
                    "executionProfileId": "native-test",
                    "supportedDepths": ["standard", "full"],
                },
            ],
            "executionProfiles": [
                _execution_profile("native-lint"),
                _execution_profile("native-test"),
            ],
        },
    }


def _execution_profile(profile_id: str) -> dict[str, object]:
    return {
        "profileId": profile_id,
        "runnerProfileId": "ubuntu-24-04",
        "permissionProfileId": "contents-read",
        "credentialProfileId": "no-credentials",
        "fixtureProfileId": "no-fixtures",
        "serviceProfileIds": [],
        "capacityClassId": "hosted-default",
        "shardingPolicy": {
            "maxShards": 1,
            "maxParallel": 1,
            "maxItemsPerShard": 1000,
            "setupSecondsPerShard": 0,
            "cpuWeight": 1,
            "wallWeight": 1,
            "operatorWeight": 1,
        },
    }
