# TODOS

## Open

A second, specialist pre-landing review ran before the Phase 1 PR. Six defects
it found were fixed on the branch (see `docs/PROJECT_PLAN.md`); the items below
were judged not worth blocking the PR for. None is reachable by an honest v1
client at v1 scale — that is the standard used to defer them.

### P2 — bound before real traffic

- **Fit-signal creation is uncapped, and `use_case` is free text.** Garments
  have `max_closet_garments`; signals have no ceiling. `build_fit_profile`
  rebuilds the per-dimension posterior once per distinct `use_case`, so one
  garment carrying thousands of distinct use-case strings pushes the build past
  PRD §9.2's 100 ms budget. Measured on this hardware: a realistic 5-garment
  closet is 0.2 ms, 50 garments x 50 tagged signals is 14.6 ms, and a single
  garment with 2,000 distinct use cases is 314 ms. Needs a per-garment signal
  cap and a bound on how many variants get built.
- **`GET /me/export` is unbounded by design.** `limit=None` on all three
  queries is correct for Art. 15 — an export that stopped at 100 rows would be
  a compliance bug — but signals and recommendations have no ceiling, and every
  row is materialized into Pydantic models in one synchronous stretch. Page it
  internally and stream the response before any account gets large.
- **`POST /auth/login` holds a DB connection across argon2.** The lookup
  autobegins the transaction, which then stays open across the ~75-250 ms
  verify and any rehash. The default pool is 5 + 10 overflow, so ~15 concurrent
  logins queue everything else behind connection checkout. `signup` already
  avoids this deliberately; `login` should do the verify before opening the
  block.

### P3 — correctness of contract, not of behaviour

- **`RecommendationExport.confidence` is a `Decimal`,** which Pydantic v2
  serializes as a JSON *string*, while `magnitude_cm` on the same document is a
  `float` and ships as a number. `GET /me/export` emits `"confidence": "0.850"`
  next to `"magnitude_cm": 1.5`.
- **The PATCH explicit-null 422 does not name the field in `loc`.** A
  `mode="before"` model validator cannot attach a field location, so the name
  survives only inside the prose message and the client cannot highlight the
  input.
- **Two error-code vocabularies on one endpoint.** Hand-built 422s use
  Pydantic-v1 dotted strings (`value_error.measurement.<kind>`); FastAPI's own
  use v2 snake_case and carry extra keys. A client switching on `type` has to
  learn both.
- **`SignupAccepted.detail` is a single-valued `Literal`,** so it publishes as
  a one-member enum and generated clients pin the exact sentence — making a
  reword a breaking schema change. A plain `str` with a default gives the same
  fixed-body guarantee.
- **`alembic upgrade --sql` is broken for the whole chain.** `0005`'s duplicate
  check and `0004`'s tombstone check call `op.get_bind().execute(...)`, which
  returns `None` in offline mode, so both die with an `AttributeError`. Rules
  out the "generate SQL, have a DBA apply it" path.
- **`uq_user_email_lower` subsumes `user_email_key`.** If `lower(email)` is
  unique then `email` is too. Harmless at v1 scale, but it doubles unique-check
  work on signup and leaves two constraint names an `IntegrityError` handler
  can see.
- **`_weights_for(fit_profile)` never reads its argument** — it returns the
  module constant unconditionally. Either key on `category_id` or drop the
  parameter.
- **`FitSignalRepository.list_for_user`'s `limit` is untested,** and its
  `ORDER BY created_at, id` contract is unasserted. Its two sibling
  repositories both got paging tests on this branch.

### P4 — when there is production data

- **Migration `0003` takes ACCESS EXCLUSIVE with no `lock_timeout`,** after
  holding row locks from two whole-table UPDATEs — the classic deadlock shape
  against a live app transaction. Irrelevant on an empty table.
- **Both new indexes build non-concurrently.** Correct now (the tables are
  trivially small, and CONCURRENTLY cannot run inside Alembic's transactional
  DDL). The first index added after there is real data needs
  `postgresql_concurrently=True` inside an `autocommit_block()`.
- **No test times the PRD budgets.** §10.1's "<4 min to first garment" and
  §9.2's share-sheet p50/p95 are unmeasured; the per-step budgets are only
  ever checked by reading the code.

## Completed

**All 18 findings from the first pre-landing review are closed** (17 on
2026-09-11, the last on 2026-09-17).

### P0

- **Cache `get_settings()`** — `@lru_cache(maxsize=1)`, `env_file` anchored to an absolute path so the loaded values no longer depend on the process CWD. 0.548 ms/call of blocking disk I/O per JWT operation became 0.00003 ms. An autouse fixture in `tests/conftest.py` clears the cache around every test.
- **Trusted-proxy handling** — new `trusted_proxy_cidrs` setting, empty by default. When the peer is inside a configured network, `X-Forwarded-For` is walked right-to-left, skipping trusted hops, and the first untrusted address is billed. Client-forged left-hand entries are ignored; an unparseable CIDR is logged and does not widen trust.
- **Structured logging** — `src/api/logging.py` emits one JSON object per event with a stable dot-separated event name. Wired at `auth.login.failed`, `auth.refresh.replayed`, `auth.token.unknown_subject`, `auth.password.rehashed`, `me.account.erased` and `me.account.erase_denied`. `user_id` only: no emails, tokens or token hashes.
- **Re-authentication on `DELETE /me`** — the body carries the current password, verified off the event loop. Note for the mobile clients: httpx's convenience `.delete()` takes no body, so the tests go through `client.request`. DELETE-with-a-body is legal and FastAPI serves it, but not every HTTP library exposes it on the convenience method.

