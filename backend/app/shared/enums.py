"""Enumerations shared across the API, services and persistence layers."""

from __future__ import annotations

from enum import Enum


class MessageRole(str, Enum):
    """Author of a chat message."""

    USER = "user"
    ASSISTANT = "assistant"


class ToolName(str, Enum):
    """Tools the agent loop may call."""

    SEARCH_DOCS = "search_docs"
    READ_PAGE = "read_page"
    GENERATE_CONFIG = "generate_config"
    DIAGNOSE_LOG = "diagnose_log"


class ChatModel(str, Enum):
    """Chat models offered to users — the server-side allowlist.

    The model id reaches the provider straight from a client-supplied preference, so this
    enum is the trust boundary: anything not listed here never becomes a billed request.
    Every value is verified to exist on AvalAI.
    """

    GPT_4_1_MINI = "gpt-4.1-mini"
    GEMINI_2_5_FLASH = "gemini-2.5-flash"
    GPT_5_MINI = "gpt-5-mini"
    GPT_5_NANO = "gpt-5-nano"
    GPT_4_1_NANO = "gpt-4.1-nano"
    GPT_4O_MINI = "gpt-4o-mini"
    CLAUDE_HAIKU_4_5 = "claude-haiku-4-5"
    GPT_4_1 = "gpt-4.1"


class Platform(str, Enum):
    """Deployment platforms supported by the config wizard."""

    NODEJS = "nodejs"
    PYTHON = "python"
    DJANGO = "django"
    FLASK = "flask"
    FASTAPI = "fastapi"
    LARAVEL = "laravel"
    PHP = "php"
    NEXTJS = "nextjs"
    REACT = "react"
    VUE = "vue"
    ANGULAR = "angular"
    STATIC = "static"
    DOCKER = "docker"
    GO = "go"
    DOTNET = "dotnet"


class UserRole(str, Enum):
    """How the two kinds of operator are named on the wire and on screen.

    The stored representation is the ``users.is_superuser`` flag, not this string — nothing
    reads a role out of the database. This exists so the API and the panel have one spelling
    to agree on.
    """

    ADMIN = "admin"
    SUPERUSER = "superuser"

    @classmethod
    def of(cls, is_superuser: bool) -> "UserRole":
        """The role a flag means."""
        return cls.SUPERUSER if is_superuser else cls.ADMIN
