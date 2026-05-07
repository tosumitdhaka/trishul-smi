"""Focused tests for optional OID index sidecars."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.helpers import MockReader
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.errors import WriterError
from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.output.json_bundle import (
    MANIFEST_FILENAME,
    OID_INDEX_FILENAME,
    JsonModuleArtifact,
    build_oid_index_bytes,
)
from trishul_smi.output.json_fmt import JsonFormatter
from trishul_smi.output.json_ir import make_json_artifact_metadata

TABLE_MIB = """
TABLE-MIB DEFINITIONS ::= BEGIN
IMPORTS MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
tableMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "T"
    CONTACT-INFO "t@example.com"
    DESCRIPTION  "Table module."
    ::= { 1 260 }
myTable OBJECT-TYPE
    SYNTAX      SEQUENCE OF MyEntry
    MAX-ACCESS  not-accessible
    STATUS      current
    DESCRIPTION "The table."
    ::= { 1 260 1 }
MyEntry ::= SEQUENCE { myIndex Integer32, myVal Integer32 }
myEntry OBJECT-TYPE
    SYNTAX      MyEntry
    MAX-ACCESS  not-accessible
    STATUS      current
    DESCRIPTION "A row."
    ::= { 1 260 1 1 }
myIndex OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Index."
    ::= { 1 260 1 1 1 }
myVal OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Value."
    ::= { 1 260 1 1 2 }
myScalar OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Scalar."
    ::= { 1 260 2 }
END
"""

NOTIF_MIB = """
NOTIF-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32, NOTIFICATION-TYPE FROM SNMPv2-SMI ;
notifMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "Notif Org"
    CONTACT-INFO "notif@example.com"
    DESCRIPTION  "Notification module."
    ::= { 1 261 }
ifIndex OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Index."
    ::= { 1 261 1 }
linkDown NOTIFICATION-TYPE
    STATUS      current
    DESCRIPTION "Link down."
    ::= { 1 261 2 }
END
"""

DUP_A_MIB = """
DUP-A DEFINITIONS ::= BEGIN
IMPORTS MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
dupAMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "Dup A"
    CONTACT-INFO "dupa@example.com"
    DESCRIPTION  "Dup A module."
    ::= { 1 270 }
dupValue OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Dup A value."
    ::= { 1 270 1 }
END
"""

DUP_B_MIB = """
DUP-B DEFINITIONS ::= BEGIN
IMPORTS MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
dupBMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "Dup B"
    CONTACT-INFO "dupb@example.com"
    DESCRIPTION  "Dup B module."
    ::= { 1 999 }
dupValue OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Dup B value."
    ::= { 1 270 1 }
END
"""

BAD_MIB = """
BAD-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;
badMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "Bad Org"
    CONTACT-INFO "bad@example.com"
    DESCRIPTION  "Bad module."
    ::= { 1 280 }
