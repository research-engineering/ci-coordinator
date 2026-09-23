import { Check, Plus, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  discoverEconomicsSources,
  registerEconomicsSource,
} from "../../api/ciEconomics/sourceClient";
import {
  type ProviderSource,
  type SourceDiscovery,
  type SourceDiscoveryInput,
  type SourceRegistration,
  sourceDiscoveryInputSchema,
} from "../../api/ciEconomics/sourceSchema";
import type { EconomicsResult } from "../../api/ciEconomics/transport";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { formatDateTime, formatInteger } from "../../domain/format";
import { EconomicsError } from "./EconomicsControls";

export function SourceDiscoveryPanel({
  scope,
  session,
}: {
  readonly scope: WorkbenchScope;
  readonly session: ControlPlaneSession | undefined;
}) {
  const [window, setWindow] = useState(() => {
    const through = new Date();
    through.setMilliseconds(0);
    return {
      from: new Date(through.getTime() - 86_400_000).toISOString().slice(0, 19),
      through: through.toISOString().slice(0, 19),
    };
  });
  const [request, setRequest] = useState<SourceDiscoveryInput>();
  const [result, setResult] = useState<EconomicsResult<SourceDiscovery>>();
  const [busy, setBusy] = useState(false);
  const [command, setCommand] = useState<{
    source: ProviderSource;
    result?: EconomicsResult<SourceRegistration>;
  }>();
  const discovery = useRef<AbortController | undefined>(undefined);
  const registration = useRef<AbortController | undefined>(undefined);
  useEffect(
    () => () => {
      discovery.current?.abort();
      registration.current?.abort();
    },
    [],
  );
  const canAudit = session?.roles.includes("audit") === true;
  const canConfigure = session?.roles.includes("configure") === true;
  const input = {
    installationId: scope.installationId,
    repositoryId: scope.repositoryId,
    createdFrom: utcInput(window.from),
    createdThrough: utcInput(window.through),
    pageNumber: 1,
  };
  const validInput = sourceDiscoveryInputSchema.safeParse(input).success;
  function search(next: SourceDiscoveryInput) {
    if (!session || !canAudit || !sourceDiscoveryInputSchema.safeParse(next).success) return;
    discovery.current?.abort();
    registration.current?.abort();
    const controller = new AbortController();
    discovery.current = controller;
    setBusy(true);
    setRequest(next);
    setResult(undefined);
    setCommand(undefined);
    void discoverEconomicsSources(next, session.csrfToken, controller.signal).then(
      (response) => {
        if (controller.signal.aborted || discovery.current !== controller) return;
        setResult(response);
        setBusy(false);
      },
      () => {
        if (!controller.signal.aborted) {
          setResult({ kind: "network-failure" });
          setBusy(false);
        }
      },
    );
  }
  function register(source: ProviderSource) {
    if (
      !session ||
      !canConfigure ||
      (registration.current &&
        !registration.current.signal.aborted &&
        command?.result === undefined)
    )
      return;
    registration.current?.abort();
    const controller = new AbortController();
    registration.current = controller;
    setCommand({ source });
    void registerEconomicsSource(source, session.csrfToken, controller.signal).then(
      (response) => {
        if (controller.signal.aborted || registration.current !== controller) return;
        setCommand({ source, result: response });
      },
      () => {
        if (!controller.signal.aborted) setCommand({ source, result: { kind: "network-failure" } });
      },
    );
  }
  return (
    <>
      <header className="economics-subheading">
        <h2>Discover runs</h2>
      </header>
      <form
        className="economics-discovery-form"
        onSubmit={(event) => {
          event.preventDefault();
          search(input);
        }}
      >
        <label>
          Created from (UTC)
          <input
            type="datetime-local"
            step="1"
            value={window.from}
            onChange={(event) => setWindow((current) => ({ ...current, from: event.target.value }))}
          />
        </label>
        <label>
          Created through (UTC)
          <input
            type="datetime-local"
            step="1"
            value={window.through}
            onChange={(event) =>
              setWindow((current) => ({ ...current, through: event.target.value }))
            }
          />
        </label>
        <button type="submit" className="button" disabled={busy || !validInput || !canAudit}>
          <Search className="button-icon" aria-hidden="true" />
          Search
        </button>
      </form>
      {!validInput ? (
        <p role="alert">A valid UTC window of at most seven days is required.</p>
      ) : null}
      {!canAudit ? <p role="status">Audit access is required.</p> : null}
      {busy ? (
        <p role="status">Discovering provider runs</p>
      ) : result?.kind === "ready" ? (
        <>
          <div className="economics-provenance">
            <span>
              {formatDateTime(result.value.createdFrom)} to{" "}
              {formatDateTime(result.value.createdThrough)}
            </span>
            <span>
              Page {result.value.pageNumber}; provider total{" "}
              {formatInteger(result.value.providerTotal)}
            </span>
          </div>
          <ul className="economics-source-list">
            {result.value.sources.map((source) => (
              <li key={source.sourceId}>
                <div>
                  <strong>Run {source.attempt.workflowRunId}</strong>
                  <span>
                    Attempt {source.attempt.runAttempt} / {source.attempt.headSha.slice(0, 8)}
                  </span>
                  <time dateTime={source.runCreatedAt}>{formatDateTime(source.runCreatedAt)}</time>
                </div>
                <button
                  type="button"
                  className="button button--compact"
                  disabled={
                    !canConfigure || (command !== undefined && command.result === undefined)
                  }
                  onClick={() => register(source)}
                  aria-label={`Register run ${source.attempt.workflowRunId}, attempt ${source.attempt.runAttempt}`}
                >
                  <Plus className="button-icon" aria-hidden="true" />
                  Register
                </button>
              </li>
            ))}
          </ul>
          {result.value.sources.length === 0 ? <p>No runs in this provider page.</p> : null}
          {result.value.termination === "truncated" ? (
            <p role="status">Provider results are incomplete.</p>
          ) : null}
          {result.value.termination === "next_page" && request ? (
            <button
              type="button"
              className="button button--compact"
              onClick={() => search({ ...request, pageNumber: request.pageNumber + 1 })}
            >
              Next provider page
            </button>
          ) : null}
        </>
      ) : result ? (
        <EconomicsError
          failure={result}
          onRetry={() => {
            if (request) search(request);
          }}
        />
      ) : null}
      {command ? (
        <div className="economics-command" aria-live="polite">
          <strong>
            Run {command.source.attempt.workflowRunId}, attempt {command.source.attempt.runAttempt}
          </strong>
          {!command.result ? (
            <span>Registering</span>
          ) : command.result.kind === "ready" ? (
            <span>
              {command.result.value.outcome === "registered" ||
              command.result.value.outcome === "replayed" ? (
                <Check className="button-icon" aria-hidden="true" />
              ) : null}
              {REGISTRATION_LABELS[command.result.value.outcome]}
            </span>
          ) : (
            <EconomicsError
              operation="register"
              failure={command.result}
              onRetry={() => register(command.source)}
            />
          )}
        </div>
      ) : null}
    </>
  );
}

const REGISTRATION_LABELS = {
  registered: "Registered for collection",
  replayed: "Already registered",
  source_conflict: "Source evidence conflicts",
  outside_source_window: "Outside the registration window",
  capacity_reached: "Repository collection capacity reached",
};

function utcInput(value: string): string {
  return `${value.length === 16 ? `${value}:00` : value}Z`;
}
