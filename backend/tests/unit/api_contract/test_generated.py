from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from schemathesis import Case
from schemathesis.core.failures import FailureGroup

from .case_record import encode_case
from .diagnostics import retain_case
from .harness import Harness
from .oracles import validate_response
from .profile import HEADERS, REQUEST_TIMEOUT_SECONDS

HARNESS = Harness()
SCHEMA = HARNESS.schema


@SCHEMA.parametrize()
def test_api_contract(case: Case[Any]) -> None:
    try:
        with HARNESS.example() as client:
            response = case.call(
                session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS
            )
            validate_response(case, response, HARNESS.ports, trusted_machine=True)
    except (Exception, FailureGroup) as error:
        # Coverage/examples are explicit cases, not necessarily Hypothesis-minimized failures.
        record = encode_case(case, HARNESS)
        directory = Path(
            os.environ.get(
                "CI_COORDINATOR_API_ARTIFACTS",
                str(Path(__file__).resolve().parents[4] / ".api-contract" / "failures"),
            )
        )
        error.add_note(retain_case(record, directory))
        raise
