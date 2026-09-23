from typing import Final

from ci_coordinator.kernel import JsonResourceLimits

AUDIT_JSON_MAX_DEPTH: Final = 64
AUDIT_JSON_MAX_NODES: Final = 10_000
AUDIT_JSON_RESOURCE_LIMITS_V1: Final = JsonResourceLimits(
    max_depth=AUDIT_JSON_MAX_DEPTH,
    max_nodes=AUDIT_JSON_MAX_NODES,
)
