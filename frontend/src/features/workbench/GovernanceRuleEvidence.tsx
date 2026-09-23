import { Braces, ShieldCheck } from "lucide-react";
import type { GovernanceObservation } from "../../api/governanceObservation/schema";

interface GovernanceRuleEvidenceProps {
  readonly rules: GovernanceObservation["rules"];
}

export function GovernanceRuleEvidence({ rules }: GovernanceRuleEvidenceProps) {
  if (rules.length === 0) return null;
  return (
    <div className="governance-rules">
      {rules.map((rule) => (
        <details className="governance-rule" key={rule.canonicalJson}>
          <summary>
            <ShieldCheck aria-hidden="true" />
            <span>
              <strong>{rule.ruleType}</strong>
              <small>
                {rule.rulesetSourceType} ruleset #{rule.rulesetId}
              </small>
            </span>
          </summary>
          <dl className="governance-rule-metadata">
            <Metadata label="Source" value={rule.rulesetSource} />
            <Metadata label="Source type" value={rule.rulesetSourceType} />
            <Metadata label="Ruleset ID" value={String(rule.rulesetId)} />
          </dl>
          <div className="governance-json">
            <div>
              <Braces aria-hidden="true" />
              Canonical provider rule
            </div>
            <pre>
              <code>{rule.canonicalJson}</code>
            </pre>
          </div>
        </details>
      ))}
    </div>
  );
}

function Metadata({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
