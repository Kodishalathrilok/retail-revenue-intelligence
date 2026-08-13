"""Vercel Python function exposing the FastAPI app.

Vercel's Python runtime serves any module-level ASGI `app`, so the whole API
runs here as a serverless function alongside the Next.js frontend -- one
project, one deploy, no separate host.

That is only possible because the published tier does not import the
scientific stack. See pyproject.toml: core dependencies are 30 MB against
Vercel's 250 MB limit; the pipeline extras are 332.9 MB and stay local.
"""

from rrip.api.main import app  # noqa: F401
