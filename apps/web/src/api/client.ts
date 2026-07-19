// Thin fetch wrapper for the demo API. Base URL + bearer auth.

import type { Garment, Recommendation, FitSignal } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
const JWT = import.meta.env.VITE_DEMO_JWT ?? "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(JWT ? { Authorization: `Bearer ${JWT}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json() as Promise<T>;
}

// Surface the server's own message. FastAPI's `detail` is either a string
// (our 422 for an unknown brand) or a list of per-field errors (add-garment
// range validation) — without unpacking it the UI can only say "422", which
// tells the user nothing about which measurement was rejected.
async function errorMessage(res: Response): Promise<string> {
  try {
    const { detail } = await res.json();
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((d) => (d.field ? `${d.field}: ${d.message}` : d.msg)).join(" · ");
    }
  } catch {
    // Non-JSON body — fall through to the status line.
  }
  return `${res.status} ${res.statusText}`;
}

export const api = {
  // Headline flow.
  recommendFromUrl: (url: string) =>
    req<Recommendation>("/demo/recommend-from-url", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),

  // Closet + onboarding.
  getCloset: () => req<Garment[]>("/demo/closet"),

  addGarment: (garment: Partial<Garment>, feedback?: string) =>
    req<{ garment: Garment; signals: FitSignal[] }>("/demo/closet/garments", {
      method: "POST",
      body: JSON.stringify({ ...garment, feedback }),
    }),
};
