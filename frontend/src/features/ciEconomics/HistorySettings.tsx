import type { DetailRetention, HistoryConfiguration } from "../../api/ciEconomics/historySchema";

const QUOTAS = [
  ["attempts", "Retained attempts"],
  ["jobs", "Retained jobs"],
  ["gaps", "Coverage gaps"],
  ["canonicalBytes", "Canonical data (bytes)"],
] as const;

export function retentionLabel(policy: DetailRetention): string {
  return policy.mode === "days"
    ? `${policy.days} days after first import`
    : policy.mode === "forever"
      ? "No automatic expiry"
      : "No new detailed records";
}

export function HistorySettings({
  configuration,
  defaultPolicy,
  onChange,
}: {
  readonly configuration: HistoryConfiguration;
  readonly defaultPolicy: DetailRetention;
  readonly onChange: (value: HistoryConfiguration) => void;
}) {
  const policy = configuration.detailRetention;
  return (
    <details className="history-advanced">
      <summary>Retention and capacity limits</summary>
      <div className="observation-fields">
        <label>
          Detailed records
          <select
            value={policy?.mode ?? "inherit"}
            onChange={(event) => {
              const mode = event.target.value;
              onChange({
                ...configuration,
                detailRetention:
                  mode === "inherit"
                    ? null
                    : mode === "days"
                      ? { mode, days: 365, anchor: "first_successful_detail_import" }
                      : mode === "disabled" || mode === "forever"
                        ? { mode }
                        : policy,
              });
            }}
          >
            <option value="inherit">Service default: {retentionLabel(defaultPolicy)}</option>
            <option value="days">Expire after a period</option>
            <option value="forever">Keep until explicitly erased</option>
            <option value="disabled">Statistics only for new imports</option>
          </select>
        </label>
        {policy?.mode === "days" ? (
          <label>
            Detail retention (days)
            <input
              type="number"
              min={1}
              max={36_500}
              step={1}
              value={policy.days}
              onChange={(event) =>
                onChange({
                  ...configuration,
                  detailRetention: { ...policy, days: Number(event.target.value) },
                })
              }
            />
          </label>
        ) : null}
      </div>
      <p className="economics-prompt">
        Statistics have no automatic expiry. Retention changes apply to new detail imports; existing
        records keep their applied policy.
      </p>
      <div className="observation-fields">
        {QUOTAS.map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              type="number"
              min={1}
              max={Number.MAX_SAFE_INTEGER}
              step={1}
              value={configuration.quota[key]}
              onChange={(event) =>
                onChange({
                  ...configuration,
                  quota: { ...configuration.quota, [key]: Number(event.target.value) },
                })
              }
            />
          </label>
        ))}
      </div>
    </details>
  );
}
