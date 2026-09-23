from typing import Final

from ci_coordinator.ci_economics.archive_statistics import (
    ArchivedAttemptStatistics,
    ArchivedJobStatistics,
)
from ci_coordinator.kernel.canonical_json import bounded_canonical_json

MAX_HISTORY_HEADER_BYTES: Final = 8192
MAX_HISTORY_JOB_BYTES: Final = 32768
MAX_HISTORY_STATISTICS_BYTES: Final = 8_388_608


def encode_archive_header(statistics: ArchivedAttemptStatistics) -> bytes:
    statistics = ArchivedAttemptStatistics.model_validate(statistics)
    return bounded_canonical_json(
        statistics.model_dump(mode="json", exclude={"jobs"}), max_bytes=MAX_HISTORY_HEADER_BYTES
    )


def encode_archive_job(job: ArchivedJobStatistics) -> bytes:
    job = ArchivedJobStatistics.model_validate(job)
    return bounded_canonical_json(job.model_dump(mode="json"), max_bytes=MAX_HISTORY_JOB_BYTES)
