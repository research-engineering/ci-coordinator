import { type KeyboardEvent, useRef } from "react";
import { EVIDENCE_TABS, type EvidenceTab } from "./navigation";

const LABELS = {
  plans: "Plans",
  runs: "Runs",
  configEpochs: "Configuration",
  overrides: "Overrides",
} as const;

export function EvidenceTabs({
  selected,
  onSelect,
}: {
  readonly selected: EvidenceTab;
  readonly onSelect: (tab: EvidenceTab) => void;
}) {
  const buttons = useRef<Partial<Record<EvidenceTab, HTMLButtonElement | null>>>({});

  function keyDown(event: KeyboardEvent<HTMLButtonElement>, tab: EvidenceTab) {
    const current = EVIDENCE_TABS.indexOf(tab);
    const index =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? EVIDENCE_TABS.length - 1
          : event.key === "ArrowRight"
            ? (current + 1) % EVIDENCE_TABS.length
            : event.key === "ArrowLeft"
              ? (current + EVIDENCE_TABS.length - 1) % EVIDENCE_TABS.length
              : undefined;
    if (index === undefined) return;
    const next = EVIDENCE_TABS[index];
    if (!next) return;
    event.preventDefault();
    onSelect(next);
    buttons.current[next]?.focus();
  }

  return (
    <div className="evidence-tabs" role="tablist" aria-label="Repository evidence">
      {EVIDENCE_TABS.map((tab) => (
        <button
          type="button"
          role="tab"
          id={`evidence-tab-${tab}`}
          key={tab}
          aria-selected={selected === tab}
          aria-controls={`repository-evidence-panel-${tab}`}
          tabIndex={selected === tab ? 0 : -1}
          ref={(element) => {
            buttons.current[tab] = element;
          }}
          onClick={() => onSelect(tab)}
          onKeyDown={(event) => keyDown(event, tab)}
        >
          {LABELS[tab]}
        </button>
      ))}
    </div>
  );
}
