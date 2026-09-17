# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions track the backend in `apps/api`; the mobile clients are versioned
separately once they ship.

## [0.2.0] - 2026-09-17

Phase 1: the backend can now hold a user's closet and recommend a size in a
brand it has never seen, end to end. Nothing is mocked — the exit-criterion
test signs up, enters five garments, records fit feedback, and gets back a
size with confidence, fit notes and the shirts that justified it.

### Added

- **Accounts.** Sign up with an email, password and explicit privacy consent;
  sign in for a 15-minute access token and a 30-day refresh token. Refresh
  tokens are single-use — presenting one twice signs the account out
  everywhere, on the assumption the token was stolen.
- **A closet.** Add, list, edit and remove owned garments, each with
  per-dimension measurements in centimetres carrying their own provenance, so
  a future camera-assisted flow can supply them without a wire-format change.
  Removal is a tombstone: a deleted garment stops shaping recommendations but
  its history survives.
- **Fit feedback.** Record how a garment actually fits — too tight through the
  chest, slightly short in the sleeve — per dimension and optionally per use
  case, so "for work" and "for the gym" can want different things.
- **A fit profile.** `GET /closet/fit-profile` shows what the closet says you
  prefer on every dimension, how confident it is, and how that changes by use
  case. An empty closet says so plainly rather than inventing a profile from
  the onboarding answer.
- **Size recommendations.** Matching against a brand's real size chart,
  weighted by which dimensions matter, adjusted for how much the fabric gives.
  Below 60% confidence you get two sizes and the trade-off between them rather
  than one false certainty. Every recommendation carries all four components
  the spec requires: size, confidence, fit notes, and the reference garments
  behind it.
- **Your data, on request.** `GET /me/export` returns everything held about
  you in one document; `DELETE /me` erases the account and everything
  user-owned with it, after re-entering your password. Both are GDPR/CCPA
  requirements and both work from day one.
- **Rate limiting** on the auth endpoints and on the authenticated surfaces,
  so neither password guessing nor closet writes run unbounded.
- **Structured JSON logs** for security-relevant events, carrying user ids
  only — never emails, tokens, or token hashes.

### Changed

- Signup returns `202 Accepted` with no tokens and the same body whether or
  not the address is already registered, so it cannot be used to discover who
  has an account. Clients follow with `/auth/login`.
- Deleting an account requires re-entering the current password; a stolen
  access token alone is not enough to erase someone's data.
- Passwords are re-hashed on login when the cost parameters have been raised,
  so existing accounts benefit from re-tuning without a reset flow.

### Fixed

Two pre-landing reviews ran before this release. The first closed 18 findings;
the second found six defects the first missed, each now covered by a test
confirmed to fail against the pre-fix code:

- **The database schema could not be created from scratch.** Migration `0003`
  bound a text value into a timestamp column, so `alembic upgrade head` failed
  on any database that did not already have the schema. Every test ran against
  a database that did, so the entire suite passed while deployment was broken.
  Migrations now have their own tests that drive the real migration tool
  against a throwaway database.
- **The authenticated rate limiter could be bypassed by anyone.** It counted
  requests against the token string as sent, without checking it, so varying
  the token on each request got a full fresh allowance every time — no account
  needed. It now counts against the verified account, falling back to the
  network address when a token is not genuine.
- **Recommendations could cite the wrong garment.** When a recommendation was
  conditioned on a use case, the reference garments were compared against the
  unconditioned profile, so the shirt shown as the reason could be the wrong
  one.
- Fit signals had no database index on the column they are looked up by,
  making profile construction and account deletion scan the whole table.
- The rate limiter's memory could grow without bound, and its cleanup ran a
  full scan on every new entry.
- Unknown fields in auth requests were silently discarded rather than
  rejected, so a mistyped fit-preference field vanished without error.
- `X-Forwarded-For` was only partially read, which behind some proxies let a
  client choose the address it was billed as.

### Infrastructure

- Migrations `0001`–`0006`, each reversible, with destructive downgrades
  refusing to run without an explicit opt-in.
- CI enforces the repository-layer boundary at lint time — route handlers
  cannot reach the database directly — plus a 90% coverage floor on the domain
  core, which currently sits at 99%.
- 514 tests covering the whole surface, including an end-to-end test of the
  Phase 1 exit criterion whose expected numbers are worked out by hand rather
  than read back from the code.

[0.2.0]: https://github.com/ryanau05/Sizeify/releases/tag/v0.2.0
