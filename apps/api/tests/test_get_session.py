"""Acceptance test for TKT-P1-00b.

Mounts a throwaway route that depends on ``get_session`` and round-trips
``SELECT 1`` against the running Postgres (see ``infra/docker-compose.yml``).
The test fails if compose Postgres isn't reachable — that's intentional and
matches the ticket's acceptance criterion.

A real per-test transactional fixture lands in TKT-P1-00e; this file just
proves the dependency wiring.
"""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session


def build_probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/_probe/select-1")
    async def probe(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> dict[str, int]:
        result = await session.execute(text("SELECT 1"))
        return {"value": result.scalar_one()}

    return app


@pytest.mark.asyncio
async def test_get_session_round_trips_select_1() -> None:
    app = build_probe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_probe/select-1")

    assert response.status_code == 200
    assert response.json() == {"value": 1}
