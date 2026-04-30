from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class CompileResult:
    """Outcome of compiling a single MIB module."""

    name: str
    status: Literal["compiled", "cached", "failed"]
    # "borrowed" is intentionally absent in v1.0 — see DD-5 in plan.md
    output_paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
