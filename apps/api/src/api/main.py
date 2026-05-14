from fastapi import FastAPI

from api.routes import health


def create_app() -> FastAPI:
    app = FastAPI(title="Sizeify API")
    app.include_router(health.router)
    return app


app = create_app()
