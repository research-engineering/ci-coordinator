import { Check, Eye, Save, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  type RegistrationCommand,
  type ValidatedSource,
  validateSource,
} from "../../api/configLifecycle/client";
import {
  type ConfigScope,
  MAX_SOURCE_BYTES,
  type SourceFormat,
  sourceSchema,
} from "../../api/configLifecycle/schema";
import { readSourceFile } from "../../api/configLifecycle/source";
import type { LifecycleFailure } from "../../api/configLifecycle/transport";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import { ConfigurationNotice } from "./ConfigurationNotice";

export function SourceEditor({
  scope,
  session,
  active,
  locked,
  onRegister,
}: {
  readonly scope: ConfigScope;
  readonly session: ControlPlaneSession;
  readonly active: boolean;
  readonly locked: boolean;
  readonly onRegister: (command: RegistrationCommand) => void;
}) {
  const [source, setSource] = useState("");
  const [format, setFormat] = useState<SourceFormat>("yaml-1.2");
  const [validation, setValidation] = useState<ValidatedSource>();
  const [review, setReview] = useState<RegistrationCommand>();
  const [failure, setFailure] = useState<LifecycleFailure>();
  const [uploadError, setUploadError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const controller = useRef<AbortController | undefined>(undefined);
  const fileInput = useRef<HTMLInputElement>(null);
  const sourceInput = useRef<HTMLTextAreaElement>(null);
  const confirm = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!active) {
      generation.current += 1;
      controller.current?.abort();
      controller.current = undefined;
      setBusy(false);
      setReview(undefined);
    }
    return () => {
      generation.current += 1;
      controller.current?.abort();
      controller.current = undefined;
    };
  }, [active]);
  useEffect(() => {
    if (review) confirm.current?.focus();
  }, [review]);
  function invalidate() {
    generation.current += 1;
    controller.current?.abort();
    controller.current = undefined;
    setValidation(undefined);
    setReview(undefined);
    setFailure(undefined);
    setUploadError(undefined);
    setBusy(false);
  }
  const admitted = sourceSchema.safeParse({ source, sourceFormat: format }).success;
  const bytes = new TextEncoder().encode(source).length;
  async function validate() {
    if (locked || !active || !admitted || controller.current) return;
    invalidate();
    const ticket = generation.current;
    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    const result = await validateSource(scope, source, format, session.csrfToken, request.signal);
    if (request.signal.aborted || ticket !== generation.current) return;
    controller.current = undefined;
    setBusy(false);
    if (result.kind === "ready") setValidation(result.value);
    else setFailure(result);
  }
  async function upload(file: File) {
    if (locked || !active) return;
    invalidate();
    const ticket = generation.current;
    setBusy(true);
    try {
      const text = await readSourceFile(file);
      if (ticket !== generation.current) return;
      setSource(text);
      setBusy(false);
    } catch {
      if (ticket !== generation.current) return;
      setBusy(false);
      setUploadError("File rejected. Select a non-empty UTF-8 source of at most 2,097,152 bytes.");
    }
  }
  return (
    <section aria-label="Configuration source" className="configuration-source">
      <div className="configuration-toolbar">
        <label>
          Source format
          <select
            value={format}
            disabled={locked || busy || !active}
            onChange={(event) => {
              invalidate();
              setFormat(event.target.value as SourceFormat);
            }}
          >
            <option value="yaml-1.2">YAML 1.2</option>
            <option value="json">JSON</option>
          </select>
        </label>
        <input
          ref={fileInput}
          type="file"
          hidden
          accept=".json,.yaml,.yml,application/json,application/yaml"
          aria-label="Upload configuration file"
          disabled={locked || busy || !active}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void upload(file);
          }}
        />
        <button
          className="button button--secondary"
          type="button"
          disabled={locked || busy || !active}
          onClick={() => fileInput.current?.click()}
        >
          <Upload aria-hidden="true" className="button-icon" />
          Upload source
        </button>
        <span className="configuration-byte-count" aria-live="polite">
          {bytes.toLocaleString("en-US")} / {MAX_SOURCE_BYTES.toLocaleString("en-US")} bytes
        </span>
      </div>
      <label htmlFor="configuration-source-text">Source</label>
      <textarea
        id="configuration-source-text"
        ref={sourceInput}
        value={source}
        rows={16}
        spellCheck={false}
        disabled={locked || !active}
        aria-invalid={source.length > 0 && !admitted}
        onChange={(event) => {
          invalidate();
          setSource(event.target.value);
        }}
      />
      {source.length > 0 && !admitted ? (
        <p role="alert">Source exceeds the UTF-8 limit or contains invalid Unicode.</p>
      ) : null}
      {uploadError ? <p role="alert">{uploadError}</p> : null}
      {failure ? <ConfigurationNotice failure={failure} /> : null}
      {validation ? (
        <p role="status">
          <Check className="button-icon" aria-hidden="true" />
          Source validated for repository {scope.repositoryId}.
        </p>
      ) : null}
      <div className="configuration-actions">
        <button
          type="button"
          className="button button--secondary"
          disabled={locked || busy || !admitted || !active}
          onClick={() => void validate()}
        >
          <Check className="button-icon" aria-hidden="true" />
          {busy ? "Reading source" : "Validate source"}
        </button>
        <button
          type="button"
          className="button"
          disabled={locked || busy || !validation || !active}
          onClick={() => {
            if (validation)
              setReview({
                kind: "registration",
                draft: validation,
                operationId: crypto.randomUUID(),
              });
          }}
        >
          <Eye className="button-icon" aria-hidden="true" />
          Review registration
        </button>
      </div>
      {review ? (
        <section className="configuration-review" aria-label="Registration review">
          <h2>Register validated source</h2>
          <dl>
            <dt>Repository</dt>
            <dd>
              {scope.repositoryId} (installation {scope.installationId})
            </dd>
            <dt>Format / size</dt>
            <dd>
              {format} / {bytes.toLocaleString("en-US")} bytes
            </dd>
            <dt>Source hash</dt>
            <dd>
              <code>{review.draft.validation.sourceHash}</code>
            </dd>
            <dt>Epoch</dt>
            <dd>
              <code>{review.draft.validation.epochId}</code>
            </dd>
          </dl>
          <p>Registration retains this source. The active configuration will not change.</p>
          <div className="configuration-actions">
            <button
              ref={confirm}
              type="button"
              className="button"
              disabled={locked || !active}
              onClick={() => {
                onRegister(review);
                setReview(undefined);
              }}
            >
              <Save className="button-icon" aria-hidden="true" />
              Confirm registration
            </button>
            <button
              type="button"
              className="button button--secondary"
              disabled={locked}
              onClick={() => {
                setReview(undefined);
                sourceInput.current?.focus();
              }}
            >
              Cancel
            </button>
          </div>
        </section>
      ) : null}
    </section>
  );
}
