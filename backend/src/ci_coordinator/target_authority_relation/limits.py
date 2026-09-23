"""Resource limits for dormant target-authority relation evidence."""

from typing import Final

from ci_coordinator.kernel.canonical_json import JsonResourceLimits

MAX_RELATION_ROWS: Final = 4_096
MAX_RAW_CANDIDATES: Final = 16_384
MAX_RELATION_FINDINGS: Final = 8 * MAX_RAW_CANDIDATES + 10 * MAX_RELATION_ROWS + 64
MAX_ROW_FIELDS: Final = 16
MAX_MEMBER_ID_BYTES: Final = 2_048
MAX_OWNER_ID_BYTES: Final = 256
MAX_SOURCE_LOCATOR_BYTES: Final = 2_048
MAX_FIELD_NAME_BYTES: Final = 64
MAX_FIELD_VALUE_BYTES: Final = 1_048_576
MAX_ROW_BYTES: Final = 2_097_152
MAX_RELATION_DOCUMENT_BYTES: Final = 33_554_432
MAX_REASON_BYTES: Final = 2_048
MAX_PRODUCER_ID_BYTES: Final = 256
MAX_PRODUCER_VERSION_BYTES: Final = 128
RELATION_JSON_LIMITS: Final = JsonResourceLimits(max_depth=16, max_nodes=65_536)
