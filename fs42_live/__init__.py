"""fieldstation42-live: streams FieldStation42 channels as live MPEG-TS (see ARCHITECTURE.md)."""

import asyncio
from contextlib import asynccontextmanager


def install(app):
    """Add the /live endpoints and background jobs to upstream's FastAPI app."""
    from fs42_live.router import router
    from fs42_live.schedule_keeper import keep_schedules_built

    app.include_router(router)

    upstream_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        keeper = asyncio.create_task(keep_schedules_built())
        try:
            async with upstream_lifespan(a) as state:
                yield state
        finally:
            keeper.cancel()

    app.router.lifespan_context = lifespan
