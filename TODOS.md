# TODOS

Carry-over from the Phase 1 pre-landing review (2026-09-10). Ten findings were
fixed in that pass; these are the ones that were found, verified, and
deliberately left. Each was reproduced against the running app or the dev
database, so none of them are speculative.

## Backend — correctness and consistency

### Wire use-case conditioning into matching

**What:** Have `match()` select a `use_case_variant` from the profile, and have `recommend()` record which one it assumed.

**Why:** PRD §6.4 requires the engine to condition on intent and to note the use case it assumed. `FitProfile.use_case_variants` is built (TKT-P1-12), serialized over the wire, and consumed by nothing: `matching.match()` reads only `fit_profile.dimensions`, and `use_case_assumed` is `None` on every persisted row. The feature looks live and is not.

**Context:** `domain/matching.py:86` and `domain/recommendation.py`. `RecommendationRepository.create_from` already accepts `use_case_assumed` as a parameter for this. The selection signal (URL hints, most common use case) is PRD §6.4 and lands with the share-sheet flow in Phase 6.

**Effort:** M
**Priority:** P1
**Depends on:** None

### Make `transaction()` re-entrant

**What:** Use `session.begin_nested()` when a transaction is already active, and move `await session.commit()` inside the `try`.

**Why:** The helper was changed during TKT-P1-07 to rely on SQLAlchemy autobegin. That made it non-re-entrant: a nested `async with transaction(s)` commits and ends the outer transaction at the inner block's exit, so an outer failure rolls back only the tail while the inner work stays permanently committed. `session.begin()` raised loudly on this; autobegin is silent.

**Context:** `repositories/base.py::transaction`. Nothing nests today, so this is latent. The risk is Phase 6's share-sheet path, which composes profile build, recommendation persist, and push dispatch. The commit also sits outside the `try`, so a commit-time failure relies on `deps.get_session`'s rollback two layers up. Add a test that nests two blocks and asserts the inner write rolls back.

**Effort:** S
**Priority:** P1
**Depends on:** None

### Deterministic tie-breaking in matching

**What:** Append `size_label` as a final sort key in `match()`, and `garment_id` in `_reference_garments_for`.

**Why:** Ties currently resolve to `size_chart` dict insertion order. Once charts are loaded from the `brand_product` JSONB column, key order is whatever Postgres chose (jsonb sorts by length then bytewise), so the recommended size can change between a fresh scrape and a round-tripped one. `distance` and `residual` are both rounded to 4 places, which makes exact ties more likely than raw floats would.

**Context:** `domain/matching.py:120`. `recommendation.py` already guards the analogous no-dimensions case for exactly this reason. Add a test that shuffles `size_chart` key order and asserts an identical `Recommendation`.

**Effort:** S
**Priority:** P1
**Depends on:** None

### Rescale the domain unit tests to the agreed chest convention

**What:** Convert the 105–107 cm chest values in `test_matching.py` and `test_recommendation.py` to the un-doubled ~54 cm scale.

