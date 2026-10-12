import { Activity } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  type CapabilityPassportResult,
  type CapabilityPassportScope,
  fetchCapabilityPassport,
} from "../../api/workbench/capabilityPassportClient";
import type { CapabilityPassport } from "../../api/workbench/capabilityPassportSchema";
import { DataTable } from "../../components/DataTable";
import { LoadingState } from "../../components/LoadingState";
import { StatePanel } from "../../components/StatePanel";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDateTime } from "../../domain/format";

type StatusTone = "positive" | "warning" | "negative" | "neutral" | "info";

export interface CapabilityRow {
  readonly key: string;
  readonly fact: string;
  readonly status: string;
  readonly tone: StatusTone;
  readonly source: string;
  readonly observedAt: string | null;
  readonly detail: string;
}

const NEXT_ACTION_LABELS: Record<CapabilityPassport["nextAction"], string> = {
  request_access: "Request access",
  repair_webhook: "Repair webhook",
  refresh_evidence: "Refresh evidence",
  complete_configuration: "Complete configuration",
  none: "No action derived",
};

const STATUS_TONES: Record<string, StatusTone> = {
  known: "positive",
  unknown: "neutral",
  denied: "negative",
  unavailable: "warning",
  expired: "warning",
};

type PanelState =
  | { readonly kind: "idle"; readonly owner: string }
  | { readonly kind: "loading"; readonly owner: string }
  | { readonly kind: "settled"; readonly owner: string; readonly result: CapabilityPassportResult };

export function CapabilityPassportPanel({
  authorityRevision,
  scope,
}: {
  readonly authorityRevision: number;
  readonly scope: CapabilityPassportScope;
}) {
  const owner = `${scope.installationId}:${scope.repositoryId}:${authorityRevision}`;
  const [state, setState] = useState<PanelState>({ kind: "idle", owner });
  const generation = useRef(0);
  const controller = useRef<AbortController | undefined>(undefined);
  const requestOwner = useRef(owner);

  useEffect(() => {
    if (requestOwner.current === owner) return;
    requestOwner.current = owner;
    generation.current += 1;
    controller.current?.abort();
  }, [owner]);
  useEffect(
    () => () => {
      generation.current += 1;
      controller.current?.abort();
    },
    [],
  );

  const check = useCallback(() => {
    generation.current += 1;
    const current = generation.current;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    setState({ kind: "loading", owner });
    void fetchCapabilityPassport(
      { installationId: scope.installationId, repositoryId: scope.repositoryId },
      abort.signal,
    ).then(
      (result) => {
        if (generation.current === current) setState({ kind: "settled", owner, result });
      },
      () => {
        if (generation.current === current) {
          setState({ kind: "settled", owner, result: { kind: "network-failure" } });
        }
      },
    );
  }, [owner, scope.installationId, scope.repositoryId]);

  const current: PanelState = state.owner === owner ? state : { kind: "idle", owner };
  const result = current.kind === "settled" ? current.result : undefined;

  return (
    <section className="capability-passport" aria-labelledby="capability-passport-heading">
      <header className="capability-passport-header">
        <div>
          <p className="eyebrow">Read-only diagnostics</p>
          <h2 id="capability-passport-heading">Capability diagnostics</h2>
        </div>
        <button
          type="button"
          className="button button--secondary"
          disabled={current.kind === "loading"}
          onClick={check}
        >
          <Activity className="button-icon" aria-hidden="true" />
          {current.kind === "idle" ? "Check capabilities" : "Check again"}
        </button>
      </header>
      {current.kind === "loading" ? (
        <LoadingState compact label="Reading capability evidence" />
      ) : result?.kind === "ready" ? (
        <CapabilityPassportEvidence passport={result.passport} />
      ) : result ? (
        <StatePanel kind={result.kind} onRetry={check} />
      ) : (
        <p className="empty-row">
          Diagnostics read current App grants and stored observation evidence on request.
        </p>
      )}
    </section>
  );
}

