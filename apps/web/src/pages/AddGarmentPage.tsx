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
  const [brand, setBrand] = useState("");
  const [sizeLabel, setSizeLabel] = useState("M");
  const [feedback, setFeedback] = useState("");
  const [signals, setSignals] = useState<FitSignal[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSave() {
    setSaving(true);
    setError(null);
    try {
      const res = await api.addGarment(
        {
          measurements_cm: measurements,
          brand: brand.trim() || "unknown",
          size_label: sizeLabel.trim() || "M",
        },
        feedback,
      );
      // Hold on the chips rather than navigating away — showing free text
      // become structured signals is the entire point of this screen. The
      // user dismisses once they've seen it.
      setSignals(res.signals);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save that shirt.");
    } finally {
      setSaving(false);
    }
  }

  if (signals) {
    return (
      <section className="add">
        <h2>Shirt added</h2>
        {signals.length > 0 ? (
          <SignalChips signals={signals} />
        ) : (
          <p className="add__empty">
            Saved. We couldn&rsquo;t read any fit signals from that description — the
            measurements still count toward your fit profile.
          </p>
        )}
        <div className="add__actions">
          <button onClick={onDone}>Back to my closet</button>
        </div>
      </section>
    );
  }

  return (
    <section className="add">
      <h2>Add a shirt</h2>

      <div className="add__identity">
        <label className="measure__row">
          <span>Brand</span>
          <input
            value={brand}
            onChange={(e) => setBrand(e.target.value)}
            placeholder="e.g. jcrew"
          />
        </label>
        <label className="measure__row">
          <span>Size</span>
          <input value={sizeLabel} onChange={(e) => setSizeLabel(e.target.value)} />
        </label>
      </div>

      <MeasurementForm value={measurements} onChange={setMeasurements} />

      <label className="add__feedback">
        How does it fit? (optional)
        <textarea
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          placeholder="e.g. perfect in the chest but the collar is a little tight"
        />
      </label>

      {error && <p className="add__error">{error}</p>}

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
