"""Authentication primitives.

* ``password`` — Argon2id hash / verify (TKT-P1-05).
* ``jwt`` — access + refresh token issuance, validation, rotation
  (TKT-P1-06).

Route handlers wire these up via ``api.deps`` and ``api.routes.auth``;
nothing in this package touches the DB directly.
"""
