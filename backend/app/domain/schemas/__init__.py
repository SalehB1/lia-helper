"""Pydantic v2 API schemas. JSON is camelCase; Python stays snake_case."""

from __future__ import annotations

from app.domain.schemas.admin import (
    AdminSettingsResponse,
    AdminSettingsUpdate,
    ModelUsageRecord,
    SettingRecord,
    UsageResponse,
)
from app.domain.schemas.chat import ChatRequest
from app.domain.schemas.common import CamelModel, HealthResponse
from app.domain.schemas.conversation import (
    ConversationDetailResponse,
    ConversationResponse,
    MessageResponse,
    SourceRef,
)
from app.domain.schemas.profile import ProfileResponse, ProfileUpdate
from app.domain.schemas.tools import (
    ConfigRequest,
    ConfigResponse,
    DiagnoseRequest,
    DiagnoseResponse,
)
from app.domain.schemas.user import (
    LoginRequest,
    LogoutRequest,
    PasswordChange,
    PasswordReset,
    RegisterRequest,
    UserCreate,
    UserResponse,
    UserUpdate,
)

__all__ = [
    "AdminSettingsResponse",
    "AdminSettingsUpdate",
    "CamelModel",
    "ChatRequest",
    "ConfigRequest",
    "ConfigResponse",
    "ConversationDetailResponse",
    "ConversationResponse",
    "DiagnoseRequest",
    "DiagnoseResponse",
    "HealthResponse",
    "LoginRequest",
    "LogoutRequest",
    "MessageResponse",
    "ModelUsageRecord",
    "PasswordChange",
    "PasswordReset",
    "ProfileResponse",
    "ProfileUpdate",
    "RegisterRequest",
    "SettingRecord",
    "SourceRef",
    "UsageResponse",
    "UserCreate",
    "UserResponse",
    "UserUpdate",
]
