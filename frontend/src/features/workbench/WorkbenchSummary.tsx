import {
  Clock3,
  Crosshair,
  Database,
  LoaderCircle,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
} from "lucide-react";
import type { WorkbenchSnapshot } from "../../api/workbench/schema";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDateTime } from "../../domain/format";

export function WorkbenchSummary({ snapshot }: { readonly snapshot: WorkbenchSnapshot }) {
  const replay = REPLAY_PRESENTATION[snapshot.replay.status];
  const ReplayIcon = replay.icon;
  const truncated = Object.values(snapshot.truncated).some(Boolean);
  return (
    <section className="summary-band" aria-label="Snapshot summary">
      <div className="summary-item">
        <Database className="summary-icon" aria-hidden="true" />
        <span>Ledger revision</span>
        <strong>{snapshot.ledgerRevision}</strong>
      </div>
      <div className="summary-item">
        <Clock3 className="summary-icon" aria-hidden="true" />
        <span>Observed</span>
        <strong>{formatDateTime(snapshot.observedAt)}</strong>
      </div>
      <div className="summary-item">
        <ReplayIcon className="summary-icon" aria-hidden="true" />
        <span>Replay</span>
        <StatusBadge className="summary-status" tone={replay.tone}>
          {snapshot.replay.status.replace("_", " ")}
        </StatusBadge>
      </div>
      <div className="summary-item">
        <Crosshair className="summary-icon" aria-hidden="true" />
        <span>Installation / repository</span>
        <strong>
          {snapshot.scope.installationId}:{snapshot.scope.repositoryId}
        </strong>
      </div>
      {truncated ? (
        <p className="truncation-warning" role="status">
          One or more sections reached the requested item limit.
        </p>
      ) : null}
    </section>
  );
}

const REPLAY_PRESENTATION = {
  in_progress: { icon: LoaderCircle, tone: "info" },
  invalid: { icon: ShieldX, tone: "negative" },
  unavailable: { icon: ShieldAlert, tone: "warning" },
  valid: { icon: ShieldCheck, tone: "positive" },
} as const satisfies Record<
  WorkbenchSnapshot["replay"]["status"],
  { readonly icon: typeof ShieldCheck; readonly tone: "info" | "negative" | "positive" | "warning" }
>;
