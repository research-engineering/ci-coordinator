from dataclasses import replace
from typing import cast

import pytest

from ci_coordinator.ci_economics.discovery import DiscoveryPageTermination
from ci_coordinator.ci_economics.observation_workflows import (
    ObservationWorkflowChoice,
    ObservationWorkflowPage,
)
from ci_coordinator.config_control import RepositoryScope
from ci_economics.observation_factories import SCOPE

CHOICE = ObservationWorkflowChoice(1, "Full Check", ".github/workflows/full.yml", "active")
PAGE = ObservationWorkflowPage(SCOPE, 1, 1, (CHOICE,), "exhausted")


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", None),
        ("page_number", True),
        ("page_number", 0),
        ("page_number", 21),
        ("provider_total", True),
        ("provider_total", -1),
        ("provider_total", 2**53),
        ("workflows", []),
        ("workflows", (object(),)),
        ("workflows", (CHOICE, CHOICE)),
        ("termination", "complete"),
    ],
)
def test_page_operand_corruption_is_rejected(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        replace(
            PAGE,
            scope=cast(RepositoryScope, value) if field == "scope" else PAGE.scope,
            page_number=cast(int, value) if field == "page_number" else PAGE.page_number,
            provider_total=cast(int, value) if field == "provider_total" else PAGE.provider_total,
            workflows=cast(tuple[ObservationWorkflowChoice, ...], value)
            if field == "workflows"
            else PAGE.workflows,
            termination=cast(DiscoveryPageTermination, value)
            if field == "termination"
            else PAGE.termination,
        )


@pytest.mark.parametrize(
    "number,count,total,termination,valid",
    [
        (1, 0, 0, "exhausted", True),
        (1, 1, 2, "exhausted", False),
        (1, 1, 2, "next_page", False),
        (1, 100, 100, "next_page", False),
        (1, 100, 101, "next_page", True),
        (20, 100, 2001, "next_page", False),
        (20, 100, 2001, "truncated", True),
        (2, 0, 0, "truncated", True),
        (1, 101, 101, "exhausted", False),
        (20, 100, 2000, "exhausted", True),
    ],
)
def test_page_termination_relations(
    number: int, count: int, total: int, termination: str, valid: bool
) -> None:
    workflows = tuple(replace(CHOICE, workflow_id=index + 1) for index in range(count))
    if valid:
        page = ObservationWorkflowPage(
            SCOPE, number, total, workflows, cast(DiscoveryPageTermination, termination)
        )
        assert len(page.workflows) == count and page.termination == termination
    else:
        with pytest.raises(ValueError):
            ObservationWorkflowPage(
                SCOPE, number, total, workflows, cast(DiscoveryPageTermination, termination)
            )


@pytest.mark.parametrize("field,limit", [("name", 256), ("path", 1024), ("state", 64)])
def test_display_text_bounds_have_inclusive_positive_control(field: str, limit: int) -> None:
    def candidate(text: str) -> ObservationWorkflowChoice:
        return replace(
            CHOICE,
            name=text if field == "name" else CHOICE.name,
            path=text if field == "path" else CHOICE.path,
            state=text if field == "state" else CHOICE.state,
        )

    assert getattr(candidate("x" * limit), field) == "x" * limit
    with pytest.raises(ValueError):
        candidate("x" * (limit + 1))
