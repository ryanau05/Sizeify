import { useState } from "react";
import { api } from "../api/client";
import type { Recommendation } from "../api/types";
import { RecommendationCard } from "../components/RecommendationCard";

// Headline demo flow: paste a partner-brand product URL, get a size.
// Stands in for the native share-sheet entry point.
export function PasteUrlPage() {
  const [url, setUrl] = useState("");
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setRec(null);
    try {
      setRec(await api.recommendFromUrl(url));
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="paste">
      <p className="paste__lede">
        Paste a shirt from any partner brand. We'll tell you what size to buy
        based on the shirts you already own.
      </p>
      <form onSubmit={onSubmit} className="paste__form">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.jcrew.com/p/..."
          aria-label="Product URL"
        />
        <button disabled={loading || !url}>
          {loading ? "Finding your size…" : "Find my size"}
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      {rec && <RecommendationCard rec={rec} />}
    </section>
  );
}
