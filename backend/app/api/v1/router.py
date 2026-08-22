"""Version 1 of the HTTP API."""

from __future__ import annotations

from app.api.cbv import SlashInferringRouter
from app.api.v1.endpoints import (
    admin,
    auth,
    chat,
    conversations,
    health,
    profile,
    tools,
    users,
)

v1_router = SlashInferringRouter()

v1_router.include_router(health.router, prefix="/health", tags=["health"])
v1_router.include_router(chat.router, prefix="/chat", tags=["chat"])
v1_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
v1_router.include_router(profile.router, prefix="/profile", tags=["profile"])
v1_router.include_router(tools.router, prefix="/tools", tags=["tools"])
v1_router.include_router(admin.router, prefix="/admin", tags=["admin"])
v1_router.include_router(auth.router, prefix="/auth", tags=["auth"])
v1_router.include_router(users.router, prefix="/users", tags=["users"])

__all__ = ["v1_router"]
