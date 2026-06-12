# `api.demo` — capstone demo layer (throwaway, flag-gated)

This package exists **only** to power the 3-day capstone prototype demo. It is
strictly additive and never imported by the production app (`api.main:app`).
Nothing in `api.demo` is part of the v1 roadmap; it can be deleted wholesale
once Phase 2 (real scrapers) and Phase 3 (real LLM extraction) land.

## Isolation contract

- **Production app is untouched.** `api.main:app` does not import anything here.
  The demo runs a *separate* ASGI app, `api.demo.app:app`, which wraps the real
  app and bolts on the `/demo/*` router.
- **Real domain core is reused, not forked.** The demo calls the same
  fit-profile / matching / recommendation functions in `api.domain` that Phase 1
  Track B delivers. Only the *edges* are stubbed:
  - `stub_scraper.py` replaces the Phase 2 scraper service with a fixture lookup.
  - `stub_llm.py` replaces the Phase 3 LLM extraction with canned signals.
- **Everything here is labelled `DEMO`** so a later `git grep DEMO` and a single
  `rm -rf src/api/demo` fully removes the demo without touching real code.

## What's stubbed vs. real

| Concern | Demo | Real (roadmap) |
|---|---|---|
| Product URL → size chart | `stub_scraper` fixture lookup | Phase 2 per-brand scrapers |
| Free-text feedback → signals | `stub_llm` canned outputs | Phase 3 LLM extraction service |
| Fit profile construction | **real** `api.domain.fit_profile` | same |
| Matching + recommendation | **real** `api.domain.matching` / `recommendation` | same |
| Closet + measurement storage | **real** repositories + models | same |
| Share-sheet entry point | web paste-box (`apps/web`) | native share extension (Phase 6) |

## Run

```bash
# from apps/api
DEMO_MODE=1 uv run uvicorn api.demo.app:app --reload --port 8000
uv run python -m api.demo.seed_demo   # seed demo user + closet + brand products
```
