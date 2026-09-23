import type { ReactNode } from "react";

export interface DataColumn<Row> {
  readonly header: string;
  readonly presentation?: "identity" | "time" | "prose";
  readonly render: (row: Row) => ReactNode;
}

interface DataTableProps<Row> {
  readonly columns: readonly DataColumn<Row>[];
  readonly emptyLabel: string;
  readonly keyOf: (row: Row) => string;
  readonly rows: readonly Row[];
}

export function DataTable<Row>({ columns, emptyLabel, keyOf, rows }: DataTableProps<Row>) {
  if (rows.length === 0) return <p className="empty-row">{emptyLabel}</p>;
  return (
    <section
      className="table-scroll"
      aria-label={`${emptyLabel.replace(/^No /, "")} table`}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: Safari requires keyboard access to scroll regions.
      tabIndex={0}
    >
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                scope="col"
                key={column.header}
                className={column.presentation ? `column-${column.presentation}` : undefined}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={keyOf(row)}>
              {columns.map((column) => (
                <td
                  key={column.header}
                  className={column.presentation ? `column-${column.presentation}` : undefined}
                >
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
