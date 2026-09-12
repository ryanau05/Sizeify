# TODOS

Carry-over from the Phase 1 pre-landing review (2026-09-10). Ten findings were
fixed in that pass; these are the ones that were found, verified, and
deliberately left. Each was reproduced against the running app or the dev
database, so none of them are speculative.

## Backend — must close before the API takes real traffic

### Cache `get_settings()`

**What:** Add `@lru_cache(maxsize=1)` to `api.config.get_settings`, resolve `env_file` to an absolute path, and switch the tests that rely on re-reading to `get_settings.cache_clear()`.

**Why:** It is uncached today, so `pydantic-settings` re-reads and re-parses `.env` from disk on every call. `auth.jwt._secret()` calls it on every token encode and every decode, which puts blocking synchronous file I/O on the event loop for every authenticated request. Measured: 0.548 ms/call.

**Context:** `repositories.base.get_engine` and `get_sessionmaker` are already `@lru_cache`d, so the DB URL freezes at first call while the JWT secret and TTLs can shift mid-process. The blocker is that `tests/test_routes_auth.py` and `test_auth_jwt.py` monkeypatch `JWT_SECRET` and depend on the re-read; they need a `cache_clear()` fixture first. Also validate at `create_app()` that `jwt_secret` is non-empty outside dev, rather than discovering it as a RuntimeError → 500 on the first login.

**Effort:** S
**Priority:** P0
**Depends on:** None

### Trusted-proxy handling in the rate limiter

**What:** Add a `trusted_proxy_cidrs` setting (empty by default) and parse the rightmost untrusted `X-Forwarded-For` hop when the peer is inside it.

**Why:** The limiter keys on the TCP peer address. Behind any load balancer or ingress that is the proxy, so the whole deployment shares one bucket and a single attacker spends the entire `/auth/login` budget, 429-ing every real user. The failure mode is inverted, not degraded: protection becomes a denial of service.

**Context:** `rate_limit.py::_key` ignores `X-Forwarded-For` deliberately and correctly, since honouring it without an allowlist makes the limiter opt-out via one spoofed header. The module docstring defers the allowlist to Phase 8 alongside the Redis backend. That is the wrong gate: this must land before the API first sits behind a proxy, whenever that happens. `scope["client"]` can also be `None` on some ASGI servers, collapsing everyone into the literal key `"unknown"`.

**Effort:** M
**Priority:** P0
**Depends on:** None

### Structured logging

**What:** Add `logging.getLogger(__name__)` and a structured handler wired in `create_app`, starting with the refresh-replay branch and repeated 401s.

**Why:** There is no logging anywhere in `src/api`. A refresh-token replay revokes a user's entire chain and nothing is ever emitted, so no operator can learn that a token was probably stolen. Failed logins and 401s are equally silent.

**Context:** The code already anticipates this: `ReusedRefreshTokenError` carries `self.user_id` "for incident-response logging", and `refresh_tokens.revoke_all_for_user` notes that blast-radius logging is wanted. `FILE_STRUCTURE.md` reserves `src/api/logging.py` for it. Log the user id only, never the token or its hash.

**Effort:** S
**Priority:** P0
**Depends on:** None

### Re-authentication on `DELETE /me`

**What:** Require the current password in the request body (verified off-thread) or a freshly minted token, and consider a short grace period before the cascade fires.

**Why:** Account erasure is irreversible and cascades to `refresh_token`, `owned_garment`, `fit_signal` and `recommendation`. A leaked or borrowed 15-minute access token is currently enough to destroy everything a user owns. The same token also fetches the full GDPR dump from `GET /me/export`.

**Context:** `routes/me.py::delete_me`. These are the two highest-consequence operations in the API and neither is stepped up.

**Effort:** S
**Priority:** P0
**Depends on:** None

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

See `docs/PHASE_1_TICKETS.md` for the 20 Phase 1 tickets, and the
"Pre-landing review" entry in `docs/PROJECT_PLAN.md` for the ten findings
fixed on 2026-09-10.
