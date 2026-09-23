import { useEffect, useRef, useState } from "react";
import type { ReportBudget } from "../../api/ciEconomics/comparisonSchema";
import { evaluateMeasurementBudget } from "../../api/ciEconomics/measurementClient";
import {
  COUNTER_ORDER,
  type ReportCounter,
  type RetainedReport,
} from "../../api/ciEconomics/measurementSchema";
import type { EconomicsResult } from "../../api/ciEconomics/transport";
import { formatInteger } from "../../domain/format";
import { EconomicsError } from "./EconomicsControls";

export function ReportBudgetControl({ report }: { readonly report: RetainedReport }) {
  const [counter, setCounter] = useState<ReportCounter>("cpu_user");
  const [maximum, setMaximum] = useState("");
  const [result, setResult] = useState<EconomicsResult<ReportBudget>>();
  const [pending, setPending] = useState(false);
  const controller = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => controller.current?.abort(), []);
  const valid = /^(0|[1-9][0-9]{0,15})$/.test(maximum) && Number.isSafeInteger(Number(maximum));
  function evaluate() {
    if (!valid) return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setPending(true);
    setResult(undefined);
    void evaluateMeasurementBudget(report, counter, Number(maximum), current.signal).then(
      (value) => {
        if (current.signal.aborted || controller.current !== current) return;
        setPending(false);
        setResult(value);
      },
      () => {
        if (!current.signal.aborted) {
          setPending(false);
          setResult({ kind: "network-failure" });
        }
      },
    );
  }
  return (
    <section className="economics-subsection" aria-label="Report budget">
      <h4>One-report budget</h4>
      <form
        className="economics-discovery-form"
        onSubmit={(event) => {
          event.preventDefault();
          evaluate();
        }}
      >
        <label>
          Counter
          <select
            value={counter}
            onChange={(event) => {
              const value = COUNTER_ORDER.find((item) => item === event.target.value);
              if (value) setCounter(value);
            }}
          >
            {COUNTER_ORDER.map((item) => (
              <option key={item} value={item}>
                {item.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label>
          Maximum (microseconds)
          <input
            type="text"
            inputMode="numeric"
            value={maximum}
            onChange={(event) => setMaximum(event.target.value)}
          />
        </label>
        <button type="submit" className="button button--compact" disabled={!valid || pending}>
          Evaluate
        </button>
      </form>
      {pending ? (
        <p role="status">Evaluating budget</p>
      ) : result?.kind === "ready" ? (
        <div role="status">
          <strong>{result.value.outcome.replaceAll("_", " ")}</strong>
          <span>
            {" "}
            / {result.value.measurement.counter.replaceAll("_", " ")} /{" "}
            {formatInteger(result.value.maximumUs)} us maximum / caller supplied / exact report
          </span>
        </div>
      ) : result ? (
        <EconomicsError failure={result} onRetry={evaluate} />
      ) : null}
    </section>
  );
}
