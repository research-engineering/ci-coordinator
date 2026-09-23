from importlib.resources import files


def render_measurement_reporter() -> bytes:
    content = (
        files("ci_coordinator.target_artifacts.resources")
        .joinpath("ci_measurement_reporter.py")
        .read_bytes()
    )
    if not content or len(content) > 131_072 or not content.endswith(b"\n") or b"\0" in content:
        raise ValueError("packaged measurement reporter is invalid")
    return content
