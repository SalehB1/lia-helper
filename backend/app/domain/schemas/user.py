"""Wire shapes for panel operators.

``password_hash`` appears in none of these on purpose: there is no response model anywhere
that can serialize it, which is a stronger guarantee than remembering to exclude it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field, computed_field

from app.core.security import MIN_PASSWORD_CHARS
from app.domain.schemas.common import CamelModel
from app.shared.enums import UserRole

MAX_USERNAME_CHARS = 64
MAX_DISPLAY_NAME_CHARS = 80
#: Bounded so a password can never become a denial-of-service: scrypt hashes whatever it is
#: given, and an unbounded field would let one request burn arbitrary CPU.
MAX_PASSWORD_CHARS = 200


class LoginRequest(CamelModel):
    """Sign-in body."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    username: str = Field(min_length=1, max_length=MAX_USERNAME_CHARS)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_CHARS)


class RegisterRequest(CamelModel):
    """Someone creating their own account, pending an admin's approval.

    Deliberately **not** a subclass of :class:`UserCreate`. That schema carries ``role``, and
    a public caller who could set it would promote themselves to superuser with one field.
    Two schemas that happen to share three fields is the cheap price of that not being
    possible.

    ``username`` is restricted to lowercase because ``ix_users_username`` is a BINARY unique
    index and the login path only strips whitespace: without the pattern, ``Admin`` and
    ``admin`` are two different accounts, and the second one is a phishing primitive.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    username: str = Field(min_length=3, max_length=MAX_USERNAME_CHARS, pattern=r"^[a-z0-9._-]+$")
    password: str = Field(min_length=MIN_PASSWORD_CHARS, max_length=MAX_PASSWORD_CHARS)
    display_name: str | None = Field(default=None, max_length=MAX_DISPLAY_NAME_CHARS)


class LogoutRequest(CamelModel):
    """An empty body, required so signing out cannot be a CORS-simple request.

    Without a JSON body a bare ``POST /auth/logout`` is a simple request that any page on
    the internet can fire at a signed-in operator's browser. It costs nothing but a
    nuisance sign-out, but the whole CSRF story here is "state-changing routes need JSON",
    and one exception is how that story stops being true.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PasswordChange(CamelModel):
    """Changing your own password."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_CHARS)
    new_password: str = Field(min_length=MIN_PASSWORD_CHARS, max_length=MAX_PASSWORD_CHARS)


class UserResponse(CamelModel):
    """One operator, as the panel sees them. Public uuid only — never the integer id."""

    uuid: str
    username: str
    display_name: str | None = None
    is_superuser: bool
    is_active: bool
    created_at: datetime | None = None

    @computed_field
    @property
    def role(self) -> UserRole:
        """The flag, named. Kept in the response so the panel has one word to compare on
        rather than re-deriving the meaning of a boolean in three components."""
        return UserRole.of(self.is_superuser)


class UserCreate(CamelModel):
    """A superuser adding an operator."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    username: str = Field(min_length=1, max_length=MAX_USERNAME_CHARS)
    password: str = Field(min_length=MIN_PASSWORD_CHARS, max_length=MAX_PASSWORD_CHARS)
    role: UserRole = UserRole.ADMIN
    display_name: str | None = Field(default=None, max_length=MAX_DISPLAY_NAME_CHARS)


class UserUpdate(CamelModel):
    """A superuser changing someone's role, activation or display name."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    role: UserRole | None = None
    is_active: bool | None = None
    display_name: str | None = Field(default=None, max_length=MAX_DISPLAY_NAME_CHARS)


class PasswordReset(CamelModel):
    """A superuser setting someone else's password."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    new_password: str = Field(min_length=MIN_PASSWORD_CHARS, max_length=MAX_PASSWORD_CHARS)
