# TODOS

## Completed

**All 18 pre-landing-review findings are closed** (17 on 2026-09-11, the last
on 2026-09-17). Nothing open.

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

- **Per-user rate limiting** — a second limiter covers `/closet/*` and `/me`, keyed on a hash of the bearer credential rather than the address, so two users behind one NAT do not share a budget and one user cannot escape theirs by changing networks. The key never contains the raw token.
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
