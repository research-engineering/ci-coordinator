export function LoadingState({
  label,
  compact = false,
}: {
  readonly label: string;
  readonly compact?: boolean;
}) {
  return (
    <section
      className={`loading-state${compact ? " loading-state--compact" : ""}`}
      aria-label={label}
      aria-live="polite"
    >
      <div className="loading-progress" role="progressbar" aria-label={label} />
      <h2>{label}</h2>
      {compact ? null : (
        <div className="loading-skeleton" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
      )}
    </section>
  );
}
