import type { ActivityPage } from "../../api/activity/schema";
import { ScrollableRegion } from "../../components/ScrollableRegion";
import { formatDateTime } from "../../domain/format";

export function ActivityTable({ page }: { readonly page: ActivityPage }) {
  return (
    <ScrollableRegion className="activity-table-scroll" label="Activity table">
      <table className="activity-table">
        <caption>
          {page.context.source === "security"
            ? "Retained access events"
            : "Repository activity references"}
        </caption>
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Action</th>
            <th scope="col">Actor</th>
            <th scope="col">Outcome</th>
            <th scope="col">Reference</th>
          </tr>
        </thead>
        <tbody>
          {page.items.map((row) => (
            <tr key={row.sequence}>
              <td>
                <time dateTime={row.occurredAt}>{formatDateTime(row.occurredAt)}</time>
              </td>
              <td>{actionLabel(row.action)}</td>
              <td>{row.subject ?? row.actor ?? "Not recorded"}</td>
              <td>{row.outcome}</td>
              <td>
                <details>
                  <summary>Details</summary>
                  <dl>
                    <dt>Operation</dt>
                    <dd>
                      <code>{row.operationRef}</code>
                    </dd>
                    <dt>Actor ID</dt>
                    <dd>
                      <code>{row.actor ?? "Not recorded"}</code>
                    </dd>
                    {row.auditEventId ? (
                      <>
                        <dt>Audit event</dt>
                        <dd>
                          <code>{row.auditEventId}</code>
                        </dd>
                        <dt>Event hash</dt>
                        <dd>
                          <code>{row.eventHash}</code>
                        </dd>
                      </>
                    ) : null}
                  </dl>
                </details>
              </td>
            </tr>
          ))}
          {page.items.length === 0 ? (
            <tr>
              <td colSpan={5}>No retained activity matches this selection.</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </ScrollableRegion>
  );
}

export function actionLabel(action: string): string {
  return action.replace(/\/v\d+$/, "").replaceAll(/[-_]/g, " ");
}
