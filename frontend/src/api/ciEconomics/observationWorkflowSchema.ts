import { z } from "zod";
import type { components } from "../generated";
import { observationCounter } from "./observationSchema";
import { positiveInteger, repositoryScopeSchema } from "./sourceSchema";

export type ObservationWorkflows = components["schemas"]["ObservationWorkflowsResponse"];

function metadata(limit: number) {
  return z.string().refine(
    (text) =>
      text.trim().length > 0 &&
      Array.from(text).length <= limit &&
      !Array.from(text).some((character) => {
        const code = character.codePointAt(0) ?? 0;
        return code < 32 || code === 127 || (code >= 0xd800 && code <= 0xdfff);
      }),
  );
}

export const observationWorkflowsSchema: z.ZodType<ObservationWorkflows> = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    schemaVersion: z.literal("ci-economics-observation-workflows/v1"),
    pageNumber: z.number().int().min(1).max(20),
    providerTotal: observationCounter,
    termination: z.enum(["next_page", "exhausted", "truncated"]),
    items: z
      .array(
        z.strictObject({
          workflowId: positiveInteger,
          name: metadata(256),
          path: metadata(1024),
          state: metadata(64),
        }),
      )
      .max(100),
  })
  .refine((page) => {
    const end = (page.pageNumber - 1) * 100 + page.items.length;
    return (
      new Set(page.items.map((item) => item.workflowId)).size === page.items.length &&
      (page.termination === "exhausted"
        ? end === page.providerTotal
        : page.termination === "truncated" ||
          (page.items.length === 100 && end < page.providerTotal && page.pageNumber < 20))
    );
  });
