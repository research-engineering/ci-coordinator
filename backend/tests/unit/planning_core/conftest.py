from __future__ import annotations

from collections.abc import Callable

import pytest

from ci_coordinator.planning_core import PlanningPolicy
from ci_coordinator.repo_context import DiffFileChangeInput, PlanningInput
from ci_coordinator.validation_contract import ValidationCatalog

from ._support import make_input_value, make_policy_value

type MakePolicy = Callable[..., PlanningPolicy]
type MakeInput = Callable[..., PlanningInput]


@pytest.fixture
def make_policy() -> MakePolicy:
    def build(
        *,
        policy_hash: str = "d" * 64,
        catalog: ValidationCatalog | None = None,
    ) -> PlanningPolicy:
        return make_policy_value(policy_hash=policy_hash, catalog=catalog)

    return build


@pytest.fixture
def make_input() -> MakeInput:
    return make_input_value


__all__ = ["DiffFileChangeInput", "MakeInput", "MakePolicy"]
