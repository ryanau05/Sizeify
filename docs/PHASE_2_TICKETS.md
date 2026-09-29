# Phase 2 — Tickets

Phase 2 of `PROJECT_PLAN.md` ("Scrapers: interface, ten modules, fixtures," weeks 3–4) decomposed into atomic tickets. Each ticket should fit in 0.5–2 days of focused work and produce a reviewable PR.

**Phase 2 exit criterion (re-stated from the plan):** `uv run python -m scrapers.cli --brand <each> --url <fixture URL>` returns a complete `BrandProduct` with a size chart for every one of the ten brands, and the daily synthetic run is green for three consecutive days.

> **Status: not started.** Phase 1 is complete (543 tests, migrations `0001`–`0007`, backend `0.2.0`). Land the three open `TODOS.md` items' blockers when their phases arrive; none blocks Phase 2.

All tickets target `apps/api`. Refs are to `Sizeify_PRD_v1.docx` and `CLAUDE.md`. Paths follow `FILE_STRUCTURE.md`, the architecture source of truth.

Two layout points, because `PROJECT_PLAN.md` and `FILE_STRUCTURE.md` read differently at a glance:

- **Scrapers live at `apps/api/src/scrapers/`**, a sibling of `src/api/` — `FILE_STRUCTURE.md` line 204 ("`scrapers/` is a sibling of `api/`"), `PROJECT_PLAN.md`'s `apps/api/src/scrapers/base.py`, and CLAUDE.md's `python -m scrapers.cli` all agree. `pythonpath = ["src"]` already makes both packages importable. Each brand is its own package with fixtures inside it (`scrapers/uniqlo/scraper.py`, `scrapers/uniqlo/fixtures/`), per `FILE_STRUCTURE.md` lines 157–167.
- **Tests stay flat.** `FILE_STRUCTURE.md` sketches `tests/unit/...`, but Phase 1 never adopted it: the suite is `tests/test_*.py` plus `tests/integration/`. Phase 2 follows what exists — `tests/scrapers/test_<brand>.py` — and `FILE_STRUCTURE.md` should be corrected to match the repo rather than the repo reorganized to match a sketch nothing has followed. Same call `FILE_STRUCTURE.md`'s `llm/` sketch already got in Phase 1 (TKT-P1-16's note).

## Two contradictions settled before you start

Phase 1 lost time to a PRD that disagreed with itself in two places. Both of the equivalents for Phase 2 are settled here; if you disagree, change this file first rather than the code.

**Scrape timeout is 1.5 s, not 1 s.** PRD §7.3's performance table says "Product lookup timeout 1 second"; PRD §9.2's per-step budget for "identify brand from URL hostname, dispatch to brand-specific scraper" says 1.5 s, and `PROJECT_PLAN.md` follows §9.2. §9.2 wins, because it is the section that adds the budgets up to the 2.5 s p50 total and reconciles with the 3 s SLO — §7.3's figure cannot be spent without breaking that arithmetic. The 1.5 s is the **total** on-demand path: resolve is separately budgeted at 200 ms and is not inside it.

**Size charts are normalized to cm and to canonical dimension names at the scraper boundary.** CLAUDE.md is unambiguous that measurements are stored in cm and that `owned_garment.measurements` is keyed by canonical dimension name. `brand_product.size_chart` must obey the same rule so `domain/matching.py` can compare the two without a translation layer — it indexes both by the names in `dimension_weights`. Most of these ten retailers publish inches, and several label rows "Chest (in.)" or "Sleeve length". Converting and renaming is the scraper's job, not the matching engine's.

---

## Phase 2 prerequisites (foundation)

Small foundation tasks the tickets below assume. Land them first; a single PR is fine.

### TKT-P2-00a — `scrapers` package skeleton and dependencies

**Scope:** Create the sibling package and pin what Phase 2 needs, without pulling a browser into the default install.

**Deliverables**

