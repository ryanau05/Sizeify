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
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
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
