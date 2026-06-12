// Per-dimension measurement inputs (cm). Dimensions + valid ranges mirror the
// v1 men's button-down schema (PRD §5.2). Values stored in cm — Sizeify stores
// cm internally everywhere; any unit toggle is a UI concern only.

const DIMENSIONS: { key: string; label: string; min: number; max: number }[] = [
  { key: "neck", label: "Neck", min: 33, max: 50 },
  { key: "chest", label: "Chest", min: 85, max: 140 },
  { key: "shoulder", label: "Shoulder", min: 38, max: 56 },
  { key: "sleeve", label: "Sleeve", min: 75, max: 100 },
  { key: "body_length", label: "Body length", min: 65, max: 90 },
];

export function MeasurementForm({
  value,
  onChange,
}: {
  value: Record<string, number>;
  onChange: (v: Record<string, number>) => void;
}) {
  function set(key: string, n: number) {
    onChange({ ...value, [key]: n });
  }

  return (
    <div className="measure">
      {DIMENSIONS.map((d) => {
        const v = value[d.key];
        const outOfRange = v != null && (v < d.min || v > d.max);
        return (
          <label key={d.key} className="measure__row">
            <span>{d.label} (cm)</span>
            <input
              type="number"
              step="0.5"
              value={v ?? ""}
              onChange={(e) => set(d.key, Number(e.target.value))}
              className={outOfRange ? "is-invalid" : ""}
            />
            {outOfRange && (
              <span className="measure__hint">
                expected {d.min}–{d.max} cm
              </span>
            )}
          </label>
        );
      })}
    </div>
  );
}
