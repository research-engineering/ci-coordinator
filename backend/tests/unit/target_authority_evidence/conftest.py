from __future__ import annotations

from copy import deepcopy

import pytest

from .factories import EvidenceFixture, evidence_fixture


@pytest.fixture(scope="module")
def evidence_seed() -> EvidenceFixture:
    return evidence_fixture()


@pytest.fixture
def fixture(evidence_seed: EvidenceFixture) -> EvidenceFixture:
    return deepcopy(evidence_seed)
