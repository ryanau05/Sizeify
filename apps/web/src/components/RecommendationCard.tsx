import type { Recommendation } from "../api/types";

// Renders the four mandatory PRD §5.4 components: size, confidence, fit notes,
// reference garments. When confidence < 60% the backend returns an alternate
// candidate with a tradeoff, and we show both.
export function RecommendationCard({ rec }: { rec: Recommendation }) {
  const pct = Math.round(rec.confidence * 100);
  const lowConfidence = rec.confidence < 0.6;

  return (
    <article className="rec">
      <header className="rec__head">
        <div>
          <div className="rec__brand">{rec.brand}</div>
          <div className="rec__product">{rec.product_name}</div>
        </div>
        <div className={`rec__confidence ${lowConfidence ? "is-low" : ""}`}>
          {pct}% confident
        </div>
      </header>

      <div className="rec__sizes">
        <div className="rec__size">
          <div className="rec__size-label">Size {rec.primary.size_label}</div>
          <ul className="rec__notes">
            {rec.primary.fit_notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
        {rec.alternate && (
          <div className="rec__size rec__size--alt">
            <div className="rec__size-label">
              or {rec.alternate.size_label}
            </div>
            <p className="rec__tradeoff">{rec.alternate.tradeoff}</p>
          </div>
        )}
      </div>

      <footer className="rec__refs">
        <div className="rec__refs-title">Based on shirts you own</div>
        <ul>
          {rec.reference_garments.map((g, i) => (
            <li key={i}>
              <strong>
                {g.brand} {g.size_label}
              </strong>{" "}
              — {g.why}
            </li>
          ))}
        </ul>
      </footer>
    </article>
  );
}
