from importlib.resources import files
from typing import Final

from ci_coordinator.execution_orchestration import LOCAL_PLAN_REQUEST_WORKFLOW_PATH

_MAX_REQUESTER_BYTES: Final = 131_072


def render_plan_requester() -> bytes:
    content = (
        files("ci_coordinator.target_artifacts.resources")
        .joinpath(LOCAL_PLAN_REQUEST_WORKFLOW_PATH.rsplit("/", 1)[1])
        .read_bytes()
    )
    if (
        not content
        or len(content) > _MAX_REQUESTER_BYTES
        or not content.endswith(b"\n")
        or b"\x00" in content
    ):
        raise ValueError("packaged plan requester is invalid")
    return content
