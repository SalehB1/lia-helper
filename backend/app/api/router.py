"""Top-level API router. Mounted by ``main.py`` under ``/api``."""

from __future__ import annotations

from app.api.cbv import SlashInferringRouter
from app.api.v1.router import v1_router

api_router = SlashInferringRouter()
api_router.include_router(v1_router, prefix="/v1")

__all__ = ["api_router"]
