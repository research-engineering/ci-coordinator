import {
  type BudgetPolicy,
  budgetCommandSchema,
  budgetMutationSchema,
  budgetPoliciesSchema,
  budgetSignalsSchema,
} from "../src/api/ciEconomics/budgetSchema";
import { reportPageSchema, sourcePageSchema } from "../src/api/ciEconomics/catalogSchema";
import {
  reportBudgetSchema,
  reportComparisonSchema,
} from "../src/api/ciEconomics/comparisonSchema";
import {
  attemptMeasurementsSchema,
  retainedReportSchema,
} from "../src/api/ciEconomics/measurementSchema";
import {
  type ProviderSource,
  sourceDiscoveryInputSchema,
  sourceDiscoverySchema,
  sourceRegistrationSchema,
} from "../src/api/ciEconomics/sourceSchema";
import { budgetPolicies, budgetPolicy, budgetSignals } from "../tests/economicsBudgetFixture";
import {
  economicsDiscovery,
  economicsMeasurements,
  economicsReport,
  economicsReportPage,
  economicsSource,
  economicsSourceItem,
  economicsSourcePage,
} from "../tests/economicsConsoleFixture";
import { exactQuery, response, type SyntheticRequest, type SyntheticResponse } from "./response";

export function economicsScenario(
  sources: readonly ProviderSource[] = [economicsSource(4202), economicsSource(4201)],
) {
  const reports = sources.map((source) => economicsReport(source));
  const commands: { path: string; csrf: string | undefined; body: unknown }[] = [];
  let policies: BudgetPolicy[] = [budgetPolicy()];

  function handle(request: SyntheticRequest): SyntheticResponse | undefined {
    const { url, method, body, csrf } = request;
    const path = url.pathname;
    if (method === "POST" && !url.search && csrf === "c".repeat(43)) {
      if (path === "/api/v2/economics/budget-policies") {
        const parsed = budgetCommandSchema.safeParse(body);
        if (!parsed.success || parsed.data.installationId !== 1 || parsed.data.repositoryId !== 1)
          return undefined;
        const command = parsed.data;
        const current = policies.find((item) => item.policyKey === command.policyKey);
        const conflict =
          (current?.revision ?? 0) !== command.expectedRevision
            ? "revision_conflict"
            : current === undefined && policies.length >= 16
              ? "capacity_reached"
              : undefined;
        commands.push({ path, csrf, body });
        if (conflict)
          return response(
            budgetMutationSchema,
            {
              schemaVersion: "ci-economics-budget-mutation/v1",
              operationId: command.operationId,
              outcome: conflict,
              policy: null,
            },
            409,
          );
        const policy: BudgetPolicy = {
          schemaVersion: "ci-economics-budget-policy/v1",
          installationId: 1,
          repositoryId: 1,
          policyKey: command.policyKey,
          revision: command.expectedRevision + 1,
          configuration: command.configuration,
        };
        policies = [...policies.filter((item) => item.policyKey !== policy.policyKey), policy].sort(
          (a, b) => (a.policyKey < b.policyKey ? -1 : a.policyKey > b.policyKey ? 1 : 0),
        );
        return response(budgetMutationSchema, {
          schemaVersion: "ci-economics-budget-mutation/v1",
          operationId: command.operationId,
          outcome: "committed",
          policy,
        });
      }
      if (path === "/api/v2/economics/source-discovery") {
        const parsed = sourceDiscoveryInputSchema.safeParse(body);
        if (!parsed.success || parsed.data.installationId !== 1 || parsed.data.repositoryId !== 1)
          return undefined;
        commands.push({ path, csrf, body });
        return response(sourceDiscoverySchema, {
          ...economicsDiscovery(),
          ...parsed.data,
        });
      }
      if (path === "/api/v2/economics/sources") {
        const source = sources.find((item) => {
          const { installationId, repositoryId, workflowRunId, runAttempt } = item.attempt;
          return (
            body !== null &&
            typeof body === "object" &&
            Object.keys(body).length === 4 &&
            Object.entries({ installationId, repositoryId, workflowRunId, runAttempt }).every(
              ([key, value]) => Reflect.get(body, key) === value,
            )
          );
        });
        if (!source) return undefined;
        commands.push({ path, csrf, body });
        return response(
          sourceRegistrationSchema,
          {
            schemaVersion: "ci-economics-source-registration/v2",
            source,
            outcome: "registered",
          },
          201,
        );
      }
    }
    if (method !== "GET") return undefined;
    for (const repositoryId of [1, 2]) {
      const root = `/api/v2/economics/repositories/1/${repositoryId}`;
      if (path === `${root}/budget-policies` && !url.search)
        return response(budgetPoliciesSchema, {
          ...budgetPolicies(repositoryId === 1 ? policies : []),
          repositoryId,
        });
      if (path === `${root}/budget-signals`) {
        const outcome = url.searchParams.get("outcome");
        if (!exactQuery(url, outcome === null ? { limit: "20" } : { limit: "20", outcome }))
          return undefined;
        const page = budgetSignals();
        return response(budgetSignalsSchema, {
          ...page,
          repositoryId,
          items:
            repositoryId === 1
              ? page.items.filter((item) => outcome === null || item.outcome === outcome)
              : [],
        });
      }
      if (path === `${root}/sources` && exactQuery(url, { limit: "20" })) {
        return response(sourcePageSchema, {
          ...economicsSourcePage(
            sources
              .filter((source) => source.attempt.repositoryId === repositoryId)
              .map(economicsSourceItem),
          ),
          repositoryId,
        });
      }
      for (const report of reports.filter(
        (item) => item.source.attempt.repositoryId === repositoryId,
      )) {
        const { source } = report;
        const attempt = source.attempt;
        if (
          path === `${root}/attempts/${attempt.workflowRunId}/${attempt.runAttempt}/measurements` &&
          exactQuery(url, { headSha: attempt.headSha })
        )
          return response(attemptMeasurementsSchema, economicsMeasurements(source));
        if (
          path === `${root}/sources/${source.sourceId}/reports` &&
          exactQuery(url, { limit: "20" })
        )
          return response(reportPageSchema, economicsReportPage(report));
        if (path === `${root}/reports/${report.reportId}` && !url.search)
          return response(retainedReportSchema, report);
        if (path === `${root}/reports/${report.reportId}/budget`) {
          const counter = url.searchParams.get("counter");
          const rawMaximum = url.searchParams.get("maximumUs");
          const measurement = report.payload.measurements.find((item) => item.counter === counter);
          if (
            !measurement ||
            !rawMaximum ||
            !/^(0|[1-9][0-9]*)$/.test(rawMaximum) ||
            !Number.isSafeInteger(Number(rawMaximum)) ||
            !exactQuery(url, { counter: measurement.counter, maximumUs: rawMaximum })
          )
            return undefined;
          const maximumUs = Number(rawMaximum);
          return response(reportBudgetSchema, {
            schemaVersion: "ci-economics-report-budget/v1",
            ok: true,
            report,
            measurement,
            maximumUs,
            thresholdAuthority: "caller_supplied",
            evaluationWindow: "exact_report",
            outcome:
              measurement.value === null
                ? "insufficient_evidence"
                : measurement.value > maximumUs
                  ? "breached"
                  : "within_budget",
          });
        }
      }
      if (path === `${root}/report-comparisons`) {
        const baseline = reports.find(
          (item) => item.reportId === url.searchParams.get("baselineReportId"),
        );
        const treatment = reports.find(
          (item) => item.reportId === url.searchParams.get("treatmentReportId"),
        );
        if (
          !baseline ||
          !treatment ||
          baseline === treatment ||
          baseline.source.attempt.repositoryId !== repositoryId ||
          treatment.source.attempt.repositoryId !== repositoryId ||
          !exactQuery(url, {
            baselineReportId: baseline.reportId,
            treatmentReportId: treatment.reportId,
          })
        )
          return undefined;
        return response(reportComparisonSchema, {
          schemaVersion: "ci-economics-report-comparison/v1",
          ok: true,
          baseline,
          treatment,
          pairDigest: "a".repeat(64),
          mismatches: [],
          coverageStatus: "not_verified",
          causalStatus: "not_established",
          differences: baseline.payload.measurements.map((item) => ({
            counter: item.counter,
            scope: item.scope,
            unit: item.unit,
            reduction: 0,
            relativeReduction: { numerator: 0, denominator: item.value },
          })),
        });
      }
    }
    return undefined;
  }
  return { commands, handle };
}
