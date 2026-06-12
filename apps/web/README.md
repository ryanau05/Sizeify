# `apps/web` — Sizeify capstone demo client (throwaway)

A lightweight Vite + React + TypeScript web client built **only** for the 3-day
capstone prototype demo. It is the web stand-in for the native share-sheet flow:
the user pastes a product URL instead of sharing it from Safari/Chrome.

This app is **not** part of the v1 roadmap (which ships native iOS + Android,
Phases 4–6). It lives beside `apps/ios` and `apps/android` but shares nothing
with them and can be deleted after the demo without affecting either.

## Why a web client for the demo

Native share extensions, share intents, and rich push notifications (the real
discovery flow, PRD §9.5) are platform-specific and cannot be built or demoed in
3 days. The web client exercises the *same backend* (`api.demo.app:app`) and the
*same domain core*, so the demo proves the core claim — closet in, correct size
out — without the native plumbing.

## Stack

- Vite + React 18 + TypeScript
- Plain `fetch` against the demo API (`VITE_API_BASE`, default `http://localhost:8000`)
- No router lib needed (2–3 screens, simple local state). Keep deps minimal.

## Layout

```
apps/web/
  index.html
  package.json
  vite.config.ts
  tsconfig.json
  .env.example
  src/
    main.tsx              app entry
    App.tsx               shell + simple tab switch (Paste URL | Closet)
    styles.css
    api/
      client.ts           thin fetch wrapper (auth header, base URL)
      types.ts            wire types mirrored from api.schemas
    pages/
      PasteUrlPage.tsx    headline flow: URL in -> RecommendationCard
      ClosetPage.tsx      list seeded garments
      AddGarmentPage.tsx  guided measurement + free-text feedback -> chips
    components/
      RecommendationCard.tsx  size + confidence + fit notes + reference garments
      MeasurementForm.tsx     per-dimension cm inputs with range validation
      SignalChips.tsx         extracted fit signals (editable)
```

## Run

```bash
cd apps/web
npm install
cp .env.example .env        # point VITE_API_BASE at the demo API
npm run dev                 # http://localhost:5173
```

The demo API must be running with `DEMO_MODE=1` (see `apps/api/src/api/demo/`).
