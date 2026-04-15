"""Shared content dataclasses for collector outputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class CollectedContent:
    title: str
    html: str
    plain_text: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