function CapabilityPassportEvidence({ passport }: { readonly passport: CapabilityPassport }) {
  return (
    <>
      <p role="status">
        Next action:{" "}
        <StatusBadge tone={passport.nextAction === "none" ? "neutral" : "info"}>
          {NEXT_ACTION_LABELS[passport.nextAction]}
        </StatusBadge>{" "}
        <span>Observed {formatDateTime(passport.observedAt)}</span>
      </p>
      <DataTable
        columns={[
          { header: "Fact", render: (row: CapabilityRow) => row.fact },
          {
            header: "Status",
            render: (row: CapabilityRow) => <StatusBadge tone={row.tone}>{row.status}</StatusBadge>,
          },
          { header: "Source", presentation: "prose", render: (row: CapabilityRow) => row.source },
          {
            header: "Observed",
            presentation: "time",
            render: (row: CapabilityRow) =>
              row.observedAt === null ? "Not observed" : formatDateTime(row.observedAt),
          },
          { header: "Detail", presentation: "prose", render: (row: CapabilityRow) => row.detail },
        ]}
        emptyLabel="No capability facts"
        keyOf={(row) => row.key}
        rows={capabilityRows(passport)}
      />
      <p className="empty-row">
        Diagnostics are not readiness, planning or enforcement authority. Webhook silence stays
        unknown, and denied access is not an empty inventory.
      </p>
    </>
  );
}

export function capabilityRows(passport: CapabilityPassport): readonly CapabilityRow[] {
  const grant = passport.installationGrant;
  const installation = grant.installation;
  const limits = passport.providerLimits;
  const reconciliation = passport.reconciliation;
  const keys = passport.actionsOidcKeys;
  return [
    row(
      "grant",
      "App installation grant",
      grant.status,
      "GitHub App installation",
      grant.observedAt,
      installation === null
        ? `Not projected: ${grant.reason ?? "unknown"}`
        : grantDetail(installation),
    ),
    row(
      "limits",
      "Provider limits",
      limits.status,
      "GitHub App installation",
      limits.observedAt,
      limits.status === "known"
        ? `Rate limited${limits.retryAfterSeconds === null ? "" : `; retry after ${limits.retryAfterSeconds} s`}`
        : "No retained rate-limit observation; absence of a limit is not capacity",
    ),
    row(
      "webhook",
      "Webhook observation",
      passport.webhookObservation.status,
      "History delivery inbox (history-enabled repositories only)",
      null,
      "Receipt time is not indexed; silence is unknown, not a broken webhook",
    ),
    row(
      "reconciliation",
      "Reconciliation freshness",
      reconciliation.status,
      "CI observation status",
      reconciliation.observedAt,
      reconciliationDetail(reconciliation),
    ),
    row(
      "oidc-keys",
      "Actions OIDC keys",
      keys.status,
      "GitHub Actions JWKS cache",
      keys.observedAt,
      keys.status === "unknown"
        ? `Not current: ${keys.reason ?? "unknown"}`
        : `${keys.keyCount} keys, ${keys.ageSeconds ?? 0} s old${keys.status === "expired" ? "; expired keys authenticate nothing" : ""}`,
    ),
    row(
      "admission-keys",
      "Production admission keys",
      passport.productionAdmissionKeys.status,
      "Production admission authorities",
      null,
      "Not projected by this passport",
    ),
  ];
}

function row(
  key: string,
  fact: string,
  status: string,
  source: string,
  observedAt: string | null,
  detail: string,
): CapabilityRow {
  return { key, fact, status, tone: STATUS_TONES[status] ?? "neutral", source, observedAt, detail };
}

function grantDetail(
  installation: NonNullable<CapabilityPassport["installationGrant"]["installation"]>,
): string {
  const permissions =
    installation.permissions === null
      ? "permissions unknown"
      : installation.permissions.length === 0
        ? "no permissions reported"
        : installation.permissions.map((item) => `${item.name}: ${item.level}`).join(", ");
  const events =
    installation.events === null
      ? "events unknown"
      : installation.events.length === 0
        ? "no events subscribed"
        : `events: ${installation.events.join(", ")}`;
  return `${installation.accountLogin} (${installation.state}, ${installation.repositorySelection} repositories); ${permissions}; ${events}`;
}

function reconciliationDetail(reconciliation: CapabilityPassport["reconciliation"]): string {
  const background = `background ${reconciliation.backgroundState.replaceAll("_", " ")}`;
  if (reconciliation.status !== "known") {
    return `${(reconciliation.reason ?? "unknown").replaceAll("_", " ")}; ${background}`;
  }
  const lanes = reconciliation.lanes
    .map(
      (lane) =>
        `${lane.lane} complete through ${
          lane.lastCompletedThrough === null ? "never" : formatDateTime(lane.lastCompletedThrough)
        }`,
    )
    .join("; ");
  const gaps = reconciliation.gapsRecorded ? "gaps recorded" : "no recorded gaps";
  const paused = reconciliation.enabled ? "" : "paused; ";
  return `${paused}${lanes || "no scans"}; ${gaps}; ${background}`;
}
