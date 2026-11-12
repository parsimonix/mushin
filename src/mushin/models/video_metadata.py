from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class VideoMetadata(BaseModel):
    """Filename-inferred video metadata; missing properties remain unknown.

    Common properties are typed explicitly. Additional GuessIt properties are
    preserved as JSON values. Language/country objects and dates become strings.
    Lists retain multiple matches rather than silently taking the first value.
    These are guesses, not verified properties of the video content.
    """

    model_config = ConfigDict(frozen=True, extra="allow", revalidate_instances="always")

    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    title: str | list[str] | None = None
    year: int | list[int] | None = None
    type: Literal["movie", "episode"] | None = None
    season: int | list[int] | None = None
    episode: int | list[int] | None = None
    episode_title: str | list[str] | None = None
    container: str | None = None
