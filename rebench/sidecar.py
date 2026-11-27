"""The sidecar format lives in :mod:`reblend.project.sidecar` (RE-Blend reads it too)."""

from reblend.project.sidecar import (  # noqa: F401
    SCHEMA_VERSION, SIDECAR_FILENAME, ArtMark, Label, Sidecar, SpriteInfo,
)

__all__ = ["ArtMark", "SIDECAR_FILENAME", "SCHEMA_VERSION", "Label", "SpriteInfo", "Sidecar"]
