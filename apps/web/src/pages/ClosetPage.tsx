import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Garment } from "../api/types";
import { AddGarmentPage } from "./AddGarmentPage";

// Lists the seeded closet and offers the add-garment onboarding flow.
export function ClosetPage() {
  const [garments, setGarments] = useState<Garment[]>([]);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setGarments(await api.getCloset());
    } catch (err) {
      setError(String(err));
    }
  }
  useEffect(() => {
    load();
  }, []);

  if (adding) {
    return (
      <AddGarmentPage
        onDone={() => {
          setAdding(false);
          load();
        }}
      />
    );
  }

  return (
    <section className="closet">
      <div className="closet__head">
        <h2>My closet</h2>
        <button onClick={() => setAdding(true)}>+ Add a shirt</button>
      </div>
      {error && <p className="error">{error}</p>}
      <ul className="closet__list">
        {garments.map((g) => (
          <li key={g.id} className="closet__item">
            <div className="closet__item-label">{g.label}</div>
            <div className="closet__item-meta">
              {g.brand} · {g.size_label} · stretch: {g.stretch_level}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
