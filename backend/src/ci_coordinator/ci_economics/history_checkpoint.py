from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from ci_coordinator.ci_economics.discovery import ProviderObservationPage
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_configuration import HistoryConfiguration
from ci_coordinator.ci_economics.history_cursor import HistoryCursor, advance_history_cursor
from ci_coordinator.config_control import RepositoryScope


@dataclass(frozen=True, slots=True)
class HistoryPendingPage:
    observed: ProviderObservationPage
    run_index: int = 0
    next_attempt: int = 1

    def __post_init__(self) -> None:
        if type(self.observed) is not ProviderObservationPage:
            raise TypeError("pending history requires an admitted provider page")
        if type(self.run_index) is not int or not 0 <= self.run_index < len(
            self.observed.page.sources
        ):
            raise ValueError("pending history index is outside its bounded source page")
        _ = self.attempt_cursor

    @property
    def attempt_cursor(self) -> HistoryAttemptCursor:
        source = self.observed.page.sources[self.run_index]
        return HistoryAttemptCursor(
            self.observed.page.scope,
            source.attempt.workflow_run_id,
            source.attempt.run_attempt,
            self.next_attempt,
        )

    @property
    def workflow_id(self) -> int:
        return self.observed.workflow_ids[self.run_index]


@dataclass(frozen=True, slots=True)
class HistoryCheckpoint:
    cursor: HistoryCursor
    pending: HistoryPendingPage | None = None
    complete: bool = False

    def __post_init__(self) -> None:
        if type(self.cursor) is not HistoryCursor or type(self.complete) is not bool:
            raise TypeError("history checkpoint requires exact cursor and completion flag")
        if self.complete and (
            self.pending is not None
            or self.cursor.window.created_through != self.cursor.created_through
        ):
            raise ValueError("completed history must reach its interval end without pending work")
        if self.pending is None:
            return
        if type(self.pending) is not HistoryPendingPage:
            raise TypeError("pending history requires a live cursor and exact pending page")
        progress = advance_history_cursor(self.cursor, self.pending.observed.page)
        if not progress.register_sources:
            raise ValueError("a subdivided page cannot also become pending attempt work")

    def accept_page(
        self, observed: ProviderObservationPage
    ) -> tuple[HistoryCheckpoint, Literal["provider_truncated"] | None]:
        if self.complete or self.pending is not None:
            raise ValueError("history checkpoint is not awaiting a provider page")
        if type(observed) is not ProviderObservationPage:
            raise TypeError("history checkpoint requires an exact observation page")
        progress = advance_history_cursor(self.cursor, observed.page)
        if not progress.register_sources or not observed.page.sources:
            return self._after_page(progress.cursor), progress.gap_reason
        return HistoryCheckpoint(self.cursor, HistoryPendingPage(observed)), progress.gap_reason

    def complete_attempt(
        self, scope: RepositoryScope, workflow_run_id: int, run_attempt: int
    ) -> HistoryCheckpoint:
        if self.pending is None:
            raise ValueError("history checkpoint is not awaiting an attempt result")
        successor = self.pending.attempt_cursor.advance(scope, workflow_run_id, run_attempt)
        if successor is not None:
            return replace(self, pending=replace(self.pending, next_attempt=successor.next_attempt))
        return self._advance_run()

    def skip_unselected_run(self, configuration: HistoryConfiguration) -> HistoryCheckpoint:
        configuration = HistoryConfiguration.model_validate(configuration)
        if self.pending is None or configuration.selects(self.pending.workflow_id):
            raise ValueError("only an explicitly unselected pending workflow may be skipped")
        return self._advance_run()

    def handoff_run(self, scope: RepositoryScope, workflow_run_id: int) -> HistoryCheckpoint:
        if self.pending is None or self.pending.next_attempt != 1:
            raise ValueError("whole-run handoff requires an unconsumed pending run")
        expected = self.pending.attempt_cursor
        if (
            type(scope) is not RepositoryScope
            or type(workflow_run_id) is not int
            or (scope, workflow_run_id) != (expected.scope, expected.workflow_run_id)
        ):
            raise ValueError("history handoff substitutes its pending run")
        return self._advance_run()

    def _advance_run(self) -> HistoryCheckpoint:
        if self.pending is None:
            raise ValueError("history run advancement requires a pending page")
        next_index = self.pending.run_index + 1
        if next_index < len(self.pending.observed.page.sources):
            return replace(
                self, pending=replace(self.pending, run_index=next_index, next_attempt=1)
            )
        return self._after_page(
            advance_history_cursor(self.cursor, self.pending.observed.page).cursor
        )

    def _after_page(self, cursor: HistoryCursor | None) -> HistoryCheckpoint:
        return HistoryCheckpoint(self.cursor if cursor is None else cursor, complete=cursor is None)