- `apps/api/src/scrapers/__init__.py` and a `scrapers/ruff.toml` that bans `api.repositories` and `api.deps` imports via TID251 — a scraper returns data, it does not write to the database (the registry and the worker do). Mirrors `src/api/routes/ruff.toml`.
- `httpx` already present; add `selectolax` (or `beautifulsoup4` + `lxml` — pick one and state why in the PR) for HTML parsing.
- `apps/api/src/scrapers/playwright_pool.py` as the single import site for Playwright (`FILE_STRUCTURE.md` line 163), so exactly one module has to be checked to answer "does a browser get loaded?".
- `playwright` as an **optional dependency group**, not a default install, plus a `scrapers-js` extra. CLAUDE.md forbids dragging a headless browser into modules that don't need it; keeping it out of the base install is what makes that enforceable rather than aspirational.
- `pytest-httpx` (or `respx`) for asserting on outbound requests without network access.
- `arq==0.28.0` is already pinned; no change.

**Acceptance**

- `uv sync` without extras installs no browser binary; `uv run python -c "import scrapers"` succeeds.
- `uv run ruff check` passes, and a deliberate `from api.repositories import ...` inside `src/scrapers/` fails it.

**Refs:** CLAUDE.md scrapers section and gotchas; `FILE_STRUCTURE.md`.

---

### TKT-P2-00b — Fixture harness: frozen HTML, no network in CI

**Scope:** The test seam every brand ticket depends on. Two modes over one code path: frozen fixtures in CI, live URLs in the scheduled job.

**Deliverables**

- Fixtures live with the brand, not with the tests: `apps/api/src/scrapers/<brand>/fixtures/<slug>.html` plus a sibling `<slug>.expected.json` holding the expected normalized `BrandProduct` (`FILE_STRUCTURE.md` line 167).
- A `load_fixture(brand, slug)` helper and a `frozen_client` fixture that serves the stored HTML through the real `httpx` transport, so a brand module is exercised through its actual request path rather than by calling its parser directly.
- An autouse guard that **fails any scraper test that opens a socket**, so a brand module cannot quietly depend on the live site in CI.
- A recording helper (`uv run python -m scrapers.cli --record --brand <b> --url <u>`) that writes both files, so refreshing a fixture after a site change is one command rather than manual curl-and-paste.

**Acceptance**

- A scraper test that reaches the network fails with a clear message naming the offending host.
- `uv run pytest tests/scrapers` passes with no network available (verify with the network disabled, not just assumed).

**Refs:** CLAUDE.md scrapers section ("fixture file with at least 5 known products"); `PROJECT_PLAN.md` Phase 2.

---

### TKT-P2-00c — Settings and compose for the worker

**Scope:** Expand `Settings` for the scrape path; confirm Redis is reachable.

**Deliverables**

- `Settings` gains: `scrape_timeout_seconds: float = 1.5`, `url_resolve_timeout_seconds: float = 2.0`, `scraper_user_agent: str`, `brand_product_ttl_seconds: int`, `scrape_rate_limit_per_host_per_second: float`.
- `redis_url` already exists; confirm `infra/docker-compose.yml` exposes Redis and document the worker's start command in CLAUDE.md's command list.
- Every timeout is a setting, not a literal, for the same reason the rate-limit budgets are: the tests need to drive the timeout path without waiting 1.5 s per case.

**Acceptance**

- `uv run pytest` passes with the new fields defaulted; a test overrides `scrape_timeout_seconds` via env and observes the change.

**Refs:** PRD §9.2, §7.3; `apps/api/src/api/config.py`.

---

## TKT-P2-01 — `scrapers/base.py`: the uniform interface

**Scope:** The contract every brand module implements, and the three outcomes PRD §9.3 names. This is the ticket the other nine brand tickets are written against, so it lands first and changes rarely.

**Deliverables**

- `apps/api/src/scrapers/base.py` exporting:
  - `ScrapedProduct` — a frozen dataclass carrying `brand`, `product_name`, `product_url` (canonical), `category_id`, `size_chart` (normalized, see TKT-P2-03), `fabric_composition | None`, `stretch_level | None`, `scraper_version`.
  - `class BrandScraper(Protocol)` with `hostnames: tuple[str, ...]`, `brand: str`, `scraper_version: str`, `size_chart_fallback_url: str | None`, and `async def scrape(url: str, client: httpx.AsyncClient) -> ScrapedProduct`.
  - Typed errors: `ProductNotFoundError`, `SizeChartUnavailableError`, `ScrapeTransportError`. PRD §9.3 requires the first two be **explicit** rather than an empty result — the `/recommend` path maps them to different user-facing notifications (§7.5), so collapsing them loses the distinction the product depends on.
