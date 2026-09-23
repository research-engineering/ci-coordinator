import { Pencil, Plus } from "lucide-react";
import { useCallback, useState } from "react";
import { fetchBudgetPolicies } from "../../api/ciEconomics/budgetClient";
import type { BudgetPolicy } from "../../api/ciEconomics/budgetSchema";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { StatusBadge } from "../../components/StatusBadge";
import { formatInteger } from "../../domain/format";
import { BudgetPolicyEditor } from "./BudgetPolicyEditor";
import { EconomicsRead, EconomicsRefresh } from "./EconomicsControls";
import { COUNTER_LABELS } from "./MeasurementReportDetail";
import { useEconomicsRead } from "./useEconomicsRead";

export function BudgetPoliciesPanel({
  scope,
  session,
}: {
  readonly scope: WorkbenchScope;
  readonly session: ControlPlaneSession | undefined;
}) {
  const read = useCallback((signal: AbortSignal) => fetchBudgetPolicies(scope, signal), [scope]);
  const query = useEconomicsRead(read);
  const [editing, setEditing] = useState<{ readonly policy?: BudgetPolicy }>();
  const [pending, setPending] = useState(false);
  function reload() {
    setEditing(undefined);
    query.refresh();
  }
  return (
    <>
      <div className="economics-subheading">
        <h2>Budget policies</h2>
        <EconomicsRefresh onClick={reload} disabled={pending} />
      </div>
      <EconomicsRead state={query.state} onRetry={reload}>
        {(page) => (
          <>
            <div className="economics-actions">
              <span>
                {page.policies.length} / {page.maximumPolicies} policy identities
              </span>
              {session?.roles.includes("configure") ? (
                <button
                  type="button"
                  className="button button--compact"
                  disabled={editing !== undefined || page.policies.length >= page.maximumPolicies}
                  onClick={() => setEditing({})}
                >
                  <Plus className="button-icon" aria-hidden="true" />
                  New policy
                </button>
              ) : null}
            </div>
            {editing && session ? (
              <BudgetPolicyEditor
                key={
                  editing.policy ? `${editing.policy.policyKey}:${editing.policy.revision}` : "new"
                }
                scope={scope}
                session={session}
                policy={editing.policy}
                onClose={() => setEditing(undefined)}
                onSaved={reload}
                onPendingChange={setPending}
              />
            ) : (
              <>
                {page.policies.length === 0 ? (
                  <p className="economics-message">No budget policies configured.</p>
                ) : null}
                <ul className="economics-source-list">
                  {page.policies.map((policy) => (
                    <li key={policy.policyKey}>
                      <div>
                        <strong>{policy.policyKey}</strong>
                        <span>{policy.configuration.selector.sampleKey}</span>
                        <span>
                          {COUNTER_LABELS[policy.configuration.counter]} /{" "}
                          {formatInteger(policy.configuration.maximumUs)} us / revision{" "}
                          {policy.revision}
                        </span>
                      </div>
                      <StatusBadge tone={policy.configuration.enabled ? "positive" : "info"}>
                        {policy.configuration.enabled ? "Enabled" : "Disabled"}
                      </StatusBadge>
                      {session?.roles.includes("configure") ? (
                        <button
                          type="button"
                          className="icon-button"
                          title={`Edit ${policy.policyKey}`}
                          aria-label={`Edit ${policy.policyKey}`}
                          disabled={policy.revision === Number.MAX_SAFE_INTEGER}
                          onClick={() => setEditing({ policy })}
                        >
                          <Pencil aria-hidden="true" />
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </EconomicsRead>
    </>
  );
}
