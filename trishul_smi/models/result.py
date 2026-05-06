from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class CompileResult:
    """Outcome of compiling a single MIB module."""

    name: str
    # "cached" is emitted by MibResolver on a disk-cache hit — not yet wired
    # in v1.0, but reserved here to avoid a future breaking Literal change
    # once the cache read-path is plumbed through the compiler (see DD-5).
    # "missing" — MIB source not found in any configured reader.
    status: Literal["compiled", "cached", "failed", "missing"]
    output_paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    is_dependency: bool = False