- The `Protocol` takes an injected `client` rather than constructing its own. That is what lets TKT-P2-00b's frozen transport and TKT-P2-02's shared policy apply to every brand without each module re-implementing either.
- Module docstring stating the stability promise from PRD §9.3: brand implementations may be swapped without touching anything else.

**Acceptance**

- `uv run mypy src` clean, including a deliberate `Protocol` conformance check per brand in a test (`assert isinstance(module, BrandScraper)` style or a static `_: BrandScraper = module`).
- Unit test: each error type is raised by a stub scraper and is distinguishable by the caller.

**Refs:** PRD §9.3, §7.5; CLAUDE.md scrapers section.

---

## TKT-P2-02 — Outbound HTTP policy: user agent, robots.txt, rate limiting

**Scope:** One shared client policy that makes every brand module a well-behaved citizen. PRD §9.3 requires all three; doing them per-module guarantees at least one module forgets.

**Deliverables**

- `apps/api/src/scrapers/http.py` exposing a factory for the shared `httpx.AsyncClient`: the identified user agent from `Settings`, connect/read timeouts inside the 1.5 s budget, redirect following bounded, and an explicit `Accept-Language`.
- A per-host token bucket enforcing `scrape_rate_limit_per_host_per_second`. Reuse the shape of `api/rate_limit.py`'s `TokenBucketLimiter` — and read its docstring first: it records why eviction under pressure is a rate-limit *reset*, which is the same trap here.
- `robots.txt` fetch, parse, and cache per host, with a decision recorded in the log. A disallowed path raises `ScrapeTransportError` with a reason rather than proceeding.
- Structured logging via `api/logging.py` for every outbound fetch: host, status, duration, robots decision. **No URLs carrying query strings that could contain user identifiers**, and no user id — a scrape is triggered by a user but is not about them (PRD §11).

**Acceptance**

- Test: a `Disallow` in a fixture `robots.txt` blocks the fetch and names the rule.
- Test: two rapid requests to one host are spaced by the configured rate; requests to different hosts are not.
- Test: the user agent is present and identifies Sizeify on every request a brand module makes.

**Refs:** PRD §9.3, §11; `apps/api/src/api/rate_limit.py`, `apps/api/src/api/logging.py`.

---

## TKT-P2-03 — Size-chart normalization to cm and canonical dimensions

**Scope:** The translation layer that keeps `brand_product.size_chart` comparable to `owned_garment.measurements`. Pure functions, no HTTP.

**Deliverables**

- `apps/api/src/scrapers/normalize.py` exporting `normalize_size_chart(rows, *, unit_hint=None) -> dict[str, dict[str, float]]` producing `{size_label: {canonical_dimension: cm_value}}`.
- A label map from the wordings these retailers actually use onto the six canonical names in `domain/dimension_weights.py` — "Chest", "Chest (in.)", "Body Length", "Sleeve Length", "Neck", "Cuff". Unknown labels are **dropped with a logged warning, not guessed**: a mis-mapped dimension produces a confident wrong recommendation, which is worse than a missing one the matching engine can skip.
- Unit inference: explicit `in.`/`cm` markers win; a bare number is resolved by plausibility against PRD §5.2's ranges (a 41 "chest" is inches, a 104 is cm — and note the un-doubled pit-to-pit convention from Phase 1, so a single-panel chest near 54 cm is the target, per CLAUDE.md). Ambiguity raises rather than picking.
- Size-label ordering preserved (`XS < S < M < L < XL`) so `matching.py`'s tie-break on summed chart measurements stays meaningful.

**Acceptance**

- Unit tests: inches convert at 2.54 exactly; a cm chart passes through unchanged; an unknown label is dropped and logged; an ambiguous unit raises.
- Property test or table test: every normalized chart's keys are a subset of `dimension_weights`' keys — this is the invariant `matching.py` relies on.
- A round-trip test feeding a normalized chart into `domain.matching.match()` against a real `FitProfile` and getting a ranked size, proving the two shapes actually meet.

