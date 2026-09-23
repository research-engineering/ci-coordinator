import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type CiEconomicsAttemptJobsResult,
  type CiEconomicsAttemptPageResult,
  fetchCiEconomicsAttemptJobs,
  fetchCiEconomicsAttempts,
} from "../../api/ciEconomics/client";
import type {
  CiEconomicsAttemptIdentity,
  CiEconomicsAttemptSummary,
} from "../../api/ciEconomics/schema";
import type { WorkbenchScope } from "../../api/workbench/client";

const MAX_INTERACTIVE_PAGES = 10_000;

type AttemptQueryState =
  | { readonly kind: "loading"; readonly requestKey: string }
  | {
      readonly kind: "settled";
      readonly requestKey: string;
      readonly result: CiEconomicsAttemptPageResult;
    };

type JobQueryState =
  | { readonly kind: "idle" }
  | { readonly kind: "loading"; readonly requestKey: string }
  | {
      readonly kind: "settled";
      readonly requestKey: string;
      readonly result: CiEconomicsAttemptJobsResult;
    };

interface AttemptNavigation {
  readonly cursor: string | null;
  readonly page: number;
  readonly scopeKey: string;
}

interface JobNavigation {
  readonly afterJobId: number | null;
  readonly attemptKey: string;
  readonly page: number;
}

interface BoundSelection {
  readonly scopeKey: string;
  readonly summary: CiEconomicsAttemptSummary;
}

