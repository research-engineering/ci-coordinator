import type { ReactNode } from "react";

type StatusTone = "positive" | "warning" | "negative" | "neutral" | "info";

interface StatusBadgeProps {
  readonly children: ReactNode;
  readonly className?: string;
  readonly tone: StatusTone;
}

export function StatusBadge({ children, className, tone }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge--${tone}${className ? ` ${className}` : ""}`}>
      {children}
    </span>
  );
}