**Refs:** PRD §5.2, §6.4; CLAUDE.md domain conventions ("stored in cm", canonical dimension names); `apps/api/src/api/domain/dimension_weights.py`.

---

## TKT-P2-04 — Brand discriminator: hostname dispatch and allowlist

**Scope:** URL → brand module, with an explicit closed set. PRD §7.5's "unknown brand" notification depends on this failing loudly rather than guessing.

**Deliverables**

- `apps/api/src/scrapers/registry.py` mapping hostname (and `www.`/country-subdomain variants) to the registered `BrandScraper`, built from the modules themselves so registration cannot drift from the map.
- `UnknownBrandError` carrying the hostname, distinct from `ProductNotFoundError` — §7.5 gives them different notification copy.
- Allowlist is **exactly** the ten brands in PRD §7.6; anything else is unknown. CLAUDE.md requires approval to add a brand, so the test that pins the list to ten is the enforcement.
- Hostname matching is on the registrable domain, not a substring. `uniqlo.com.evil.example` must not match Uniqlo — the same substring-prefix trap that let `/me` match `/metrics` in the rate limiter.

**Acceptance**

- Test: every brand in §7.6 resolves; an unregistered host raises `UnknownBrandError`.
- Test: a look-alike hostname embedding a brand domain does **not** match.
- Test: the registry's brand count is exactly 10 and the names match §7.6 verbatim.

**Refs:** PRD §7.6, §7.5; CLAUDE.md v1 scope rules.

---

## TKT-P2-05 — URL resolver: redirects and social wrappers

**Scope:** Any shared URL → canonical product URL, inside 2 s, failing fast.

**Deliverables**

- `apps/api/src/api/services/url_resolver.py` exporting `async def resolve(url) -> str`. It lives under `api/services/` rather than `scrapers/` because the `/recommend` route calls it before a brand is even known.
- Redirect chain followed with a hard 2 s total budget and a bounded hop count. PRD §9.2 budgets a single HEAD at 200 ms; the 2 s is the ceiling for a pathological chain, not the target.
- Instagram and TikTok wrapper URLs handled explicitly (CLAUDE.md gotchas): extract the destination where the wrapper exposes it, and fail with a typed error where it does not, rather than scraping the wrapper's own HTML.
- **SSRF guard.** The input is a URL a user can choose. Reject non-HTTP(S) schemes, and reject hosts resolving to private, loopback, or link-local addresses — before the request, and again after each redirect hop, because a public host can redirect to `169.254.169.254`. This is not in the plan's text and is the single most important thing in this ticket.
- Query-string stripping of tracking parameters so cache keys collapse (`brand_product.product_url` is UNIQUE; `?utm_source=` variants would otherwise each miss the cache and trigger a scrape).

**Acceptance**

- Test: a 3-hop redirect resolves; a loop terminates within the budget with a typed error.
- Test: `http://127.0.0.1/`, `http://169.254.169.254/`, `file://`, and a public host redirecting to a private address are all rejected — the last one is the case a naive pre-flight check misses.
- Test: two URLs differing only in tracking parameters resolve to one canonical URL.
- Test: the 2 s budget is enforced against a deliberately slow fixture server.

**Refs:** PRD §9.2, §7.2; CLAUDE.md gotchas ("Hard 2s timeout — fail fast").

---

## TKT-P2-06 — API-first adapter for Shopify-backed brands

**Scope:** PRD §9.3 says to prefer an API path where one exists. Several of the ten run Shopify, whose `products/<handle>.js` endpoint returns structured JSON. One adapter serves all of them.

**Deliverables**

- `apps/api/src/scrapers/shopify.py` with a reusable `scrape_shopify_product(url, client, *, brand, category_id)` that a brand module delegates to in one line.
- Size-chart extraction from the Shopify payload where present, falling back to the brand's HTML size-chart route via `size_chart_fallback_url` — PRD §7.5 requires the fallback before declaring product-not-found.
- Which of the ten are Shopify is **a finding, not an assumption**: record it per brand in that brand's ticket as part of the investigation, and note in the PR which brands this adapter ended up serving.

**Acceptance**

