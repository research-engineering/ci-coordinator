from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

WORKBENCH_PATH = "/api/v1/workbench/repositories/{installation_id}/{repository_id}"
STATUS_PATH = "/api/v1/config/repositories/{installation_id}/{repository_id}/status"
VALIDATION_PATH = "/api/v1/config/validations"
OPERATIONS = (("GET", WORKBENCH_PATH), ("GET", STATUS_PATH), ("POST", VALIDATION_PATH))
OPERATION_COUNT = len(OPERATIONS)
EXAMPLES_BY_PROFILE = {"fast": 10, "deep": 60}
DEFAULT_SEED = 20260919
MAX_SEED = 2**32 - 1
REQUEST_TIMEOUT_SECONDS = 5
TOKEN = "synthetic-api-contract-machine-credential-20260919"
HEADERS = MappingProxyType({"Authorization": f"Bearer {TOKEN}"})


@dataclass(frozen=True)
class Campaign:
    name: str
    seed: int
    max_examples: int


def campaign_from_environment(environment: Mapping[str, str] | None = None) -> Campaign:
    env = os.environ if environment is None else environment
    name = env.get("CI_COORDINATOR_API_CAMPAIGN", "fast")
    if name not in EXAMPLES_BY_PROFILE:
        raise ValueError("CI_COORDINATOR_API_CAMPAIGN must be fast or deep")
    raw_seed = env.get("CI_COORDINATOR_API_SEED", str(DEFAULT_SEED))
    if (
        not raw_seed
        or len(raw_seed) > 10
        or not raw_seed.isascii()
        or not raw_seed.isdecimal()
        or int(raw_seed) > MAX_SEED
    ):
        raise ValueError("CI_COORDINATOR_API_SEED must be a decimal integer in [0, 4294967295]")
    return Campaign(name, int(raw_seed), EXAMPLES_BY_PROFILE[name])
