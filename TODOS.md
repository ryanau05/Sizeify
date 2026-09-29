# TODOS

## Open

Three items, all of which need something that does not exist yet. Everything
else from the three Phase 1 reviews is closed — see Completed below.

- **`GET /me/export` still buffers the whole document.** The reads are paged
  now, so the event loop gets a yield point every few milliseconds instead of
  one after half a second (measured: 486 ms uninterrupted at the ceiling the
  caps allow). Peak memory is unchanged and still proportional to the account,
  because the response is one JSON object and every row has to be in it.
  Bounding *that* means streaming the body, which needs a database session
  that outlives the handler — and routes are barred from holding one
  (`routes/ruff.toml`), so it belongs in a `services/export.py` alongside the
  session-lifetime care a `StreamingResponse` generator needs. Worth doing
  when an account can plausibly get large; not before.
- **Refresh-token retention has no scheduler.** The delete path exists and is
  tested (`RefreshTokenRepository.delete_expired`), and
  `python -m api.maintenance.prune_refresh_tokens` runs it today from cron.
  What is missing is the job queue that should own it — `arq` on Redis, which
  CLAUDE.md puts in Phase 8 alongside the Redis-backed rate limiter. The
  entry point is written so that step is a matter of calling `prune()` from a
  worker.
- **Three of PRD §9.2's five budgets are untimed,** because the code they
  budget does not exist: resolve is Phase 5, the scraper is Phase 2, push is
  Phase 7, and the end-to-end p50 < 3 s / p95 < 5 s needs the share-sheet
  endpoint from Phase 6. The two this repository owns are now asserted in
  `tests/test_prd_latency_budgets.py` — profile build at 126x headroom under
  its 100 ms line, matching at ~11,000x under its 300 ms one. PRD §10.1's
  "<4 minutes to first garment" is a UX measurement over a human using the
  mobile client and is not observable from the backend at all.

## Completed

**All 18 findings from the first pre-landing review are closed** (17 on
2026-09-11, the last on 2026-09-17), and **17 of the 20 items the second
and third reviews left open** were closed on 2026-09-29.

### Closed 2026-09-29

Ordered as they were worked, by importance:

- **Login no longer pins a pooled connection across argon2.** The read
  autobegins a transaction and SQLAlchemy holds the connection until it ends,
  so the ~75-250 ms hash ran with one checked out; the pool is 5 + 10, so ~15
  concurrent logins exhausted it and everything else queued behind work that
  was not using its connection. The lookup commits before hashing — verified
  to return the connection, checked-out 1 -> 0 — and it has to be a commit
  rather than a rollback or the test harness's SAVEPOINT would take the
  fixture rows with it.
- **`GET /me/export` pages its reads** (the remaining half is above).
- **`Cache-Control: no-store` on both token endpoints and the export** (RFC
  6749 §5.1). Not on the 401 path: FastAPI builds a fresh response for a
  raised `HTTPException` and discards what a dependency wrote, which is
  acceptable because a 401 carries no token.
- **Limiter prefixes match path boundaries,** so a future `/metrics` or
  `/members` is no longer swept into the authenticated bucket by `"/me"`.
- **Bucket keys are normalized,** so one IPv6 host cannot mint a bucket per
  spelling.
- **`confidence` ships as a JSON number,** not a string beside
  `magnitude_cm`'s number in the same document.
- **The PATCH explicit-null 422 names its field** in `loc`, one entry per
  field, via a field validator rather than a model one.
- **One error-code vocabulary per endpoint** — the hand-built codes are
  snake_case like Pydantic's. Nothing had asserted on them, which is why
  changing all three broke no test; there is a test now.
- **`SignupAccepted.detail` is a defaulted `str`,** not a one-member enum
  pinning the exact sentence in every generated client.
- **`alembic upgrade --sql` works for the whole chain.** 0004 and 0005 emit
  their data guards *as SQL* rather than skipping them, so the safety check
  survives into the generated script — verified by rendering 0005 against a
  database holding two case-variant addresses and watching psql refuse with
  the migration's own wording.
- **`user_email_key` is gone** (migration 0007). `uq_user_email_lower`
  subsumed it, and keeping both cost two unique checks per signup and left
  the `IntegrityError` handler guessing which name would fire.
- **Migration 0003 sets `lock_timeout`,** so a blocked `SET NOT NULL` fails
  while someone is watching instead of queueing every reader behind it.
- **0006 records the `CONCURRENTLY` rule** where the next index author will
  read it, including that a failed build leaves an INVALID index.
- **The closet ceiling is a per-user critical section.** Count and insert are
  in one transaction behind a `SELECT ... FOR UPDATE` on the user row;
  before, two concurrent creates both read the same pre-count and both
  proceeded.
- **`_weights_for` dispatches on `category_id`** instead of ignoring its
  argument, and an untuned category raises rather than silently ranking
  against button-down priorities.
- **`FitSignalRepository.list_for_user` has paging and ordering tests** — it
  is what the export now pages through, so an off-by-one would truncate a
  compliance response.
- **`_signal_for`'s latest-wins rule is documented,** with why it is right
  and where it would change.
- **A docstring example no longer uses `too_long`,** which is not a `Verdict`.

Each fix has a regression test confirmed to fail against the pre-fix code.

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