- Unit tests against frozen Shopify JSON for at least two brands that use it.
- Test: a product whose payload has no size chart triggers the fallback URL, and a fallback that also lacks one raises `SizeChartUnavailableError` (not `ProductNotFoundError`).

**Refs:** PRD §9.3, §7.5, §7.6.

---

## TKT-P2-07 … TKT-P2-16 — The ten brand modules

One ticket per brand, in this order (cheapest signal first — land two, learn what the shared code is missing, then continue):

| Ticket | Brand | Hostname (to confirm) |
|---|---|---|
| TKT-P2-07 | Uniqlo | `uniqlo.com` |
| TKT-P2-08 | J.Crew | `jcrew.com` |
| TKT-P2-09 | Bonobos | `bonobos.com` |
| TKT-P2-10 | Everlane | `everlane.com` |
| TKT-P2-11 | Banana Republic | `bananarepublic.gap.com` |
| TKT-P2-12 | Brooks Brothers | `brooksbrothers.com` |
| TKT-P2-13 | Charles Tyrwhitt | `ctshirts.com` |
| TKT-P2-14 | Mr. Porter | `mrporter.com` |
| TKT-P2-15 | Spier & Mackay | `spierandmackay.com` |
| TKT-P2-16 | Proper Cloth | `propercloth.com` |

**Shared contract — every brand ticket.** PRD §7.6 lists these "subject to engineering validation of scrapeability," so each ticket's first deliverable is a finding, not code.

**Scope:** One `BrandScraper` implementation for the brand, with fixtures, conforming to TKT-P2-01 without widening the interface.

**Deliverables**

- `apps/api/src/scrapers/<brand>/scraper.py` implementing `BrandScraper`, with `<brand>/fixtures/` beside it — the per-brand package layout `FILE_STRUCTURE.md` specifies, not a flat `brands/<brand>.py`. Prefer the Shopify adapter (TKT-P2-06) or a public/affiliate API over HTML parsing (PRD §9.3).
- **A recorded investigation** in the PR description: does the product page carry the size chart, or is it behind a separate route or an XHR? Is JS rendering genuinely required, or does the server-rendered HTML contain the data? Are measurements in inches or cm, single-panel or doubled chest? What is the fallback size-chart URL (PRD §7.6 expects one per brand)?
- Five fixtures minimum in `apps/api/src/scrapers/<brand>/fixtures/`, each a frozen page plus its expected normalized chart, per CLAUDE.md. Pick products that differ — at least one with an incomplete chart and one non-shirt if the brand's URL shape allows it, so the error paths are covered by fixtures rather than only by unit tests.
- `size_chart_fallback_url` set, and `scraper_version` bumped whenever the extraction logic changes — `brand_product.scraper_version` exists so a bad scrape generation can be identified and re-run.
- **Playwright only if the investigation proves it necessary**, and then only imported inside that module, behind the `scrapers-js` extra. State in the PR what you tried before reaching for it. CLAUDE.md is explicit, and a browser on the 1.5 s path will not fit the budget.

**Acceptance**

- `uv run python -m scrapers.cli --brand <brand> --url <fixture URL>` prints a complete `BrandProduct` with a size chart.
- All five fixtures pass with no network access.
- Normalized chart keys are a subset of `dimension_weights`; values are cm.
- A product-not-found URL raises `ProductNotFoundError`; a product with no obtainable chart raises `SizeChartUnavailableError`.
- If the brand needs Playwright, `uv sync` without the extra still imports the registry without error — the module must degrade to a clear "extra not installed" message rather than an `ImportError` at registry build time.

**Refs:** PRD §7.6, §9.3, §7.5, §5.2; CLAUDE.md scrapers section and v1 scope rules.

---

## TKT-P2-17 — `scrapers.cli`

**Scope:** The command the exit criterion is written in terms of, plus the fixture recorder.

**Deliverables**

- `apps/api/src/scrapers/cli.py` supporting `--brand`, `--url`, `--record`, `--json`, and `--all` (run every brand against its fixtures, for the daily job).
- Exit codes that distinguish the outcomes: `0` success, `3` product-not-found, `4` size-chart-unavailable, `5` transport/robots refusal, `6` unknown brand. The scheduled job (TKT-P2-20) branches on these, so a single non-zero code would make "site changed" indistinguishable from "network blipped".
- Human-readable default output; `--json` for the job.