### P1

- **Use-case conditioning wired through** — `match()` and `recommend()` take a `use_case`, `FitProfile.dimensions_for()` selects the variant, and `default_use_case()` implements PRD §6.4's "most common use case" fallback. `Recommendation.use_case_assumed` states what it conditioned on and `create_from` records it by default. A hint with no variant conditions nothing and says so.
- **`transaction()` is re-entrant** — an inner block opens a SAVEPOINT and only the outermost exit commits. Depth is tracked on `session.info`, not inferred from `in_transaction()`, which autobegin makes true as soon as a handler reads anything.
- **Deterministic tie-breaking** — `match()` breaks exact ties on the size's own summed measurements, then the label. The reference-garment sort was left alone on purpose: its input is already deterministic (closet order, oldest first).
- **Domain tests rescaled** — chest values shifted to the agreed un-doubled scale, chosen so every delta is preserved and no hand-computed expectation needed re-deriving.

### P2

- **Per-user rate limiting** — a second limiter covers `/closet/*` and `/me`, keyed on the caller's identity rather than the address, so two users behind one NAT do not share a budget and one user cannot escape theirs by changing networks. It first shipped keying on a hash of the *unverified* bearer header, which let any caller mint a fresh full bucket per request by varying the token; the second review caught that and it now keys on the verified JWT `sub`, falling back to the peer address when the token does not decode. The key never contains the raw token.
- **Closet size cap** — `max_closet_garments` (500, far above PRD §10.1's 3-5) so `POST /closet/garments` is not an unbounded row-creation primitive.
- **Request-validation consistency** — `extra="forbid"` on `OwnedGarmentCreate` and `FitSignalCreate`. Create used to drop unknown fields silently while PATCH rejected them; a body carrying `source` is now a 422 naming it rather than a 201 that quietly ignored it.
- **OpenAPI error responses** — 409 on signup, 401 on login and refresh, 429 across all `/auth/*` documenting `Retry-After`, and a typed `ErrorDetail` body on every declared error. Previously the document listed only the success code and 422.
- **Destructive downgrades guarded** — `0003` refuses without `ALEMBIC_ALLOW_DESTRUCTIVE_DOWNGRADE=1` (it destroys every credential and consent record, and re-upgrading locks out 100% of accounts with no reset flow to recover through). `0004` refuses while any soft-deleted garment exists, since dropping `deleted_at` resurrects them as live closet rows.
- **Fabricated consent marked** — `0003` backfills a deliberately impossible `1970-01-01` sentinel instead of `created_at`, so a manufactured consent record is greppable rather than indistinguishable from a real one in the Art. 15 export.
- **`device_push_token` in the export** — included. A device identifier stored against a named user is personal data under Art. 15 whether or not it is portable; "meaningless outside our push pipeline" was true and was not the test the regulation applies.

### P3

- **Signup no longer reveals whether an address is registered** — `POST /auth/signup` returns 202 with a fixed body either way, and no token pair. Issuing tokens and being non-enumerable are mutually exclusive: a pair can only exist for an account just created, so its presence *is* the answer. The client calls `/auth/login` next, which for a genuinely new account always succeeds. Verified: identical status, byte-identical body, identical headers, and median latency within 1.6% (argon2 runs on both paths before the lookup, so it dominates). This was originally deferred as needing transactional email; it does not — email only improves the case where a user who forgot they had an account signs up again and is not told, which remains the one rough edge.
- **Password rehash on login** — `needs_rehash` plus an upgrade in the login transaction, so re-calibrating `MEMORY_COST_KIB` reaches accounts that already exist. Returns `False` for unparseable hashes, migration `0003`'s locked sentinel among them.
- **Fifth place in the dimension rule** — CLAUDE.md now says "all five" and names `_DIM_LABEL`; `test_seed_garment_categories.py` pins it against the schema. A missing label degrades the push-notification wording rather than failing, which is exactly the silent degradation the rule exists to prevent.
- **Retired demo rows cleared** — the five `owned_garment` rows and the `demo@sizeify.app` user left over from the retired capstone track are gone from the dev database. They were in the pre-Phase-1 flat measurement shape *and* on the doubled chest scale, invalid twice over under the agreed convention.

See `docs/PHASE_1_TICKETS.md` for the 20 Phase 1 tickets, and the
"Pre-landing review" entry in `docs/PROJECT_PLAN.md` for the ten findings
fixed on 2026-09-10.
