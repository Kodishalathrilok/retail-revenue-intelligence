"""Entrypoint that runs the API on an event loop psycopg can actually use.

psycopg's async driver refuses to run on Windows' ProactorEventLoop:

    InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async
    mode.

That surfaces confusingly, because the pool swallows the connect error and the
caller sees only `PoolTimeout: pool initialization incomplete after 15 sec`.

Setting `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())` before
`uvicorn.run()` is NOT enough -- uvicorn 0.52 builds its own loop and the app
still receives a ProactorEventLoop (verified by inspecting the running loop from
inside a startup hook). So the loop is constructed here and uvicorn is told not
to make one, via `loop="none"`.
"""

from __future__ import annotations

import asyncio
import sys


def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    import uvicorn

    if sys.platform != "win32":
        uvicorn.run("rrip.api.main:app", host=host, port=port, reload=reload)
        return

    if reload:
        # The reloader spawns a subprocess that builds its own loop, which this
        # workaround cannot reach. Failing loudly beats starting a server whose
        # every database call errors.
        raise RuntimeError(
            "--reload is not supported on Windows: the reload subprocess builds "
            "a ProactorEventLoop that psycopg cannot use. Run without --reload.")

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    config = uvicorn.Config("rrip.api.main:app", host=host, port=port,
                            loop="none", log_level="info")
    server = uvicorn.Server(config)

    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.serve())
    finally:
        loop.close()


if __name__ == "__main__":
    serve()
