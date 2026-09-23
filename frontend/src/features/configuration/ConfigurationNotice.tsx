import type { LifecycleFailure } from "../../api/configLifecycle/transport";

export function ConfigurationNotice({ failure }: { readonly failure: LifecycleFailure }) {
  const messages: Record<LifecycleFailure["kind"], string> = {
    unauthenticated: "Administrator session expired.",
    forbidden: "Configuration access denied for this repository.",
    invalid_config: "Source or request rejected.",
    conflict: "Operation identity conflicts with retained evidence. Refresh and review again.",
    revision_conflict: "Active configuration changed. Refresh and review the new revision.",
    target_unavailable: "The selected epoch is no longer available.",
    attestation_invalid: "Repository attestation is missing or expired.",
    coverage_reducing: "Rollback rejected: required validation coverage would be reduced.",
    coverage_unproven: "Rollback rejected: non-reducing coverage is not proved.",
    overloaded: "Configuration service is busy.",
    unavailable: "Configuration service is unavailable.",
    "invalid-response": "Response identity or metadata could not be verified.",
    "invalid-request": "The source or command does not meet the configuration contract.",
    "network-failure": "A verified response was not received.",
  };
  return (
    <div role="alert" className="configuration-notice">
      <p>{messages[failure.kind]}</p>
      {failure.diagnostics?.length ? (
        <section aria-label="Validation diagnostics">
          <pre>
            {failure.diagnostics
              .map(
                (diagnostic) =>
                  `${diagnostic.code} (${diagnostic.phase})\n${diagnostic.instancePointer || "/"} - ${diagnostic.ruleId}`,
              )
              .join("\n\n")}
          </pre>
        </section>
      ) : null}
    </div>
  );
}
