import type { FitSignal } from "../api/types";

// Renders extracted fit signals as chips. In the real product these are
// editable (PRD §6.1 review step); for the demo, read-only is enough to show
// free text -> structured signals.
// The wire carries canonical dimension names (neck_circumference,
// shoulder_width, …) — the same vocabulary the matching engine uses. Map them
// to the words a person would say when reading the chips aloud on stage.
const LABELS: Record<string, string> = {
  neck_circumference: "collar",
  chest: "chest",
  shoulder_width: "shoulders",
  sleeve_length: "sleeves",
  body_length: "length",
  cuff_circumference: "cuffs",
};

export function SignalChips({ signals }: { signals: FitSignal[] }) {
  return (
    <div className="chips">
      <div className="chips__title">We read your feedback as:</div>
      <ul className="chips__list">
        {signals.map((s, i) => (
          <li key={i} className={`chip chip--${s.verdict}`}>
            {LABELS[s.dimension] ?? s.dimension.replace(/_/g, " ")}:{" "}
            {s.verdict.replace(/_/g, " ")}
          </li>
        ))}
      </ul>
    </div>
  );
}
