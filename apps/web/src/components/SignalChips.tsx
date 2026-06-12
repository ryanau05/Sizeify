import type { FitSignal } from "../api/types";

// Renders extracted fit signals as chips. In the real product these are
// editable (PRD §6.1 review step); for the demo, read-only is enough to show
// free text -> structured signals.
export function SignalChips({ signals }: { signals: FitSignal[] }) {
  return (
    <div className="chips">
      <div className="chips__title">We read your feedback as:</div>
      <ul className="chips__list">
        {signals.map((s, i) => (
          <li key={i} className={`chip chip--${s.verdict}`}>
            {s.dimension}: {s.verdict.replace("_", " ")}
          </li>
        ))}
      </ul>
    </div>
  );
}
