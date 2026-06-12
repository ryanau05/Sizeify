// Wire types mirrored from api.schemas (keep in sync by hand for the demo).
// These are the contracts the web client renders against.

export type Verdict =
  | "too_tight"
  | "slightly_tight"
  | "preferred"
  | "slightly_loose"
  | "too_loose"
  | "slightly_short"
  | "too_short";

export interface ReferenceGarment {
  label: string;
  brand: string;
  size_label: string;
  why: string; // one-line reason this garment drove the call
}

export interface SizeCandidate {
  size_label: string;
  fit_notes: string[]; // per-dimension narrative (PRD §5.4)
  tradeoff?: string; // present when two candidates are returned
}

// The four mandatory PRD §5.4 components, plus the candidate list.
export interface Recommendation {
  brand: string;
  product_name: string;
  primary: SizeCandidate;
  alternate?: SizeCandidate; // present when confidence < 60%
  confidence: number; // 0..1
  reference_garments: ReferenceGarment[];
}

export interface Garment {
  id: string;
  label: string;
  brand: string;
  size_label: string;
  stretch_level: "none" | "slight" | "moderate" | "high";
  measurements_cm: Record<string, number>;
}

export interface FitSignal {
  dimension: string;
  verdict: Verdict;
  source: "nlp_extracted" | "user_edited" | "user_added";
  raw_feedback_text: string;
}
