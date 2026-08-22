"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from __future__ import annotations

from app.domain.models.app_setting import AppSetting
from app.domain.models.base import Base, TimestampMixin, UUIDMixin
from app.domain.models.conversation import Conversation
from app.domain.models.message import Message
from app.domain.models.session import Session
from app.domain.models.user import User

__all__ = [
    "AppSetting",
    "Base",
    "Conversation",
    "Message",
    "Session",
    "TimestampMixin",
    "User",
    "UUIDMixin",
]
