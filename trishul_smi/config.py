from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class CompilerConfig:
    """All tunable knobs for MibCompiler. Every field has a safe default."""

    # MIB source URLs — @mib@ is substituted with the MIB name
    sources: list[str] = field(default_factory=lambda: [
        "https://mibs.pysnmp.com/asn1/@mib@",
        "https://www.circitor.fr/Mibs/Mib/@mib@.mib",
    ])

    # Output
    output_dir: Path = field(default_factory=lambda: Path("./mibs-output"))
    formats: list[Literal["json", "pysnmp"]] = field(default_factory=lambda: ["json"])

    # HTTP
    http_timeout: float = 30.0
    http_retries: int = 3

    # Disk cache
    cache_dir: Path | None = field(
        default_factory=lambda: Path.home() / ".cache" / "trishul-smi"
    )
    cache_ttl_days: int = 7  # TTL for HTTP-fetched MIBs; 0 = never expire

    # Size guard — enforced by FileReader and HttpReader
    max_mib_size: int = 10 * 1024 * 1024  # 10 MB
