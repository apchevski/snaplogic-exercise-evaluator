// Shared building blocks for the classic-dashboard look: a panel with a navy
// title bar, a toolbar search box, sortable column headers (sorted column gets
// the lavender highlight), and the "Last updated / 1 of N / Go to" footer.
import { useMemo, useState, type ReactNode } from "react";

export interface SortState {
  key: string;
  dir: "asc" | "desc";
}

/** Toggle direction on the active column, or switch column with its default direction. */
export function nextSort(
  sort: SortState,
  key: string,
  defaultDir: "asc" | "desc",
): SortState {
  if (sort.key === key) return { key, dir: sort.dir === "asc" ? "desc" : "asc" };
  return { key, dir: defaultDir };
}

export function Panel({
  title,
  hint,
  toolbar,
  footer,
  children,
}: {
  title: string;
  hint?: string;
  toolbar?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <header className="panel-header">
        <h2>{title}</h2>
        {hint && (
          <span className="help-icon" title={hint} aria-label={hint}>
            ?
          </span>
        )}
      </header>
      {toolbar && <div className="panel-toolbar">{toolbar}</div>}
      {children}
      {footer}
    </section>
  );
}

export function SearchBox({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    <span className="search-box">
      <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
        <circle cx="6.5" cy="6.5" r="4.5" fill="none" stroke="#8a97a3" strokeWidth="1.6" />
        <line x1="10" y1="10" x2="14" y2="14" stroke="#8a97a3" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <input
        type="search"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </span>
  );
}

/** SnapLogic-style square selection checkbox used in table select columns.
 * `indeterminate` renders the header "some rows selected" dash state. */
export function RowCheckbox({
  checked,
  indeterminate,
  onChange,
  ariaLabel,
  disabled,
}: {
  checked: boolean;
  indeterminate?: boolean;
  onChange: () => void;
  ariaLabel: string;
  disabled?: boolean;
}) {
  return (
    <input
      type="checkbox"
      className="row-select"
      checked={checked}
      disabled={disabled}
      ref={(el) => {
        if (el) el.indeterminate = !checked && !!indeterminate;
      }}
      onChange={onChange}
      aria-label={ariaLabel}
    />
  );
}

export function SortableTh({
  label,
  sortKey,
  sort,
  onSort,
}: {
  label: string;
  sortKey: string;
  sort: SortState;
  onSort: (key: string) => void;
}) {
  const active = sort.key === sortKey;
  return (
    <th className={active ? "sorted" : ""} aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className="th-sort" onClick={() => onSort(sortKey)}>
        {label}
        <span className="sort-arrows" aria-hidden="true">
          <i className={active && sort.dir === "asc" ? "on" : ""}>▲</i>
          <i className={active && sort.dir === "desc" ? "on" : ""}>▼</i>
        </span>
      </button>
    </th>
  );
}

export function usePagination<T>(items: T[], perPage: number) {
  const [rawPage, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(items.length / perPage));
  const page = Math.min(rawPage, pageCount);
  const pageItems = useMemo(
    () => items.slice((page - 1) * perPage, page * perPage),
    [items, page, perPage],
  );
  return { page, setPage, pageItems, pageCount };
}

export function PagerFooter({
  page,
  pageCount,
  onPage,
  lastUpdated,
}: {
  page: number;
  pageCount: number;
  onPage: (p: number) => void;
  lastUpdated?: string | null;
}) {
  const [goto, setGoto] = useState("");
  const jump = () => {
    const n = Number.parseInt(goto, 10);
    if (Number.isFinite(n)) onPage(Math.min(Math.max(n, 1), pageCount));
    setGoto("");
  };
  return (
    <div className="panel-footer">
      <span className="last-updated">
        {lastUpdated ? `Last updated ${lastUpdated}` : ""}
      </span>
      <span className="pager">
        <button type="button" className="pager-btn" disabled={page <= 1} onClick={() => onPage(1)} aria-label="First page">
          «
        </button>
        <button type="button" className="pager-btn" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
          ‹
        </button>
        <span className="pager-pos">
          {page} of {pageCount}
        </span>
        <button type="button" className="pager-btn" disabled={page >= pageCount} onClick={() => onPage(page + 1)} aria-label="Next page">
          ›
        </button>
        <button type="button" className="pager-btn" disabled={page >= pageCount} onClick={() => onPage(pageCount)} aria-label="Last page">
          »
        </button>
        <label className="goto">
          Go to
          <input
            value={goto}
            onChange={(e) => setGoto(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") jump();
            }}
            aria-label="Go to page"
          />
        </label>
      </span>
    </div>
  );
}
