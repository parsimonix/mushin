from pydantic import BaseModel, ConfigDict, Field


class FileMetadata(BaseModel):
    """Basic metadata for an artifact's logical name and stored content.

    extension is the final suffix, including its dot (or an empty string).
    It describes the filename, not a detected content format.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    filename: str = Field(min_length=1)
    stem: str
    extension: str
    size_bytes: int = Field(ge=0)
