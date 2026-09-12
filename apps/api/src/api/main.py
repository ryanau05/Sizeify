"""FastAPI app factory.

``create_app`` builds a fully-wired application: middleware, then routers.
It is called once at import for the ASGI server (``app`` below) and once
per test by the ``app`` fixture — which is why the rate limiter is
constructed here rather than held as a module global. A per-app limiter
means one test's exhausted bucket cannot leak into the next.
"""

from fastapi import FastAPI

from api.config import get_settings
from api.logging import configure_logging
from api.rate_limit import RateLimitMiddleware, TokenBucketLimiter
from api.routes import auth, closet, health, me


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    app = FastAPI(title="Sizeify API")

    # Guards the only unauthenticated write surface in the app. Installed
    # app-wide and scoped by path prefix inside the middleware, so
    # ``/auth/*`` endpoints added later are covered automatically while
    # everything else short-circuits on a prefix check.
    app.add_middleware(
        RateLimitMiddleware,
        limiter=TokenBucketLimiter(
            capacity=settings.auth_rate_limit_capacity,
            window_seconds=settings.auth_rate_limit_window_seconds,
        ),
        path_prefix="/auth/",
        # The real endpoint list, so an unrouted /auth/* path cannot mint a
        # bucket of its own (see ``RateLimitMiddleware._key``).
        known_paths=[route.path for route in auth.router.routes if hasattr(route, "path")],
        trusted_proxies=settings.trusted_proxies(),
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(closet.router)
    app.include_router(me.router)
    return app


app = create_app()