**Acceptance**

- `uv run python -m scrapers.cli --brand uniqlo --url <fixture>` exits 0 and prints a chart.
- Each error path returns its documented exit code, asserted by test.

**Refs:** `PROJECT_PLAN.md` Phase 2 exit criterion; CLAUDE.md command list.

---

## TKT-P2-18 — `brand_product` cache: write, read, staleness

**Scope:** Persisting scrape results and serving the cache-hit path PRD §9.2 depends on.

**Deliverables**

- `BrandProductRepository` gains `get_by_url(product_url)` and `upsert_from_scrape(ScrapedProduct)`. The `product_url` UNIQUE constraint makes the upsert the natural concurrency guard — two simultaneous scrapes of one URL must not raise; follow `seeds/garment_categories.py`'s `on_conflict_do_update` shape.
- `scraped_at` and `scraper_version` written on every upsert. A row whose `scraper_version` is older than the module's current version counts as stale even if `scraped_at` is fresh — that is how a scraper fix reaches already-cached products.
- Staleness read path: `brand_product_ttl_seconds` decides refresh. Serve the stale row **and** enqueue a refresh rather than blocking the request, except when there is no row at all.

**Acceptance**

- Test: two concurrent upserts of one URL both succeed and leave one row.
- Test: a row older than the TTL is reported stale; a row with an old `scraper_version` is stale regardless of age.
- Test: a cache hit performs **no** outbound request (assert with the socket guard from TKT-P2-00b).

**Refs:** PRD §9.2 (cache hit skips to step 6), §8; `apps/api/src/api/repositories/`.

---

## TKT-P2-19 — `arq` worker and the scrape job

**Scope:** The queue the on-demand path enqueues onto. CLAUDE.md forbids FastAPI `BackgroundTasks` for this — they die with the request worker, and a scrape outliving the request is the entire point.

**Deliverables**

- `apps/api/src/scrapers/worker.py` with an `arq` `WorkerSettings` and a `scrape_product(ctx, url)` job that resolves → discriminates → scrapes → upserts, returning the `brand_product` id.
- Idempotency by URL: enqueueing the same URL twice while one is in flight must not run two scrapes. `arq`'s `job_id` gives this for free — use the canonical URL hash as the id.
- Retry policy: transport errors retry with backoff; `ProductNotFoundError` and `SizeChartUnavailableError` are **terminal** and must not retry. Retrying a definite answer wastes the budget and hammers the retailer.
- A documented start command in CLAUDE.md, and the worker's Redis connection from `redis_url`.

**Acceptance**

- Integration test against the compose Redis: enqueue, run one worker cycle, assert a `brand_product` row exists.
- Test: the same URL enqueued twice yields one job.
- Test: a terminal error does not retry; a transport error does.

**Refs:** `PROJECT_PLAN.md` Phase 2; CLAUDE.md backend rules ("Use a proper queue… `arq` on Redis").

---

## TKT-P2-20 — Daily synthetic run and drift alerting

**Scope:** The thing that makes ten scrapers maintainable instead of ten liabilities. CI proves the parsers still work against frozen HTML; only a live run detects that a retailer changed their markup.

**Deliverables**

- `.github/workflows/scrapers-fixtures.yml` on every PR: `uv run pytest tests/scrapers` against frozen fixtures, no network.
- `.github/workflows/scrapers-daily.yml`, a **separate** scheduled workflow running `scrapers.cli --all --live` daily against the real fixture URLs, comparing each result to the stored `.expected.json`.
- On diff: page the on-call channel with the brand, the field that changed, and both values. A diff is a site change, not a test failure — the message should say which.
- The live job must not fail the build on a transport blip: distinguish exit code 5 (transport) from 3/4 (structural) per TKT-P2-17, retry once, and only alert on a second failure or a structural diff.

**Acceptance**

- The PR job passes with the network disabled.
- A deliberately altered fixture expectation causes the live job to alert with the offending field named.
- Three consecutive green scheduled runs — this is half of the phase exit criterion, so it gates the phase rather than any single ticket.

