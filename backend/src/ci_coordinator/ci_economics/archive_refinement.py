from collections.abc import Mapping
from typing import Literal

from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics

type ArchiveUpdate = Literal["unchanged", "refined", "incomparable", "conflict"]


def classify_archive_update(
    prior: ArchivedAttemptStatistics, incoming: ArchivedAttemptStatistics
) -> ArchiveUpdate:
    prior = ArchivedAttemptStatistics.model_validate(prior)
    incoming = ArchivedAttemptStatistics.model_validate(incoming)
    if prior.population == "conflict" or incoming.population == "conflict":
        return "conflict"
    prior_header = prior.model_dump(exclude={"jobs", "population"})
    incoming_header = incoming.model_dump(exclude={"jobs", "population"})
    prior_jobs = {job.provider_job_id: job.model_dump() for job in prior.jobs}
    incoming_jobs = {job.provider_job_id: job.model_dump() for job in incoming.jobs}
    if (
        _conflicts(prior_header, incoming_header)
        or any(
            _conflicts(prior_jobs[key], incoming_jobs[key])
            for key in prior_jobs.keys() & incoming_jobs.keys()
        )
        or (prior.population == "complete" and not incoming_jobs.keys() <= prior_jobs.keys())
        or (incoming.population == "complete" and not prior_jobs.keys() <= incoming_jobs.keys())
    ):
        return "conflict"
    if (
        _preserves(prior_header, incoming_header)
        and _preserves_jobs(prior_jobs, incoming_jobs)
        and (incoming.population != "complete" or prior.population == "complete")
    ):
        return "unchanged"
    if (
        _preserves(incoming_header, prior_header)
        and _preserves_jobs(incoming_jobs, prior_jobs)
        and (prior.population != "complete" or incoming.population == "complete")
    ):
        return "refined"
    return "incomparable"


def _conflicts(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return any(
        value is not None and right[name] is not None and value != right[name]
        for name, value in left.items()
    )


def _preserves(candidate: Mapping[str, object], required: Mapping[str, object]) -> bool:
    return all(value is None or candidate[name] == value for name, value in required.items())


def _preserves_jobs(
    candidate: Mapping[int, dict[str, object]], required: Mapping[int, dict[str, object]]
) -> bool:
    return required.keys() <= candidate.keys() and all(
        _preserves(candidate[key], job) for key, job in required.items()
    )
