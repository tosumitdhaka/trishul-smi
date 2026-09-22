from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class CompileResult:
    """Outcome of compiling a single MIB module."""

    name: str
    # "cached" — served from the compiled-module disk cache (MibCache) this
    # run; the module was not re-parsed. Emitted by MibCompiler since v0.4.9
    # (issue #15).
    # "missing" — MIB source not found in any configured reader.
    status: Literal["compiled", "cached", "failed", "missing"]
    output_paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    is_dependency: bool = False
    missing_dependencies: list[str] = field(default_factory=list)