**Refs:** `PROJECT_PLAN.md` Phase 2 (exit criterion); CLAUDE.md scrapers section ("Daily synthetic test runs… to detect HTML drift early").

---

## TKT-P2-21 — `POST /recommend`: the on-demand path end to end

**Scope:** The endpoint PRD §9.2 budgets, minus push. Push dispatch and the mobile share extensions are Phase 6; this ticket returns the recommendation as JSON so Phase 6 has something to notify about.

**Deliverables**

- `POST /recommend` taking `{url}` plus the bearer token, and running §9.2's sequence: resolve (200 ms) → cache lookup → on miss, enqueue and await the scrape within `scrape_timeout_seconds` → fit profile (100 ms) → match (300 ms) → persist `recommendation` → return it.
- Every §7.5 failure mode as a distinct typed response, because the notification copy differs per case: unknown brand, product not found, size chart unavailable, insufficient closet data (fewer than 3 garments), and scrape timeout → "couldn't find this exact product".
- The await is bounded and the timeout is **not** an error the user sees as a failure: past 1.5 s the response is the manual-entry invitation, and the scrape keeps running in the worker so the next request for that URL hits cache.
- Rate limited like the other authenticated surfaces — add `/recommend` to the authenticated limiter's prefixes, and note that `api/rate_limit.py` matches on path boundaries, so a bare `/recommend` prefix is correct.
- `Cache-Control: no-store` is **not** needed here (no credential in the body), but the response is per-user; do not add any shared caching.

**Acceptance**

- Integration test per §7.5 failure mode, each asserting its own response shape.
- Test: a cache hit returns without enqueueing a job.
- Test: a scrape exceeding the timeout returns the manual-entry response, and the job still completes afterwards (assert the row appears).
- Test: a closet with two garments returns the insufficient-data response rather than a low-confidence recommendation.
- Latency test in the shape of `tests/test_prd_latency_budgets.py`: the cache-hit path stays inside §9.2's non-scrape budget.

**Refs:** PRD §9.2, §7.5, §7.3, §5.4; `apps/api/src/api/rate_limit.py`, `apps/api/tests/test_prd_latency_budgets.py`.

---

## TKT-P2-22 — Phase 2 exit-criterion integration test

**Scope:** The single test that proves the phase, in the shape of `tests/integration/test_phase1_exit.py`.

**Deliverables**

- `apps/api/tests/integration/test_phase2_exit.py` driving, for **every one of the ten brands**, the full path from a fixture URL through resolve → discriminate → scrape → normalize → upsert → match, asserting a complete `BrandProduct` with a size chart.
- Hand-recorded expectations, not values read back from the scrapers — the same rule `test_phase1_exit.py` states. If a chart changes, the test should fail with a number traceable to the fixture, not quietly agree with the new one.
- A parametrized shape so a failing brand names itself in the test id.

**Acceptance**

- Ten brands, ten passing cases, no network.
- `uv run pytest --cov=scrapers --cov-fail-under=90` — the domain floor for Phase 1 was 90% on `api.domain`; hold scrapers to the same bar, and wire it into the backend workflow beside the existing gate.

**Refs:** `PROJECT_PLAN.md` Phase 2 exit criterion; `apps/api/tests/integration/test_phase1_exit.py`.

---

## Suggested execution order

Three tracks. Track A is the shared spine every brand depends on; Track B is the ten brands, parallelizable across people once the spine lands; Track C is the request path, which needs the spine but not all ten brands.

- **Prerequisites (land first, single PR):** 00a → 00b → 00c
- **Track A (spine):** 01 → 02 → 03 → 04 → 06 → 17
- **Track B (brands):** 07 and 08 first, sequentially — they will expose what the spine is missing, and it is cheaper to fix `base.py` after two brands than after ten. Then 09 → 16 in parallel.
- **Track C (request path):** 05 → 18 → 19 → 21 (05 and 18 can start once 01 is merged; 21 needs 19 and at least one brand)
- **Gates:** 20 (needs Track B complete to be meaningful), then 22.

**Do not start Track B in parallel across ten people on day one.** The interface will change after the first two brands, and ten in-flight modules written against the pre-change `base.py` is the expensive way to discover that.
