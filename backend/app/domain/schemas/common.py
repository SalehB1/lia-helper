"""Shared schema base and health payload."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base for every API schema: ORM-friendly in, camelCase out."""

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    @field_serializer("created_at", "updated_at", check_fields=False)
    def _serialize_utc(self, value: datetime | None) -> str | None:
        """Always emit a timezone designator on timestamps.

        SQLite's DATETIME column drops the offset on write and hands back a naive
        datetime. Without the ``+00:00`` suffix, ``new Date()`` in the panel would read
        the value as LOCAL time and shift every timestamp by the client's offset.

        Args:
            value: The stored timestamp, tz-aware or naive-UTC.

        Returns:
            An ISO-8601 string carrying an explicit UTC offset.
        """
        if value is None:
            return None
        stamped = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return stamped.isoformat()


class HealthResponse(CamelModel):
    """Liveness/readiness payload for `/healthz` and `/api/v1/health`."""

    status: str
    chunks: int
    embeddings: bool
    db: bool
    disk: bool
    #: Corpus provenance, from `data/corpus_meta.json`. Both are None until the first
    #: ingest that writes the sidecar. Provenance only — never any document text.
    ingested_at: str | None = None
    corpus_commit: str | None = None
    #: Turns generating right now. A count only, like `chunks` — this is the one public
    #: route, so nothing here may carry a conversation uuid or a word of anyone's text.
    live_runs: int = 0