**Why:** The review settled that chest is pit-to-pit **un-doubled** (PRD §5.2's 35–80 range, which the validator and the closet API enforce). These tests predate that and use the doubled scale, so they now contradict the convention they are meant to exercise. They pass because they are internally consistent, which is exactly what makes them misleading.

**Context:** Left out of the review pass because rescaling means recomputing every hand-derived expected value, which deserves its own focused change rather than riding along with bug fixes. `tests/_factories.py` and all closet API tests already use 54.

**Effort:** M
**Priority:** P1
**Depends on:** None

### Per-user rate limiting beyond `/auth/*`

**What:** Add a second limiter instance keyed on the authenticated user id for the closet and `/me` routers, plus a cap on closet size.

**Why:** Only `/auth/*` is throttled. `POST /closet/garments` is an unbounded row-creation primitive for any valid token; `GET /closet/fit-profile` recomputes the whole profile from an unbounded read on every call; `GET /me/export` runs three unbounded queries by design. One compromised account can drive all of them at full rate.

**Context:** `main.py` mounts the limiter with `path_prefix="/auth/"`. `TokenBucketLimiter` already takes an opaque key, so this is a second middleware instance rather than new machinery.

**Effort:** M
**Priority:** P2
**Depends on:** None

### Request-validation consistency

**What:** Add `model_config = ConfigDict(extra="forbid")` to `OwnedGarmentCreate` and `FitSignalCreate`.

**Why:** POST silently discards unknown fields while PATCH rejects them with a 422. A client sending `source` to the fit-signal endpoint, the one field the route deliberately refuses to let clients set, gets a 201 and never learns it was ignored.

**Context:** `schemas/closet.py`. `OwnedGarmentUpdate` and `MeasurementValue` already forbid extras, so the strictness is inconsistent even within a single request body.

**Effort:** S
**Priority:** P2
**Depends on:** None

### Document the error responses in OpenAPI

**What:** Add `responses={409: ...}` to signup, `{401: ...}` to login and refresh, a shared `RATE_LIMITED_RESPONSE` (429, documenting `Retry-After`) across all `/auth/*`, and a typed `ErrorDetail` body schema on the existing 401/404 entries.

**Why:** The generated document lists only `201/200` and `422` for the auth routes, so a generated client has no branch for the 401, 409, or 429 those endpoints actually return, and will not read `Retry-After`.

**Context:** `routes/auth.py`. `UNAUTHORIZED_RESPONSE` and `_NOT_FOUND_RESPONSE` in `deps.py` and `routes/closet.py` show the pattern but carry a description with no `content` schema.

**Effort:** S
**Priority:** P2
**Depends on:** None

### Guard the destructive migration downgrades

**What:** Make `0003`'s downgrade refuse to run without an explicit flag, and have `0004`'s either hard-delete tombstoned rows or refuse when any exist.

**Why:** `0003.downgrade()` drops `password_hash` and `privacy_consent_accepted_at`, destroying every credential and every consent record; re-upgrading then backfills the unauthenticatable sentinel for all of them, locking out 100% of accounts. `0004.downgrade()` drops `deleted_at`, so a downgrade/re-upgrade cycle resurrects every soft-deleted garment as a live closet row.

**Context:** Both cycle cleanly today, so nothing stops someone running them. Harmless pre-launch; the point is to fix it while that is still true.

**Effort:** S
**Priority:** P2
**Depends on:** None

### Mark or remove the fabricated consent timestamps

**What:** Backfill an obviously synthetic sentinel (for example `-infinity`) instead of `created_at`, or delete the pre-auth rows outright.

**Why:** Migration `0003` manufactures a consent record for rows that never gave consent, and the result is indistinguishable downstream from a real one. `GET /me/export` presents it to the user as a genuine consent timestamp, and `schemas/me.py` explicitly frames it as "the consent record we are relying on".

**Context:** The migration's own docstring acknowledges these are dev fixtures rather than real consent records. Nothing in the data says so.

**Effort:** S
**Priority:** P2
**Depends on:** None

### Reconsider `device_push_token` in the GDPR export

**What:** Either include it in `UserExport`, or include a redacted presence indicator and record the legal basis for the omission in the privacy policy.

**Why:** A device identifier the controller stores against a named user is personal data under GDPR Art. 15 regardless of whether it is portable. The current exclusion rests on it being "meaningless outside our own push pipeline", which is a different test from the one the regulation applies.

**Context:** `schemas/me.py::UserExport`. Excluding `password_hash` and the refresh chain is on firmer ground; this one is not on the same footing.

**Effort:** S
**Priority:** P2
**Depends on:** None

### Password rehash on login

**What:** Check `check_needs_rehash` after a successful verify and re-hash in the same transaction.

**Why:** `auth/password.py`'s whole premise is that `MEMORY_COST_KIB` will be re-measured and raised on production hardware. When that happens every existing user keeps their weaker hash forever, with no upgrade path short of a password reset.

**Context:** The same gap means migration `0003`'s `LOCKED_PASSWORD_HASH` sentinel rows are only ever detected as generic verify failures.

**Effort:** S
**Priority:** P3
**Depends on:** None

### Signup account-existence oracle

**What:** Return 202 with an identical body whether or not the address is taken, and deliver the outcome by email.

**Why:** Signup returns 409 with "An account with that email already exists", which is an existence oracle. Login is carefully defended against exactly this (a dummy hash equalizes the timing), so signup undercuts that work.

**Context:** `routes/auth.py`. The module docstring names the rate limiter as the mitigation, but it is per-IP at 10/min, which a distributed prober outruns. Needs an email pipeline, hence the lower priority.

**Effort:** M
**Priority:** P3
**Depends on:** Transactional email

## Infrastructure

### Add a fifth place to the dimension-update rule

**What:** Update CLAUDE.md's "update **all four**" rule to name `_DIM_LABEL` in `domain/recommendation.py`, and add a test pinning it against the category schema.

**Why:** Adding a measurement dimension now requires five edits, not four. A missing label degrades silently to a title-cased identifier ("Cuff Circumference" instead of "Cuff") in the push notification the user reads.

**Context:** `_DIM_LABEL` has a `.get()` fallback, so nothing fails; the text just gets worse. `test_seed_garment_categories.py` already pins the schema against `dimension_weights` and is the natural home.

**Effort:** S
**Priority:** P3
**Depends on:** None

### Clear the retired demo rows from the dev database

**What:** `docker exec sizeify-postgres psql -U sizeify -d sizeify -c "delete from \"user\" where email='demo@sizeify.app'"`

**Why:** Five `owned_garment` rows survive from the retired capstone demo track. They are in the pre-Phase-1 flat measurement shape *and* on the doubled chest scale, so they are invalid twice over under the agreed convention. They now raise `MalformedMeasurementError` on the fit-profile path, which is the correct new behaviour but noisy locally.

**Context:** Deliberately not done during the review because it deletes data from a developer's database. No forward migration was written for them: migrating the doubled values forward would produce rows that violate three of the category's own bounds.

**Effort:** S
**Priority:** P3
**Depends on:** None

## Completed

**2026-09-11 — all four P0s closed.**

- **Cache `get_settings()`** — `@lru_cache(maxsize=1)`, `env_file` anchored to an absolute path so the loaded values no longer depend on the process CWD. 0.548 ms/call of blocking disk I/O per JWT operation is now 0.00003 ms. An autouse fixture in `tests/conftest.py` clears the cache around every test, and each `_jwt_secret` fixture clears it after `setenv`.
- **Trusted-proxy handling** — new `trusted_proxy_cidrs` setting (empty by default, so nothing is trusted). When the peer is inside a configured network, `X-Forwarded-For` is walked right-to-left, skipping trusted hops, and the first untrusted address is billed. Client-forged left-hand entries are ignored; an unparseable CIDR is logged and does not widen trust.
- **Structured logging** — `src/api/logging.py` emits one JSON object per event with a stable dot-separated event name. Wired at `auth.login.failed`, `auth.refresh.replayed`, `auth.token.unknown_subject`, `me.account.erased` and `me.account.erase_denied`. No emails, tokens or token hashes are ever logged; `user_id` only.
- **Re-authentication on `DELETE /me`** — the request body now carries the current password, verified off the event loop. Note for the mobile clients: httpx's convenience `.delete()` takes no body, so the tests go through `client.request`. DELETE-with-a-body is legal and FastAPI serves it, but not every HTTP library exposes it on the convenience method.

See `docs/PHASE_1_TICKETS.md` for the 20 Phase 1 tickets, and the
"Pre-landing review" entry in `docs/PROJECT_PLAN.md` for the ten findings
fixed on 2026-09-10.
