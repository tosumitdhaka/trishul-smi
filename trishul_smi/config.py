from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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
            "https://www.circitor.fr/Mibs/Mib/@mib@.mib",
        ]
    )

    # Output
    output_dir: Path = field(default_factory=lambda: Path("./mibs-output"))
    # list[str] rather than list[Literal[...]] so that adding a new formatter
    # to compiler._FORMATTER_CLASSES does not require updating this annotation.
    # Runtime validation below mirrors _VALID_FORMATS in compiler.py — keep
    # the two sets in sync when adding a new output format.
    formats: list[str] = field(default_factory=lambda: ["json"])

    # HTTP
    http_timeout: float = 30.0
    http_retries: int = 3

    # Disk cache
    cache_dir: Path | None = field(default_factory=lambda: Path.home() / ".cache" / "trishul-smi")
    cache_ttl_days: int = 7  # 0 = never expire

    # Size guard — enforced by FileReader and HttpReader
    max_mib_size: int = 10 * 1024 * 1024  # 10 MB

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
        # Format names are validated here (early, before any I/O) and again
        # in MibCompiler.__init__ against the live _FORMATTER_CLASSES registry.
        # Both guards must agree — keep this set in sync with compiler.py.
        _known_formats = {"json", "pysnmp"}
        unknown = set(self.formats) - _known_formats
        if unknown:
            raise ValueError(
                f"Unknown format(s): {sorted(unknown)}. Valid formats: {sorted(_known_formats)}"
            )