END
"""


class TestOidIndexEmission:
    @pytest.mark.asyncio
    async def test_oid_index_not_emitted_by_default(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, formats=["json"], cache_dir=None)
        compiler = MibCompiler(config).add_reader(MockReader({"TABLE-MIB": TABLE_MIB}))

        await compiler.compile("TABLE-MIB")

        assert not (tmp_path / OID_INDEX_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_oid_index_emitted_when_requested(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path,
            formats=["json"],
            cache_dir=None,
            emit_oid_index=True,
        )
        compiler = MibCompiler(config).add_reader(
            MockReader({"TABLE-MIB": TABLE_MIB, "NOTIF-MIB": NOTIF_MIB})
        )

        await compiler.compile("TABLE-MIB", "NOTIF-MIB")

        oid_index = json.loads((tmp_path / OID_INDEX_FILENAME).read_bytes())
        assert "1.260.1.1.1" in oid_index["oids"]
        assert oid_index["oids"]["1.260.1.1.1"] == [
            {
                "module": "TABLE-MIB",
                "object": "myIndex",
                "class": "objecttype",
                "object_type": "OBJECT-TYPE",
                "nodetype": "column",
            }
        ]
        assert oid_index["oids"]["1.261.2"] == [
            {
                "module": "NOTIF-MIB",
                "object": "linkDown",
                "class": "notificationtype",
                "object_type": "NOTIFICATION-TYPE",
            }
        ]

    @pytest.mark.asyncio
    async def test_oid_index_supports_multiple_entries_per_oid(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path,
            formats=["json"],
            cache_dir=None,
            emit_oid_index=True,
        )
        compiler = MibCompiler(config).add_reader(
            MockReader({"DUP-A": DUP_A_MIB, "DUP-B": DUP_B_MIB})
        )

        await compiler.compile("DUP-B", "DUP-A")

        oid_index = json.loads((tmp_path / OID_INDEX_FILENAME).read_bytes())
        assert oid_index["oids"]["1.270.1"] == [
            {
                "module": "DUP-A",
                "object": "dupValue",
                "class": "objecttype",
                "object_type": "OBJECT-TYPE",
                "nodetype": "scalar",
            },
            {
                "module": "DUP-B",
                "object": "dupValue",
                "class": "objecttype",
                "object_type": "OBJECT-TYPE",
                "nodetype": "scalar",
            },
        ]

    @pytest.mark.asyncio
    async def test_oid_index_lists_only_successfully_emitted_json_modules(self, tmp_path: Path):
        class FailingJsonFormatter(JsonFormatter):
            def format(self, module):  # noqa: ANN001
                if module.name == "BAD-MIB":
                    raise RuntimeError("simulated json failure")
                return super().format(module)

        config = CompilerConfig(
            output_dir=tmp_path,
            formats=["json"],
            cache_dir=None,
            emit_oid_index=True,
        )
        compiler = MibCompiler(config).add_reader(
            MockReader({"TABLE-MIB": TABLE_MIB, "BAD-MIB": BAD_MIB})
        )
        compiler._formatters = {"json": FailingJsonFormatter()}

        await compiler.compile("TABLE-MIB", "BAD-MIB")

        oid_index = json.loads((tmp_path / OID_INDEX_FILENAME).read_bytes())
        all_modules = {
            entry["module"] for entries in oid_index["oids"].values() for entry in entries
        }
        assert all_modules == {"TABLE-MIB"}

    @pytest.mark.asyncio
    async def test_oid_index_omitted_when_no_json_modules_emitted(self, tmp_path: Path):
        class FailingJsonFormatter(JsonFormatter):
            def format(self, module):  # noqa: ANN001
                raise RuntimeError(f"simulated json failure for {module.name}")

        config = CompilerConfig(
            output_dir=tmp_path,
            formats=["json"],
            cache_dir=None,
            emit_oid_index=True,
        )
        compiler = MibCompiler(config).add_reader(MockReader({"BAD-MIB": BAD_MIB}))
        compiler._formatters = {"json": FailingJsonFormatter()}

        await compiler.compile("BAD-MIB")

        assert not (tmp_path / OID_INDEX_FILENAME).exists()

    def test_oid_index_allows_symbolic_oid_keys(self):
        module = MibModule(
            name="HOST-RESOURCES-MIB",
            language="SMIv2",
            objects={
                "hrMIBAdminInfoNode": MibObject(
                    name="hrMIBAdminInfoNode",
                    oid="hrMIBAdminInfo.1",
                    oid_path=[],
                    object_type="OBJECT-TYPE",
                    syntax="Integer32",
                ),
                "hrSystemUptime": MibObject(
                    name="hrSystemUptime",
                    oid="1.3.6.1.2.1.25.1.1",
                    oid_path=[1, 3, 6, 1, 2, 1, 25, 1, 1],
                    object_type="OBJECT-TYPE",
                    syntax="Integer32",
                ),
            },
        )

        payload = json.loads(
            build_oid_index_bytes(
                make_json_artifact_metadata(),
                [
                    JsonModuleArtifact(
                        module="HOST-RESOURCES-MIB",
                        file="HOST-RESOURCES-MIB.json",
                        module_data=module,
                    )
                ],
            )
        )

        assert "1.3.6.1.2.1.25.1.1" in payload["oids"]
        assert "hrMIBAdminInfo.1" in payload["oids"]
        assert payload["oids"]["hrMIBAdminInfo.1"] == [
            {
                "module": "HOST-RESOURCES-MIB",
                "object": "hrMIBAdminInfoNode",
                "class": "objecttype",
                "object_type": "OBJECT-TYPE",
                "nodetype": "scalar",
            }
        ]

    @pytest.mark.asyncio
    async def test_manifest_references_oid_index_when_both_requested(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path,
            formats=["json"],
            cache_dir=None,
            emit_manifest=True,
            emit_oid_index=True,
        )
        compiler = MibCompiler(config).add_reader(MockReader({"TABLE-MIB": TABLE_MIB}))

        await compiler.compile("TABLE-MIB")

        manifest = json.loads((tmp_path / MANIFEST_FILENAME).read_bytes())
        assert manifest["sidecars"] == {"oid_index": OID_INDEX_FILENAME}

    @pytest.mark.asyncio
    async def test_oid_index_write_error_raises_writer_error(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path,
            formats=["json"],
            cache_dir=None,
            emit_oid_index=True,
        )
        compiler = MibCompiler(config).add_reader(MockReader({"TABLE-MIB": TABLE_MIB}))
        original_write_bytes = Path.write_bytes

        def _write_bytes(path: Path, data: bytes) -> int:
            if path.name == OID_INDEX_FILENAME:
                raise OSError("disk full")
            return original_write_bytes(path, data)

        with patch("pathlib.Path.write_bytes", autospec=True, side_effect=_write_bytes):
            with pytest.raises(WriterError, match="OID index"):
                await compiler.compile("TABLE-MIB")
