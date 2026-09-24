from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME, history_dataset, history_scan

from ci_coordinator.ci_economics.history_commands import ConfigureHistory
from ci_coordinator.persistence.ci_history_configuration_store import _validate_receipt


def test_expansion_replay_requires_the_current_lower_bound_to_cover_its_receipt() -> None:
    snapshot = replace(history_dataset(), configuration_revision=2)
    command = ConfigureHistory.model_validate(
        {
            "installationId": snapshot.scope.installation_id,
            "repositoryId": snapshot.scope.repository_id,
            "expectedRevision": 1,
            "configuration": snapshot.configuration,
            "initialCreatedFrom": None,
            "expandCreatedFrom": (ARCHIVE_TIME - timedelta(days=400)).isoformat(),
            "rescan": False,
            "operationId": "expand-replay-bound",
            "actor": "operator-1",
        }
    )

    _validate_receipt(command, snapshot, snapshot, history_scan(snapshot, days=500))
    with pytest.raises(ValueError, match="current population bound"):
        _validate_receipt(command, snapshot, snapshot, history_scan(snapshot, days=365))
