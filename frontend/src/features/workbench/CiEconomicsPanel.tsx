import {
  AlertTriangle,
  Ban,
  BarChart3,
  ChevronLeft,
  ChevronRight,
  KeyRound,
  RefreshCw,
  ServerOff,
} from "lucide-react";
import type {
  CiEconomicsAttemptJobsResult,
  CiEconomicsAttemptPageResult,
} from "../../api/ciEconomics/client";
import type { CiEconomicsAttemptSummary } from "../../api/ciEconomics/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { IdentityDisclosure } from "../../components/IdentityDisclosure";
import { LoadingState } from "../../components/LoadingState";
import { formatInteger } from "../../domain/format";
import { CiEconomicsAttemptEvidence } from "./CiEconomicsAttemptEvidence";
import { CiEconomicsAttemptList } from "./CiEconomicsAttemptList";
import { useCiEconomics } from "./useCiEconomics";

export function CiEconomicsPanel({
  authorityRevision,
  scope,
}: {
  readonly authorityRevision: number;
  readonly scope: WorkbenchScope;
}) {
  const query = useCiEconomics(scope, authorityRevision);
  const attemptResult =
    query.attempts.state.kind === "settled" ? query.attempts.state.result : undefined;
  return (
    <section className="ci-economics" aria-labelledby="ci-economics-title">
      <div className="section-heading economics-heading">
        <h2 id="ci-economics-title">
          <BarChart3 className="evidence-icon" aria-hidden="true" />
          CI economics
        </h2>
        <RefreshButton label="Refresh CI economics" onClick={query.attempts.refresh} />
      </div>
      {query.attempts.state.kind === "loading" ? (
        <LoadingState label="Loading CI economics" />
      ) : attemptResult?.kind === "ready" ? (
        <>
          <CiEconomicsAttemptList
            attempts={attemptResult.page.items}
            onSelect={query.selectAttempt}
            selected={query.selectedAttempt}
          />
          <PageControls
            canAdvance={
              attemptResult.page.nextCursor !== null &&
              query.attempts.page < query.maxInteractivePages
            }
            canReset={query.attempts.page > 1}
            label="Attempt page"
            onAdvance={() => {
              if (attemptResult.page.nextCursor !== null) {
                query.attempts.showNext(attemptResult.page.nextCursor);
              }
            }}
            onReset={query.attempts.showLatest}
            page={query.attempts.page}
          />
          <AttemptDetail
            jobs={query.jobs}
            maxInteractivePages={query.maxInteractivePages}
            selected={query.selectedAttempt}
          />
        </>
      ) : attemptResult ? (
        <FailurePanel result={attemptResult} onRetry={query.attempts.refresh} />
      ) : null}
    </section>
  );
}

function AttemptDetail({
  jobs,
  maxInteractivePages,
  selected,
}: {
  readonly jobs: ReturnType<typeof useCiEconomics>["jobs"];
  readonly maxInteractivePages: number;
  readonly selected: CiEconomicsAttemptSummary | undefined;
}) {
  if (!selected) {
    return <p className="economics-prompt">Select an attempt to inspect its retained evidence.</p>;
  }
  const result = jobs.state.kind === "settled" ? jobs.state.result : undefined;
  return (
    <div className="economics-subsection economics-detail">
      <div className="economics-subheading">
        <div>
          <h3>Attempt evidence</h3>
          <IdentityDisclosure value={selected.subjectId} />
        </div>
        <RefreshButton label="Refresh attempt evidence" onClick={jobs.refresh} />
      </div>
      {jobs.state.kind === "loading" ? (
        <LoadingState label="Loading attempt evidence" />
      ) : result?.kind === "ready" ? (
        <>
          <CiEconomicsAttemptEvidence economics={result.economics} />
          <PageControls
            canAdvance={result.economics.nextJobId !== null && jobs.page < maxInteractivePages}
            canReset={jobs.page > 1}
            label="Job page"
            onAdvance={() => {
              if (result.economics.nextJobId !== null) {
                jobs.showNext(result.economics.nextJobId);
              }
            }}
            onReset={jobs.showFirst}
            page={jobs.page}
          />
        </>
      ) : result ? (
        <FailurePanel result={result} onRetry={jobs.refresh} />
      ) : null}
    </div>
  );
}

function PageControls({
  canAdvance,
  canReset,
  label,
  onAdvance,
  onReset,
  page,
}: {
  readonly canAdvance: boolean;
  readonly canReset: boolean;
  readonly label: string;
  readonly onAdvance: () => void;
  readonly onReset: () => void;
  readonly page: number;
}) {
  return (
    <nav className="economics-pagination" aria-label={`${label} navigation`}>
      <button
        className="button button--compact"
        disabled={!canReset}
        onClick={onReset}
        type="button"
      >
        <ChevronLeft className="button-icon" aria-hidden="true" />
        Latest
      </button>
      <span aria-current="page">
        {label} {formatInteger(page)}
      </span>
      <button
        className="button button--compact"
        disabled={!canAdvance}
        onClick={onAdvance}
        type="button"
      >
        Older
        <ChevronRight className="button-icon" aria-hidden="true" />
      </button>
    </nav>
  );
}

function FailurePanel({
  onRetry,
  result,
}: {
  readonly onRetry: () => void;
  readonly result: Exclude<
    CiEconomicsAttemptPageResult | CiEconomicsAttemptJobsResult,
    { readonly kind: "ready" }
  >;
}) {
  const presentation = FAILURE_PRESENTATION[result.kind];
  const Icon = presentation.icon;
  return (
    <div className="economics-message" role="alert">
      <Icon className="state-icon" aria-hidden="true" />
      <strong>{presentation.title}</strong>
      <span>{presentation.description}</span>
      <button className="button button--secondary" onClick={onRetry} type="button">
        <RefreshCw className="button-icon" aria-hidden="true" />
        Retry
      </button>
    </div>
  );
}

function RefreshButton({
  label,
  onClick,
}: {
  readonly label: string;
  readonly onClick: () => void;
}) {
  return (
    <button
      aria-label={label}
      className="icon-button"
      onClick={onClick}
      title={label}
      type="button"
    >
      <RefreshCw aria-hidden="true" />
    </button>
  );
}

const FAILURE_PRESENTATION = {
  forbidden: {
    description: "The authenticated operator cannot read economics for this repository.",
    icon: Ban,
    title: "Economics access denied",
  },
  "invalid-response": {
    description:
      "The CI measurements could not be validated. Retry; if this persists, contact an administrator.",
    icon: AlertTriangle,
    title: "Economics response rejected",
  },
  "network-failure": {
    description: "The economics endpoint could not be reached.",
    icon: ServerOff,
    title: "Economics connection failed",
  },
  "not-found": {
    description: "This retained attempt is no longer available.",
    icon: ServerOff,
    title: "Attempt evidence not found",
  },
  unauthenticated: {
    description: "An authenticated operator session is required.",
    icon: KeyRound,
    title: "Economics authentication required",
  },
  unavailable: {
    description: "Trustworthy economics evidence is temporarily unavailable.",
    icon: ServerOff,
    title: "Economics unavailable",
  },
} as const;
