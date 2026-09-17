"""Signing keys for the test suite.

One definition, imported everywhere, rather than the same literal pasted
into a ``monkeypatch.setenv`` call in each test module.

This started as ordinary duplication and turned into a real problem. Every
module that invented its own variant added a *new* secret-shaped literal to
the repository, and a secret scanner cannot tell a test signing key from a
production one — so each variant opened its own incident on push. Five
variants had accumulated, three of them introduced on this branch while
fixing unrelated bugs, which is exactly how this kind of thing spreads: each
author reasonably writes a memorable string for their own test, and nobody
sees the aggregate.

Nothing here is a credential. These keys sign tokens that exist only inside a
test process, they are identical in every checkout of this repository, and
they are never read by anything that talks to a real database or a real
client. The real ``JWT_SECRET`` lives in ``apps/api/.env``, which is
gitignored.

Import these rather than writing a new literal::

    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
"""

from __future__ import annotations

#: The key almost every test signs with.
TEST_JWT_SECRET = "test-secret-do-not-deploy-anywhere"

#: A second key, for the tests that assert a token signed with the *wrong*
#: key is rejected. Derived from the first rather than written out, so adding
#: this case costs no new secret-shaped literal.
#:
#: Length matters: pyjwt warns on HS256 keys under 32 bytes, which would
#: clutter test output without flagging a real bug. The derivation keeps it
#: comfortably over.
ALTERNATE_JWT_SECRET = f"{TEST_JWT_SECRET}-alternate"
