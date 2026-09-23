import type { ReactNode } from "react";

export function ScrollableRegion({
  label,
  className,
  children,
}: {
  readonly label: string;
  readonly className: string;
  readonly children: ReactNode;
}) {
  return (
    <section
      aria-label={label}
      className={className}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: Scrollable content without controls needs its own keyboard focus target.
      tabIndex={0}
    >
      {children}
    </section>
  );
}
