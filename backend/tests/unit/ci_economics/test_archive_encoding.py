import json

import pytest

from ci_coordinator.ci_economics.archive_encoding import encode_archive_header, encode_archive_job
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics
from ci_coordinator.kernel import canonical_json
from ci_economics.archive_factories import archived_statistics


@pytest.mark.parametrize("job_name", ["Lint", "long" * 128, "\U0001f680" * 512, 'quote"\n'])
def test_header_and_job_encoding_preserve_independent_canonical_operands(job_name: str) -> None:
    baseline = archived_statistics().canonical_mapping()
    baseline["jobs"] = [{**archived_statistics().jobs[0].model_dump(mode="json"), "name": job_name}]
    statistics = ArchivedAttemptStatistics.model_validate_json(json.dumps(baseline))
    header = encode_archive_header(statistics)
    job = encode_archive_job(statistics.jobs[0])
    assert json.loads(header) == {key: value for key, value in baseline.items() if key != "jobs"}
    assert job == canonical_json(json.loads(json.dumps(baseline))["jobs"][0])
    assert "jobs" not in json.loads(header)
