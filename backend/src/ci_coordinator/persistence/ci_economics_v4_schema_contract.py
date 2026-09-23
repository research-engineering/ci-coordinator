from dataclasses import replace
from typing import Final

from ci_coordinator.persistence.ci_economics_v3_schema_contract import V3_CATALOG

_TOTAL_CHECKS: Final = frozenset(
    ("ci_workflow_attempt_collections", f"ck_ci_workflow_attempt_collections_{name}")
    for name in ("lease", "state_shape")
)

V4_CATALOG: Final = replace(
    V3_CATALOG,
    constraint_definitions={
        key: f"CHECK (({definition.removeprefix('CHECK (').removesuffix(')')}) IS TRUE)"
        if key in _TOTAL_CHECKS
        else definition
        for key, definition in V3_CATALOG.constraint_definitions.items()
    },
)
