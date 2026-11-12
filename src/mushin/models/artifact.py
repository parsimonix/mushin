from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mushin.utilities.funcs import validate_filename


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
