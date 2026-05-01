from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Single source of truth for valid output format names.
# compiler.py imports this to build _FORMATTER_CLASSES — add new formats here.
VALID_FORMATS: frozenset[str] = frozenset({"json", "pysnmp"})


@dataclass
class CompilerConfig:
    """All tunable knobs for MibCompiler. Every field has a safe default.

    __post_init__ validates field values eagerly so misconfiguration is
    caught at construction time rather than buried inside an async stack.
    """

    # MIB source URL templates — @mib@ is replaced with the MIB name
    sources: list[str] = field(
        default_factory=lambda: [
            "https://mibs.pysnmp.com/asn1/@mib@",
            "https://mibbrowser.online/mibs/@mib@.mib",
        ]
    )

    # Output
    output_dir: Path = field(default_factory=lambda: Path("./mibs-output"))
    # list[str] rather than list[Literal[...]] so that adding a new formatter
    # only requires updating VALID_FORMATS above and compiler._FORMATTER_CLASSES.
    formats: list[str] = field(default_factory=lambda: ["json"])

    # HTTP
    http_timeout: float = 30.0
    http_retries: int = 3

    # Disk cache
    cache_dir: Path | None = field(default_factory=lambda: Path.home() / ".cache" / "trishul-smi")
    cache_ttl_days: int = 7  # 0 = never expire

    # Size guard — enforced by FileReader and HttpReader
    max_mib_size: int = 10 * 1024 * 1024  # 10 MB

    # Output content flags
    no_texts: bool = False  # suppress setDescription/setOrganization/setRevisions/TC description

    def __post_init__(self) -> None:
        if self.max_mib_size <= 0:
            raise ValueError(f"max_mib_size must be > 0, got {self.max_mib_size}")
        if self.http_timeout <= 0:
            raise ValueError(f"http_timeout must be > 0, got {self.http_timeout}")
        if self.http_retries < 0:
            raise ValueError(f"http_retries must be >= 0, got {self.http_retries}")
        if self.cache_ttl_days < 0:
            raise ValueError(
                f"cache_ttl_days must be >= 0 (0 = never expire), got {self.cache_ttl_days}"
            )
        if not self.sources:
            raise ValueError("sources must not be empty")
        if not self.formats:
            raise ValueError("formats must not be empty")
        unknown = set(self.formats) - VALID_FORMATS
        if unknown:
            raise ValueError(
                f"Unknown output format(s): {sorted(unknown)}. "
                f"Valid formats: {sorted(VALID_FORMATS)}"
            )