export function useCiEconomics(scope: WorkbenchScope, authorityRevision: number) {
  const scopeKey = `${scope.installationId}:${scope.repositoryId}`;
  const [attemptRevision, setAttemptRevision] = useState(0);
  const [jobRevision, setJobRevision] = useState(0);
  const [attemptNavigation, setAttemptNavigation] = useState<AttemptNavigation>({
    cursor: null,
    page: 1,
    scopeKey,
  });
  const attemptCursor = attemptNavigation.scopeKey === scopeKey ? attemptNavigation.cursor : null;
  const attemptPage = attemptNavigation.scopeKey === scopeKey ? attemptNavigation.page : 1;
  const attemptRequest = useMemo(
    () => ({
      cursor: attemptCursor,
      key: `${scopeKey}:${attemptCursor ?? "latest"}:${attemptRevision}:${authorityRevision}`,
      scope: { installationId: scope.installationId, repositoryId: scope.repositoryId },
    }),
    [
      attemptCursor,
      attemptRevision,
      authorityRevision,
      scope.installationId,
      scope.repositoryId,
      scopeKey,
    ],
  );
  const [attemptState, setAttemptState] = useState<AttemptQueryState>({
    kind: "loading",
    requestKey: attemptRequest.key,
  });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setAttemptState({ kind: "loading", requestKey: attemptRequest.key });
    void fetchCiEconomicsAttempts(
      attemptRequest.scope,
      attemptRequest.cursor,
      controller.signal,
    ).then(
      (result) => {
        if (active) {
          setAttemptState({ kind: "settled", requestKey: attemptRequest.key, result });
        }
      },
      () => {
        if (active) {
          setAttemptState({
            kind: "settled",
            requestKey: attemptRequest.key,
            result: { kind: "network-failure" },
          });
        }
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [attemptRequest]);

  const [selection, setSelection] = useState<BoundSelection>();
  const activeSelection = selection?.scopeKey === scopeKey ? selection.summary : undefined;
  const attemptKey = activeSelection ? attemptEvidenceKey(activeSelection) : "idle";
  const [jobNavigation, setJobNavigation] = useState<JobNavigation>({
    afterJobId: null,
    attemptKey,
    page: 1,
  });
  const activeJobNavigation =
    jobNavigation.attemptKey === attemptKey
      ? jobNavigation
      : { afterJobId: null, attemptKey, page: 1 };
  const jobRequest = useMemo(
    () =>
      activeSelection
        ? {
            summary: activeSelection,
            key: `${attemptKey}:${activeJobNavigation.afterJobId ?? "first"}:${jobRevision}:${authorityRevision}`,
            navigation: activeJobNavigation,
          }
        : undefined,
    [activeJobNavigation, activeSelection, attemptKey, authorityRevision, jobRevision],
  );
  const [jobState, setJobState] = useState<JobQueryState>({ kind: "idle" });

  useEffect(() => {
    if (!jobRequest) {
      setJobState({ kind: "idle" });
      return;
    }
    const controller = new AbortController();
    let active = true;
    setJobState({ kind: "loading", requestKey: jobRequest.key });
    void fetchCiEconomicsAttemptJobs(
      jobRequest.summary,
      jobRequest.navigation.afterJobId,
      controller.signal,
    ).then(
      (result) => {
        if (active) setJobState({ kind: "settled", requestKey: jobRequest.key, result });
      },
      () => {
        if (active) {
          setJobState({
            kind: "settled",
            requestKey: jobRequest.key,
            result: { kind: "network-failure" },
          });
        }
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [jobRequest]);

  const attempts =
    attemptState.requestKey === attemptRequest.key
      ? attemptState
      : { kind: "loading" as const, requestKey: attemptRequest.key };
  const jobs =
    jobRequest === undefined
      ? ({ kind: "idle" } as const)
      : jobState.kind !== "idle" && jobState.requestKey === jobRequest.key
        ? jobState
        : ({ kind: "loading", requestKey: jobRequest.key } as const);

  const selectAttempt = useCallback(
    (summary: CiEconomicsAttemptSummary) => {
      const nextAttemptKey = attemptEvidenceKey(summary);
      setSelection({ scopeKey, summary });
      setJobNavigation({ afterJobId: null, attemptKey: nextAttemptKey, page: 1 });
    },
    [scopeKey],
  );
  const showNextAttempts = useCallback(
    (cursor: string) => {
      setAttemptNavigation((current) => {
        const page = current.scopeKey === scopeKey ? current.page : 1;
        return page >= MAX_INTERACTIVE_PAGES
          ? { cursor: current.scopeKey === scopeKey ? current.cursor : null, page, scopeKey }
          : { cursor, page: page + 1, scopeKey };
      });
    },
    [scopeKey],
  );
  const showLatestAttempts = useCallback(() => {
    setAttemptNavigation({ cursor: null, page: 1, scopeKey });
  }, [scopeKey]);
  const showNextJobs = useCallback(
    (afterJobId: number) => {
      setJobNavigation((current) => {
        const page = current.attemptKey === attemptKey ? current.page : 1;
        return page >= MAX_INTERACTIVE_PAGES
          ? {
              afterJobId: current.attemptKey === attemptKey ? current.afterJobId : null,
              attemptKey,
              page,
            }
          : { afterJobId, attemptKey, page: page + 1 };
      });
    },
    [attemptKey],
  );
  const showFirstJobs = useCallback(() => {
    setJobNavigation({ afterJobId: null, attemptKey, page: 1 });
  }, [attemptKey]);

  return {
    attempts: {
      page: attemptPage,
      refresh: () => setAttemptRevision((value) => value + 1),
      showLatest: showLatestAttempts,
      showNext: showNextAttempts,
      state: attempts,
    },
    jobs: {
      page: activeJobNavigation.page,
      refresh: () => setJobRevision((value) => value + 1),
      showFirst: showFirstJobs,
      showNext: showNextJobs,
      state: jobs,
    },
    maxInteractivePages: MAX_INTERACTIVE_PAGES,
    selectedAttempt: activeSelection,
    selectAttempt,
  } as const;
}

function attemptIdentityKey(attempt: CiEconomicsAttemptIdentity): string {
  return [
    attempt.installationId,
    attempt.repositoryId,
    attempt.workflowRunId,
    attempt.runAttempt,
    attempt.headSha,
  ].join(":");
}

function attemptEvidenceKey(summary: CiEconomicsAttemptSummary): string {
  return [
    attemptIdentityKey(summary.attempt),
    summary.subjectId,
    summary.contractHash,
    summary.plannedRoute,
    summary.snapshotDigest,
    summary.recordedAt,
    summary.jobCount,
  ].join(":");
}
