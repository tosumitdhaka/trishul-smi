from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Built-in output format names. Plugin formats registered under the
# ``trishul_smi.formatters`` entry-point group are discovered dynamically by
# ``output/registry.py``, so they cannot be validated statically here — the
# registry resolves them when MibCompiler is constructed and raises there for
# unknown names. This set documents the built-ins only.
VALID_FORMATS: frozenset[str] = frozenset({"json"})

# Allowlist for MIB names accepted from the CLI (issue #22). Names flow into
# filesystem paths (FileReader: directory / f"{name}{ext}") and HTTP URL
# templates (HttpReader: template.replace("@mib@", name)), so anything that
# could escape a directory or steer a URL is rejected up front. Discovered
# --mib-dir stems must pass too (e.g. "mib-802.1ap"), hence dots, underscores,
# and hyphens are allowed; a leading dot is not (it permits hidden / relative
# names like ".."). No leading dot, no path separators, no URL-special chars.
MIB_NAME_PATTERN: str = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_MIB_NAME_RE: re.Pattern[str] = re.compile(MIB_NAME_PATTERN)


def validate_mib_name(name: str) -> None:
    """Validate a MIB name against the allowlist.

    Raises
    ------
    ValueError
        If *name* starts with a non-alphanumeric character or contains
        anything outside ``[A-Za-z0-9._-]`` (e.g. path separators or
        URL-special characters).
    """
    if not _MIB_NAME_RE.fullmatch(name):
        raise ValueError(
            f"Invalid MIB name {name!r}: must match {MIB_NAME_PATTERN} "
            "(start with a letter or digit; then only letters, digits, '.', '_', '-')."
        )


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
    # list[str] rather than list[Literal[...]] so that plugin format names
    # (discovered via the trishul_smi.formatters entry-point group) can pass
    # construction; unknown names fail later, in MibCompiler, with a listing of
    # available formats (built-ins + discovered plugins).
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
    emit_manifest: bool = False
    emit_oid_index: bool = False
    dry_run: bool = False
    # Pin generated_at to a fixed epoch so repeated runs over the same source
    # (and version) emit byte-identical module JSON + sidecars.
    reproducible: bool = False

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
        # The pysnmp .py output format was removed in v0.5.0 (breaking change).
        # Give a pointed error before the generic unknown-format message so
        # `-f pysnmp` users know the format is gone and what to use instead.
        if "pysnmp" in self.formats:
            raise ValueError(
                "the 'pysnmp' output format was removed in v0.5.0 — use the JSON "
                "bundle output instead (--format json, optionally with "
                "--emit-manifest / --emit-oid-index); 'tsmi convert' is "
                "unaffected for reading existing .py files"
            )
        if self.emit_manifest and "json" not in self.formats:
            raise ValueError("emit_manifest requires 'json' in formats")
        if self.emit_oid_index and "json" not in self.formats:
            raise ValueError("emit_oid_index requires 'json' in formats")
