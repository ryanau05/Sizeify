"""HTTP helpers shared across the route tests.

Kept apart from ``_factories`` (which builds database rows) because these
drive the API the way a client does.
"""

from __future__ import annotations

from typing import Any

from _keys import TEST_PASSWORD
from httpx import AsyncClient

DEFAULT_CONSENT_AT = "2026-09-09T10:30:00Z"


def signup_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "email": "alice@example.com",
        "password": TEST_PASSWORD,
        "privacy_consent_accepted_at": DEFAULT_CONSENT_AT,
    }
    body.update(overrides)
    return body


async def signup_and_login(
    client: AsyncClient,
    email: str = "alice@example.com",
    password: str = TEST_PASSWORD,
) -> str:
    """Register and sign in, returning the access token.

    Two calls because ``POST /auth/signup`` deliberately returns no tokens:
    issuing a pair would say whether the account was new, which is the
    account-existence oracle the 202 exists to close. For a genuinely new
    account the login that follows always succeeds — the password was just
    set — so this is what a client's "sign up" button actually does.
    """
    registered = await client.post("/auth/signup", json=signup_body(email=email, password=password))
    assert registered.status_code == 202, registered.text

    signed_in = await client.post("/auth/login", json={"email": email, "password": password})
    assert signed_in.status_code == 200, signed_in.text
    return str(signed_in.json()["access_token"])


async def auth_header(
    client: AsyncClient,
    email: str = "alice@example.com",
    password: str = TEST_PASSWORD,
) -> dict[str, str]:
    return {"Authorization": f"Bearer {await signup_and_login(client, email, password)}"}
