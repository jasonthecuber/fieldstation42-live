"""fieldstation42-live: streams FieldStation42 channels as live MPEG-TS (see ARCHITECTURE.md)."""

import asyncio
from contextlib import asynccontextmanager


def install(app):
    """Add the /live endpoints and background jobs to upstream's FastAPI app."""
    from fs42_live.router import router
    from fs42_live.schedule_keeper import keep_breaks_found, keep_schedules_built
    from fs42_live.guide import fill_in_retro_guide
    from fs42_live.loudness import keep_measured
    from fs42_live.bars import keep_found

    from fastapi.responses import RedirectResponse

    app.include_router(router)
    # a short address to type on a TV remote: http://<server>:4242/tv
    app.add_api_route("/tv", lambda: RedirectResponse("/live/watch"), include_in_schema=False)
    fill_in_retro_guide()

    upstream_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        keeper = asyncio.create_task(keep_schedules_built())
        breaks = asyncio.create_task(keep_breaks_found())
        levels = asyncio.create_task(keep_measured())
        cropped = asyncio.create_task(keep_found())
        try:
            async with upstream_lifespan(a) as state:
                yield state
        finally:
            keeper.cancel()
            breaks.cancel()
            levels.cancel()
            cropped.cancel()

    app.router.lifespan_context = lifespan
