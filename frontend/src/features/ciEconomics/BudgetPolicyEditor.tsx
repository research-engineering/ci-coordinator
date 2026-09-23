import { Save, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { configureBudgetPolicy } from "../../api/ciEconomics/budgetClient";
import { type BudgetPolicy, budgetCommandSchema } from "../../api/ciEconomics/budgetSchema";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { COUNTER_LABELS } from "./MeasurementReportDetail";

export function BudgetPolicyEditor({
  scope,
  policy,
  session,
  onClose,
  onSaved,
  onPendingChange,
}: {
  readonly scope: WorkbenchScope;
  readonly policy: BudgetPolicy | undefined;
  readonly session: ControlPlaneSession;
  readonly onClose: () => void;
  readonly onSaved: () => void;
  readonly onPendingChange: (pending: boolean) => void;
}) {
  const [key, setKey] = useState(policy?.policyKey ?? "");
  const [enabled, setEnabled] = useState(policy?.configuration.enabled ?? true);
  const [sample, setSample] = useState(policy?.configuration.selector.sampleKey ?? "");
  const [producer, setProducer] = useState(policy?.configuration.selector.producerDigest ?? "");
  const [runner, setRunner] = useState(policy?.configuration.selector.runnerClassDigest ?? "");
  const [counter, setCounter] = useState(policy?.configuration.counter ?? "elapsed");
  const [maximum, setMaximum] = useState(String(policy?.configuration.maximumUs ?? 300_000_000));
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<string>();
  const controller = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => controller.current?.abort(), []);
  const [operation] = useState(() => crypto.randomUUID());
  const candidate = budgetCommandSchema.safeParse({
    installationId: scope.installationId,
    repositoryId: scope.repositoryId,
    policyKey: key,
    expectedRevision: policy?.revision ?? 0,
    operationId: operation,
    configuration: {
      enabled,
      selector: {
        sampleKey: sample,
        producerDigest: producer,
        method: "waited_children/v1",
        runnerClassDigest: runner || null,
      },
      counter,
      maximumUs: /^(0|[1-9][0-9]*)$/.test(maximum) ? Number(maximum) : NaN,
    },
  });
  async function submit() {
    if (!candidate.success || controller.current || notice) return;
    const active = new AbortController();
    controller.current = active;
    setPending(true);
    onPendingChange(true);
    try {
      const result = await configureBudgetPolicy(candidate.data, session.csrfToken, active.signal);
      if (active.signal.aborted) return;
      if (result.kind === "ready" && result.value.policy !== null) {
        onSaved();
      } else {
        setNotice(
          result.kind === "ready"
            ? {
                committed: "The write outcome could not be validated. Reload policies.",
                replayed: "The write outcome could not be validated. Reload policies.",
                revision_conflict: "The policy changed. Reload before editing again.",
                operation_conflict:
                  "The operation identity is already used. Reload before editing again.",
                capacity_reached:
                  "All 16 policy identities are allocated. Edit an existing policy.",
              }[result.value.outcome]
            : result.kind === "forbidden" || result.kind === "unauthenticated"
              ? "Configuration access was rejected. Check your session and reload."
              : "The write outcome is unknown. Reload policies before making another change.",
        );
      }
    } catch {
      if (!active.signal.aborted)
        setNotice("The write outcome is unknown. Reload policies before making another change.");
    } finally {
      if (!active.signal.aborted) {
        setPending(false);
        onPendingChange(false);
      }
    }
  }
  return (
    <form
      className="economics-budget-editor"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <div className="economics-subheading">
        <h3>{policy ? `Edit ${policy.policyKey}` : "New budget policy"}</h3>
        <button
          type="button"
          className="icon-button"
          title="Close editor"
          aria-label="Close editor"
          onClick={notice === undefined ? onClose : onSaved}
          disabled={pending}
        >
          <X aria-hidden="true" />
        </button>
      </div>
      <fieldset disabled={pending || notice !== undefined}>
        <label>
          Policy key
          <input
            required
            maxLength={128}
            value={key}
            onChange={(event) => setKey(event.target.value)}
            readOnly={policy !== undefined}
          />
        </label>
        <label>
          Sample key
          <input
            required
            maxLength={128}
            value={sample}
            onChange={(event) => setSample(event.target.value)}
          />
        </label>
        <label className="economics-budget-wide">
          Producer SHA-256
          <input
            required
            maxLength={64}
            value={producer}
            onChange={(event) => setProducer(event.target.value)}
            spellCheck={false}
            autoCapitalize="none"
          />
        </label>
        <label className="economics-budget-wide">
          Runner class SHA-256 (blank: any declared class)
          <input
            maxLength={64}
            value={runner}
            onChange={(event) => setRunner(event.target.value)}
            spellCheck={false}
            autoCapitalize="none"
          />
        </label>
        <label>
          Counter
          <select
            value={counter}
            onChange={(event) => setCounter(event.target.value as typeof counter)}
          >
            {Object.entries(COUNTER_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Maximum (microseconds)
          <input
            required
            inputMode="numeric"
            value={maximum}
            onChange={(event) => setMaximum(event.target.value)}
          />
        </label>
        <label className="economics-budget-enabled">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          Enabled
        </label>
      </fieldset>
      <div className="economics-actions">
        <span>Evaluation: newly accepted reports</span>
        <span>Expected revision {policy?.revision ?? 0}</span>
        <button
          type="submit"
          className="button button--compact"
          disabled={!candidate.success || pending || notice !== undefined}
        >
          <Save className="button-icon" aria-hidden="true" />
          {pending ? "Saving" : "Save policy"}
        </button>
      </div>
      {notice ? (
        <div className="economics-message" role="alert">
          <span>{notice}</span>
          <button type="button" className="button button--secondary" onClick={onSaved}>
            Reload policies
          </button>
        </div>
      ) : null}
    </form>
  );
}
