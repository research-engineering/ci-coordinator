import { Search } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import {
  admitWorkbenchScope,
  type WorkbenchScope,
  workbenchScopeSchema,
} from "../../api/workbench/client";
import { MAX_WORKBENCH_SECTION_ITEMS } from "../../api/workbench/limits";

interface ScopeFormProps {
  readonly initialScope: WorkbenchScope;
  readonly onSubmit: (scope: WorkbenchScope) => void;
}

const FIELDS = [
  { name: "installationId", label: "Installation", max: Number.MAX_SAFE_INTEGER },
  { name: "repositoryId", label: "Repository", max: Number.MAX_SAFE_INTEGER },
  { name: "limit", label: "Items per section", max: MAX_WORKBENCH_SECTION_ITEMS },
] as const;

export function ScopeForm({ initialScope, onSubmit }: ScopeFormProps) {
  const id = useId();
  const [draft, setDraft] = useState({
    installationId: String(initialScope.installationId),
    repositoryId: String(initialScope.repositoryId),
    limit: String(initialScope.limit),
  });
  const [submitted, setSubmitted] = useState(false);
  const invalidFields = FIELDS.filter(
    ({ name }) => !workbenchScopeSchema.shape[name].safeParse(Number(draft[name])).success,
  );

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const scope = admitWorkbenchScope({
      installationId: Number(draft.installationId),
      limit: Number(draft.limit),
      repositoryId: Number(draft.repositoryId),
    });
    setSubmitted(true);
    if (scope) {
      onSubmit(scope);
    } else {
      const first = invalidFields[0];
      const input = first && event.currentTarget.elements.namedItem(first.name);
      if (input instanceof HTMLInputElement) input.focus();
    }
  }

  return (
    <form className="scope-form" onSubmit={submit} noValidate>
      {FIELDS.map((field) => (
        <label key={field.name}>
          {field.label}
          <input
            aria-describedby={`${id}-${field.name}-error`}
            aria-invalid={submitted && invalidFields.includes(field)}
            inputMode="numeric"
            max={field.max}
            min="1"
            name={field.name}
            required
            type="number"
            value={draft[field.name]}
            onChange={(event) => {
              const value = event.currentTarget.value;
              setDraft((previous) => ({ ...previous, [field.name]: value }));
            }}
          />
        </label>
      ))}
      <button type="submit" className="button button--primary">
        <Search className="button-icon" aria-hidden="true" />
        Load snapshot
      </button>
      <p className="form-error scope-errors" role="alert" aria-atomic="true">
        {FIELDS.map((field) => (
          <span id={`${id}-${field.name}-error`} key={field.name}>
            {submitted && invalidFields.includes(field)
              ? `${field.label}: enter a whole number between 1 and ${field.max}.`
              : null}
          </span>
        ))}
      </p>
    </form>
  );
}
