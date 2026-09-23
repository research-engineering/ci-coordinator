import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { shortIdentity } from "../domain/format";

export function IdentityDisclosure({ value }: { readonly value: string }) {
  const compact = shortIdentity(value);
  return (
    <div className="identity-value">
      {compact === value ? (
        <code>{value}</code>
      ) : (
        <details className="identity-disclosure">
          <summary aria-label={`Inspect identifier ${compact}`}>
            <code>{compact}</code>
          </summary>
          <code className="identity-disclosure-value">{value}</code>
        </details>
      )}
      <CopyIdentifier key={value} value={value} />
    </div>
  );
}

function CopyIdentifier({ value }: { readonly value: string }) {
  const [status, setStatus] = useState<"idle" | "pending" | "copied" | "unavailable">("idle");
  async function copy() {
    setStatus("pending");
    try {
      await navigator.clipboard.writeText(value);
      setStatus("copied");
    } catch {
      setStatus("unavailable");
    }
  }
  return (
    <>
      <button
        type="button"
        className="icon-button identity-copy"
        aria-label={`Copy identifier ${shortIdentity(value)}`}
        title="Copy full identifier"
        disabled={status === "pending"}
        onClick={() => void copy()}
      >
        {status === "copied" ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      </button>
      <span className="identity-copy-status" role="status">
        {status === "copied"
          ? "Copied"
          : status === "unavailable"
            ? "Copy unavailable. Select the full identifier."
            : ""}
      </span>
    </>
  );
}
