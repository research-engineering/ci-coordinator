from ci_coordinator.ci_economics.analytics_configuration import (
    ConfigurePurposeSettings,
    PurposeSettingsQuery,
)
from ci_coordinator.ci_economics.archive_analytics_models import PurposeEntry


def purpose_query(**changes: object) -> PurposeSettingsQuery:
    return PurposeSettingsQuery.model_validate(
        {"installation_id": 101, "repository_id": 202, "generation": 1, **changes}
    )


def purpose_command(**changes: object) -> ConfigurePurposeSettings:
    return ConfigurePurposeSettings.model_validate(
        {
            **purpose_query().model_dump(by_alias=False),
            "expected_revision": 0,
            "operation_id": "purpose-operation",
            "actor": "operator-1",
            "entries": (PurposeEntry(workflow_id=404, job_name="Lint", purposes=("lint",)),),
            **changes,
        }
    )
