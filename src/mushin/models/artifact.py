from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def validate_filename(value: str) -> str:
    """Require a single logical filename, independent of host path syntax.

    Destination-specific rules and containment checks belong to the artifact
    service when it materializes content.
    """
    if not value.strip() or value in {".", ".."} or any(char in value for char in ("/", "\\", ":", "\x00")):
        raise ValueError("Artifact filename must be a single name, without path components")
    return value


class ArtifactRef(BaseModel):
    """Immutable reference to managed content with a logical filename.

    The artifact service assigns and resolves content_id; it is not a path.
    References with different filenames may share the same stored content.
    Creating a reference does not verify that its content exists.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    content_id: UUID
    filename: str = Field(min_length=1)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        return validate_filename(value)
