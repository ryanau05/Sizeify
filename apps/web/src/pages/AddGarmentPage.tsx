import { useState } from "react";
import { api } from "../api/client";
import type { FitSignal } from "../api/types";
import { MeasurementForm } from "../components/MeasurementForm";
import { SignalChips } from "../components/SignalChips";

// Guided onboarding: enter measurements (cm) + optional free-text fit feedback.
// The feedback is run through the stubbed LLM extraction to show structured
// chips — the "type how it fits, get signals" moment.
export function AddGarmentPage({ onDone }: { onDone: () => void }) {
  const [measurements, setMeasurements] = useState<Record<string, number>>({});
  const [feedback, setFeedback] = useState("");
  const [signals, setSignals] = useState<FitSignal[]>([]);
  const [saving, setSaving] = useState(false);

  async function onSave() {
    setSaving(true);
    try {
      const res = await api.addGarment({ measurements_cm: measurements }, feedback);
      setSignals(res.signals);
      onDone();
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="add">
      <h2>Add a shirt</h2>
      <MeasurementForm value={measurements} onChange={setMeasurements} />

      <label className="add__feedback">
        How does it fit? (optional)
        <textarea
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          placeholder="e.g. perfect in the chest but the collar is a little tight"
        />
      </label>

      {signals.length > 0 && <SignalChips signals={signals} />}

      <div className="add__actions">
        <button onClick={onDone} className="ghost">
          Cancel
        </button>
        <button onClick={onSave} disabled={saving}>
          {saving ? "Saving…" : "Save shirt"}
        </button>
      </div>
    </section>
  );
}
