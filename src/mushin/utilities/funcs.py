from __future__ import annotations


def validate_filename(value: str) -> str:
    """Require a single logical filename, independent of host path syntax.

    Destination-specific rules and containment checks belong to the artifact
    service when it materializes content.
    """
    if not value.strip() or value in {".", ".."} or any(char in value for char in ("/", "\\", ":", "\x00")):
        raise ValueError("Artifact filename must be a single name, without path components")
    return value
