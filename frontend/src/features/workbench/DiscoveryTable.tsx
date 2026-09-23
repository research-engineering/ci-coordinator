import { ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";
import { type DataColumn, DataTable } from "../../components/DataTable";

const PAGE_SIZE = 25;

interface DiscoveryTableProps<Row> {
  readonly columns: readonly DataColumn<Row>[];
  readonly emptyLabel: string;
  readonly keyOf: (row: Row) => string;
  readonly label: string;
  readonly rows: readonly Row[];
}

export function DiscoveryTable<Row>({
  columns,
  emptyLabel,
  keyOf,
  label,
  rows,
}: DiscoveryTableProps<Row>) {
  const [requestedPage, setRequestedPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(requestedPage, pageCount);
  const start = (page - 1) * PAGE_SIZE;
  const visibleRows = rows.slice(start, start + PAGE_SIZE);

  return (
    <div className="discovery-table">
      <DataTable columns={columns} emptyLabel={emptyLabel} keyOf={keyOf} rows={visibleRows} />
      {rows.length > PAGE_SIZE ? (
        <nav className="discovery-pagination" aria-label={`${label} pagination`}>
          <span>
            {start + 1}-{Math.min(start + PAGE_SIZE, rows.length)} of {rows.length}
          </span>
          <button
            type="button"
            className="icon-button"
            disabled={page === 1}
            onClick={() => setRequestedPage(page - 1)}
            title={`Previous ${label} page`}
          >
            <ChevronLeft aria-hidden="true" />
          </button>
          <button
            type="button"
            className="icon-button"
            disabled={page === pageCount}
            onClick={() => setRequestedPage(page + 1)}
            title={`Next ${label} page`}
          >
            <ChevronRight aria-hidden="true" />
          </button>
        </nav>
      ) : null}
    </div>
  );
}
