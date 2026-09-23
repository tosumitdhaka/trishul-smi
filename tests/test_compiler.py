"""Integration tests for MibCompiler, ReaderChain, and output formatters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers import MockReader
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.models import CompileResult
from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.output.json_fmt import JsonFormatter
from trishul_smi.reader.chain import ReaderChain

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

MINIMAL_V2 = """
TEST-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, Integer32 FROM SNMPv2-SMI ;
testMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Minimal test MIB."
    ::= { 1 3 }
END
"""

OBJECT_V2 = """
OBJECT-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
objectMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Obj Org"
    CONTACT-INFO "obj@example.com"
    DESCRIPTION  "MIB with an OBJECT-TYPE."
    ::= { 1 7 }
foo OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "A test object."
    ::= { objectMIB 1 }
END
"""

UPSTREAM_WITH_MISSING_DEP = """
UPSTREAM-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, Integer32 FROM SNMPv2-SMI
    MissingSymbol FROM MISSING-DEP ;
upstreamMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Upstream Org"
    CONTACT-INFO "upstream@example.com"
    DESCRIPTION  "MIB with an unresolved non-base dependency."
    ::= { 1 10 }
END
"""

DOWNSTREAM_IMPORTING_UPSTREAM = """
DOWNSTREAM-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, Integer32 FROM SNMPv2-SMI
    upstreamMIB FROM UPSTREAM-MIB ;
downstreamMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Downstream Org"
    CONTACT-INFO "downstream@example.com"
    DESCRIPTION  "Depends on UPSTREAM-MIB."
    ::= { 1 11 }
END
"""


# ---------------------------------------------------------------------------
# ReaderChain
# ---------------------------------------------------------------------------


class TestReaderChain:
    @pytest.mark.asyncio
    async def test_returns_first_reader_hit(self):
        r1 = MockReader({"IF-MIB": "content-from-r1"})
        r2 = MockReader({"IF-MIB": "content-from-r2"})
        chain = ReaderChain(r1, r2)
        assert await chain.fetch("IF-MIB") == "content-from-r1"

    @pytest.mark.asyncio
    async def test_falls_back_to_second_reader(self):
        r1 = MockReader({})
        r2 = MockReader({"IF-MIB": "content-from-r2"})
        chain = ReaderChain(r1, r2)
        assert await chain.fetch("IF-MIB") == "content-from-r2"

    @pytest.mark.asyncio
    async def test_raises_if_all_miss(self):
        chain = ReaderChain(MockReader({}), MockReader({}))
        with pytest.raises(MibNotFoundError):
            await chain.fetch("MISSING")

    @pytest.mark.asyncio
    async def test_non_notfound_error_propagates_immediately(self):
        """MibSizeLimitError must not be swallowed by the fallback logic."""
        r1 = MockReader({}, size_limit_names={"BIG-MIB"})
        r2 = MockReader({"BIG-MIB": "small copy"})
        chain = ReaderChain(r1, r2)
        with pytest.raises(MibSizeLimitError):
            await chain.fetch("BIG-MIB")

    def test_empty_readers_raises(self):
        with pytest.raises(ValueError, match="at least one reader"):
            ReaderChain()

    @pytest.mark.asyncio
    async def test_append_adds_reader(self):
        chain = ReaderChain(MockReader({}))
        chain.append(MockReader({"X": "found"}))
        assert await chain.fetch("X") == "found"


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


class TestJsonFormatter:
    def test_output_is_valid_json(self):
        m = MibModule(name="IF-MIB", language="SMIv2", imports={"SNMPv2-SMI": ["OBJECT-TYPE"]})
        data = json.loads(JsonFormatter().format(m))
        assert data["module"] == "IF-MIB"
        assert data["language"] == "SMIv2"
        assert "generated_by" in data

    def test_objects_serialised(self):
        obj = MibObject(
            name="ifIndex",
            oid="1.3.6.1",
            oid_path=[1, 3, 6, 1],
            object_type="OBJECT-TYPE",
            syntax="Integer32",
            max_access="read-only",
            status="current",
        )
        m = MibModule(name="IF-MIB", language="SMIv2", objects={"ifIndex": obj})
        data = json.loads(JsonFormatter().format(m))
        assert "ifIndex" in data["objects"]
        assert data["objects"]["ifIndex"]["syntax"] == "Integer32"

    def test_runtime_oid_emitted_from_canonical_path(self):
        obj = MibObject(
            name="ifIndex",
            oid="ifMIB.1",
            oid_path=[1, 3, 6, 1],
            oid_parent=None,
            object_type="OBJECT-TYPE",
            syntax="Integer32",
        )
        m = MibModule(name="IF-MIB", language="SMIv2", objects={"ifIndex": obj})
        data = json.loads(JsonFormatter().format(m))
        assert data["objects"]["ifIndex"]["oid"] == "1.3.6.1"

    def test_unresolved_symbolic_oid_omitted_from_json(self):
        obj = MibObject(
            name="ifIndex",
            oid="ifMIB.1",
            oid_path=[1],
            oid_parent="ifMIB",
            object_type="OBJECT-TYPE",
            syntax="Integer32",
        )
        m = MibModule(name="IF-MIB", language="SMIv2", objects={"ifIndex": obj})
        data = json.loads(JsonFormatter().format(m))
        assert "oid" not in data["objects"]["ifIndex"]
        assert data["objects"]["ifIndex"]["oid_path"] == [1]

    def test_empty_module_serialises(self):
        data = json.loads(JsonFormatter().format(MibModule(name="EMPTY-MIB", language="SMIv1")))
        assert data["objects"] == {}
        assert data["types"] == {}
        assert data["notifications"] == {}


# ---------------------------------------------------------------------------
# MibCompiler (integration)
# ---------------------------------------------------------------------------


class TestMibCompiler:
    def test_default_config_created_when_none_given(self):
        """MibCompiler() with no args must create a default CompilerConfig."""
        compiler = MibCompiler()
        assert compiler._config is not None
        assert "json" in compiler._config.formats

    def test_unknown_format_raises_at_construction(self):
        with pytest.raises(ValueError, match="Unknown output format"):
            MibCompiler(CompilerConfig(formats=["invalid-fmt"], cache_dir=None))

    def test_no_readers_raises(self):
        compiler = MibCompiler(CompilerConfig(cache_dir=None, formats=["json"]))
        with pytest.raises(RuntimeError, match="No readers"):
            import asyncio

            asyncio.run(compiler.compile("TEST-MIB"))

    @pytest.mark.asyncio
    async def test_compile_writes_json(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path / "out", formats=["json"], cache_dir=None)
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        results = await compiler.compile("TEST-MIB")
        compiled = [r for r in results if r.status == "compiled"]
        assert any(r.name == "TEST-MIB" for r in compiled)
        data = json.loads((tmp_path / "out" / "TEST-MIB.json").read_bytes())
        assert data["module"] == "TEST-MIB"

    @pytest.mark.asyncio
    async def test_missing_mib_status_missing(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({}))
        results = await compiler.compile("MISSING-MIB")
        assert any(r.name == "MISSING-MIB" and r.status == "missing" for r in results)

    @pytest.mark.asyncio
    async def test_requested_module_with_missing_dependency_fails_without_output(
        self, tmp_path: Path
    ):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(
            MockReader({"UPSTREAM-MIB": UPSTREAM_WITH_MISSING_DEP})
        )

        results = await compiler.compile("UPSTREAM-MIB")
        by_name = {result.name: result for result in results}

        assert by_name["UPSTREAM-MIB"].status == "failed"
        assert "MISSING-DEP" in (by_name["UPSTREAM-MIB"].error or "")
        assert by_name["MISSING-DEP"].status == "missing"
        assert not (tmp_path / "UPSTREAM-MIB.json").exists()

    @pytest.mark.asyncio
    async def test_transitive_dependents_of_missing_dependency_also_fail(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(
            MockReader(
                {
                    "UPSTREAM-MIB": UPSTREAM_WITH_MISSING_DEP,
                    "DOWNSTREAM-MIB": DOWNSTREAM_IMPORTING_UPSTREAM,
                }
            )
        )

        results = await compiler.compile("DOWNSTREAM-MIB")
        by_name = {result.name: result for result in results}

        assert by_name["DOWNSTREAM-MIB"].status == "failed"
        assert "UPSTREAM-MIB" in (by_name["DOWNSTREAM-MIB"].error or "")
        assert by_name["UPSTREAM-MIB"].status == "failed"
        assert "MISSING-DEP" in (by_name["UPSTREAM-MIB"].error or "")
        assert by_name["MISSING-DEP"].status == "missing"
        assert not (tmp_path / "DOWNSTREAM-MIB.json").exists()
        assert not (tmp_path / "UPSTREAM-MIB.json").exists()

    @pytest.mark.asyncio
    async def test_fluent_add_reader(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = (
            MibCompiler(config)
            .add_reader(MockReader({}))
            .add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        )
        results = await compiler.compile("TEST-MIB")
        assert any(r.status == "compiled" and r.name == "TEST-MIB" for r in results)

    @pytest.mark.asyncio
    async def test_add_reader_after_compile_raises(self, tmp_path: Path):
        """Issue #23 item 3 — the add_reader() docstring promise is now real:
        adding a reader after compile() has been invoked raises RuntimeError."""
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))

        await compiler.compile("TEST-MIB")

        with pytest.raises(RuntimeError, match="after compile"):
            compiler.add_reader(MockReader({"OTHER-MIB": MINIMAL_V2}))

    def test_add_reader_before_compile_is_allowed(self):
        config = CompilerConfig(cache_dir=None, formats=["json"])
        compiler = MibCompiler(config)
        returned = compiler.add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        assert returned is compiler
        assert len(compiler._readers) == 1

    @pytest.mark.asyncio
    async def test_failed_compile_does_not_poison_add_reader(self, tmp_path: Path):
        """A compile() that fails validation (no readers registered) must not
        set the compiled flag — add_reader stays usable afterwards (review L1)."""
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config)

        with pytest.raises(RuntimeError, match="No readers registered"):
            await compiler.compile("TEST-MIB")

        compiler.add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        results = await compiler.compile("TEST-MIB")
        assert any(r.status == "compiled" and r.name == "TEST-MIB" for r in results)

    @pytest.mark.asyncio
    async def test_output_dir_created(self, tmp_path: Path):
        out = tmp_path / "deep" / "nested" / "out"
        config = CompilerConfig(output_dir=out, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        await compiler.compile("TEST-MIB")
        assert out.is_dir()

    @pytest.mark.asyncio
    async def test_compile_result_has_output_paths(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        results = await compiler.compile("TEST-MIB")
        compiled = next(r for r in results if r.name == "TEST-MIB")
        assert len(compiled.output_paths) == 1
        assert compiled.output_paths[0].suffix == ".json"

    @pytest.mark.asyncio
    async def test_compile_object_mib(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"OBJECT-MIB": OBJECT_V2}))
        results = await compiler.compile("OBJECT-MIB")
        compiled = next((r for r in results if r.name == "OBJECT-MIB"), None)
        assert compiled is not None and compiled.status == "compiled"
        data = json.loads((tmp_path / "OBJECT-MIB.json").read_bytes())
        assert "foo" in data["objects"]
        assert data["objects"]["foo"]["syntax"] == "Integer32"

    @pytest.mark.asyncio
    async def test_formatter_error_captured_in_warnings_not_raised(self, tmp_path: Path):
        """A formatter that raises must not abort the compile run."""
        from unittest.mock import patch

        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))

        # Per-run formatter instances (issue #20) mean the shared registry is
        # no longer mutated, so patch the class method — the freshly-constructed
        # per-run JsonFormatter raises too.
        with patch.object(JsonFormatter, "format", side_effect=RuntimeError("simulated crash")):
            results = await compiler.compile("TEST-MIB")

        compiled = next((r for r in results if r.name == "TEST-MIB"), None)
        assert compiled is not None
        assert compiled.status == "compiled"
        assert len(compiled.output_paths) == 0
        assert any("formatter error" in w for w in compiled.warnings)
        assert any("simulated crash" in w for w in compiled.warnings)

    @pytest.mark.asyncio
    async def test_writer_error_raised_on_unwritable_output_dir(self, tmp_path: Path):
        """If the output directory cannot be created, WriterError is raised."""
        from unittest.mock import patch

        from trishul_smi.errors import WriterError

        config = CompilerConfig(output_dir=tmp_path / "out", cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        with patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")):
            with pytest.raises(WriterError, match="permission denied"):
                await compiler.compile("TEST-MIB")


# ---------------------------------------------------------------------------
# JsonFormatter — types and notifications serialisation
# ---------------------------------------------------------------------------


class TestJsonFormatterExtended:
    def test_types_serialised(self):
        from trishul_smi.models.mib_type import MibType

        tc = MibType(name="DisplayString", base_type="OCTET STRING", description="A display string")
        m = MibModule(name="TC-MIB", language="SMIv2", types={"DisplayString": tc})
        import json

        data = json.loads(JsonFormatter().format(m))
        assert "DisplayString" in data["types"]
        assert data["types"]["DisplayString"]["base_type"] == "OCTET STRING"
        assert data["types"]["DisplayString"]["description"] == "A display string"

    def test_notifications_serialised(self):
        notif = MibObject(
            name="linkDown",
            oid="1.3.6.1.6.3.1.1.5.3",
            oid_path=[1, 3, 6, 1, 6, 3, 1, 1, 5, 3],
            object_type="NOTIFICATION-TYPE",
            status="current",
        )
        m = MibModule(name="IF-MIB", language="SMIv2", notifications={"linkDown": notif})
        import json

        data = json.loads(JsonFormatter().format(m))
        assert "linkDown" in data["notifications"]
        assert data["notifications"]["linkDown"]["object_type"] == "NOTIFICATION-TYPE"


# ---------------------------------------------------------------------------
class TestIsDependency:
    @pytest.mark.asyncio
    async def test_requested_mib_not_dependency(self, tmp_path):
        from tests.helpers import MockReader
        from trishul_smi.compiler import MibCompiler
        from trishul_smi.config import CompilerConfig

        mib_text = """
REQD-MIB DEFINITIONS ::= BEGIN
IMPORTS MODULE-IDENTITY FROM SNMPv2-SMI ;
reqdMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Requested MIB."
    ::= { 1 400 }
END
"""
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"REQD-MIB": mib_text}))
        results = await compiler.compile("REQD-MIB")
        r = next(x for x in results if x.name == "REQD-MIB")
        assert r.is_dependency is False

    @pytest.mark.asyncio
    async def test_transitive_dep_is_dependency(self, tmp_path):
        from tests.helpers import MockReader
        from trishul_smi.compiler import MibCompiler
        from trishul_smi.config import CompilerConfig

        base_text = """
BASE-DEP-MIB DEFINITIONS ::= BEGIN
IMPORTS MODULE-IDENTITY FROM SNMPv2-SMI ;
baseMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Base."
    ::= { 1 401 }
END
"""
        child_text = """
CHILD-DEP-MIB DEFINITIONS ::= BEGIN
IMPORTS MODULE-IDENTITY FROM SNMPv2-SMI
        baseMIB FROM BASE-DEP-MIB ;
childMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Child."
    ::= { 1 402 }
END
"""
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(
            MockReader({"BASE-DEP-MIB": base_text, "CHILD-DEP-MIB": child_text})
        )
        results = await compiler.compile("CHILD-DEP-MIB")
        base = next((x for x in results if x.name == "BASE-DEP-MIB"), None)
        child = next((x for x in results if x.name == "CHILD-DEP-MIB"), None)
        assert child is not None and child.is_dependency is False
        assert base is not None and base.is_dependency is True


# ---------------------------------------------------------------------------
# v0.3.0 gap-closure tests
# ---------------------------------------------------------------------------

_NOTIF_MIB = """
NOTIF-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, NOTIFICATION-TYPE, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
notifMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "Notif Org"
    CONTACT-INFO "info@example.com"
    DESCRIPTION  "MIB with notifications."
    ::= { 1 20 }
ifIndex OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Interface index."
    ::= { notifMIB 1 }
ifOperStatus OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Operational status."
    ::= { notifMIB 2 }
linkDown NOTIFICATION-TYPE
    OBJECTS     { ifIndex, ifOperStatus }
    STATUS      current
    DESCRIPTION "Link went down."
    ::= { notifMIB 3 }
END
"""

_GROUP_MIB = """
GROUP-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI
    OBJECT-GROUP FROM SNMPv2-CONF ;
groupMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "Group Org"
    CONTACT-INFO "g@example.com"
    DESCRIPTION  "MIB with conformance groups."
    ::= { 1 21 }
fooObj OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "A foo."
    ::= { groupMIB 1 }
barObj OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "A bar."
    ::= { groupMIB 2 }
fooGroup OBJECT-GROUP
    OBJECTS     { fooObj, barObj }
    STATUS      current
    DESCRIPTION "Foo group."
    ::= { groupMIB 3 }
END
"""


class TestV030JsonGaps:
    """v0.3.0: JSON output gap closure."""

    def _fmt(self, mib_text: str, no_texts: bool = False) -> dict:
        from trishul_smi.parser.smi_parser import SmiParser

        parser = SmiParser()
        module = parser.parse(mib_text)
        return json.loads(JsonFormatter(no_texts=no_texts).format(module))

    # Item 1 — NOTIFICATION-TYPE members in JSON with module attribution
    def test_notification_members_present(self):
        data = self._fmt(_NOTIF_MIB)
        assert "linkDown" in data["notifications"]
        notif = data["notifications"]["linkDown"]
        assert notif["members"] == [
            {"module": "NOTIF-MIB", "object": "ifIndex"},
            {"module": "NOTIF-MIB", "object": "ifOperStatus"},
        ]

    # Item 2 — --no-texts suppresses descriptions in JSON
    def test_no_texts_removes_description(self):
        full = self._fmt(_NOTIF_MIB, no_texts=False)
        lean = self._fmt(_NOTIF_MIB, no_texts=True)
        assert "description" in full["notifications"]["linkDown"]
        assert "description" not in lean["notifications"]["linkDown"]
        assert "description" in full["objects"]["ifIndex"]
        assert "description" not in lean["objects"]["ifIndex"]

    def test_no_texts_module_metadata_structural_only(self):
        full = self._fmt(_NOTIF_MIB, no_texts=False)
        lean = self._fmt(_NOTIF_MIB, no_texts=True)
        # module_metadata is always present — structural fields survive no-texts
        assert "module_metadata" in full
        assert "module_metadata" in lean
        lean_meta = lean["module_metadata"]
        assert "lastupdated" in lean_meta
        assert "revisions" in lean_meta
        # text fields are stripped
        assert "organization" not in lean_meta
        assert "contactinfo" not in lean_meta
        assert "description" not in lean_meta
        # revision entries have date but no description
        for rev in lean_meta["revisions"]:
            assert "date" in rev
            assert "description" not in rev

    def test_no_texts_shrinks_output(self):
        from trishul_smi.parser.smi_parser import SmiParser

        module = SmiParser().parse(_NOTIF_MIB)
        full = JsonFormatter(no_texts=False).format(module)
        lean = JsonFormatter(no_texts=True).format(module)
        assert len(lean) < len(full)

    # Item 3 — module-identity metadata in JSON
    def test_module_metadata_fields(self):
        data = self._fmt(_NOTIF_MIB)
        meta = data["module_metadata"]
        assert meta["organization"] == "Notif Org"
        assert meta["contactinfo"] == "info@example.com"
        assert meta["lastupdated"] == "2020-01-01"
        assert isinstance(meta["revisions"], list)

    # J1 — nodetype field on OBJECT-TYPE entries (uses absolute OIDs so paths resolve)
    def test_nodetype_table_row_column_scalar(self):
        table_mib = """
TABLE-MIB DEFINITIONS ::= BEGIN
IMPORTS MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
tableMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "T"
    CONTACT-INFO "t@t.com"
    DESCRIPTION  "T."
    ::= { 1 60 }
myTable OBJECT-TYPE
    SYNTAX      SEQUENCE OF MyEntry
    MAX-ACCESS  not-accessible
    STATUS      current
    DESCRIPTION "The table."
    ::= { 1 60 1 }
MyEntry ::= SEQUENCE { myIndex Integer32, myVal Integer32 }
myEntry OBJECT-TYPE
    SYNTAX      MyEntry
    MAX-ACCESS  not-accessible
    STATUS      current
    DESCRIPTION "A row."
    ::= { 1 60 1 1 }
myIndex OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Index."
    ::= { 1 60 1 1 1 }
myVal OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Value."
    ::= { 1 60 1 1 2 }
myScalar OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Scalar."
    ::= { 1 60 2 }
END
"""
        data = self._fmt(table_mib)
        objects = data["objects"]
        assert objects["myTable"]["nodetype"] == "table"
        assert objects["myEntry"]["nodetype"] == "row"
        assert objects["myIndex"]["nodetype"] == "column"
        assert objects["myVal"]["nodetype"] == "column"
        assert objects["myScalar"]["nodetype"] == "scalar"
        # MODULE-IDENTITY should not have nodetype
        assert "nodetype" not in objects["tableMIB"]

    # J3 — module description in module_metadata
    def test_module_metadata_description(self):
        data = self._fmt(_NOTIF_MIB)
        meta = data["module_metadata"]
        assert "description" in meta
        assert "MIB with notifications" in meta["description"]

    # J4 — revision dates converted to ISO 8601
    def test_revision_dates_iso(self):
        mib_with_revisions = """
REV-MIB DEFINITIONS ::= BEGIN
IMPORTS MODULE-IDENTITY FROM SNMPv2-SMI ;
revMIB MODULE-IDENTITY
    LAST-UPDATED "200306140000Z"
    ORGANIZATION "Rev Org"
    CONTACT-INFO "rev@example.com"
    DESCRIPTION  "Revisions test."
    REVISION     "200306140000Z"
    DESCRIPTION  "Added stuff."
    REVISION     "9603280000Z"
    DESCRIPTION  "Initial version."
    ::= { 1 70 }
END
"""
        data = self._fmt(mib_with_revisions)
        meta = data["module_metadata"]
        assert meta["lastupdated"] == "2003-06-14"
        dates = [r["date"] for r in meta["revisions"]]
        assert "2003-06-14" in dates
        assert "1996-03-28" in dates

    # Item 4 — TC displayhint and status in types
    def test_tc_displayhint_and_status(self):
        tc_mib = """
TC-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;
tcMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "TC Org"
    CONTACT-INFO "tc@example.com"
    DESCRIPTION  "TC test."
    ::= { 1 22 }
DisplayString ::= TEXTUAL-CONVENTION
    DISPLAY-HINT "255a"
    STATUS       current
    DESCRIPTION  "Display string."
    SYNTAX       OCTET STRING (SIZE (0..255))
END
"""
        data = self._fmt(tc_mib)
        ds = data["types"]["DisplayString"]
        assert ds["display_hint"] == "255a"
        assert ds["status"] == "current"

    # J-G1 — class field on every object and TC
    def test_class_field_on_objects(self):
        data = self._fmt(_NOTIF_MIB)
        for name, obj in data["objects"].items():
            assert "class" in obj, f"missing class on {name}"
        for name, tc in data["types"].items():
            assert tc["class"] == "textualconvention", f"wrong class on type {name}"

    def test_class_field_values(self):
        data = self._fmt(_NOTIF_MIB)
        objects = data["objects"]
        # MODULE-IDENTITY
        mi_names = [n for n, o in objects.items() if o["object_type"] == "MODULE-IDENTITY"]
        assert mi_names
        assert objects[mi_names[0]]["class"] == "moduleidentity"
        # NOTIFICATION-TYPE
        notifs = data["notifications"]
        for obj in notifs.values():
            assert obj["class"] == "notificationtype"

    def test_class_field_objecttype(self):
        table_mib = """
TABLE2-MIB DEFINITIONS ::= BEGIN
IMPORTS MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
t2MIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z" ORGANIZATION "T"
    CONTACT-INFO "t" DESCRIPTION "T." ::= { 1 61 }
scalarObj OBJECT-TYPE
    SYNTAX Integer32 MAX-ACCESS read-only
    STATUS current DESCRIPTION "s." ::= { 1 61 1 }
END
"""
        data = self._fmt(table_mib)
        assert data["objects"]["scalarObj"]["class"] == "objecttype"

    # J-G2 — MODULE-COMPLIANCE group refs
    def test_module_compliance_members(self):
        compliance_mib = """
COMPLY-MIB DEFINITIONS ::= BEGIN
IMPORTS MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI
        MODULE-COMPLIANCE, OBJECT-GROUP FROM SNMPv2-CONF ;
complyMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z" ORGANIZATION "T"
    CONTACT-INFO "t" DESCRIPTION "T." ::= { 1 62 }
aObj OBJECT-TYPE SYNTAX Integer32 MAX-ACCESS read-only
    STATUS current DESCRIPTION "a." ::= { 1 62 1 }
bObj OBJECT-TYPE SYNTAX Integer32 MAX-ACCESS read-only
    STATUS current DESCRIPTION "b." ::= { 1 62 2 }
aGroup OBJECT-GROUP
    OBJECTS { aObj } STATUS current
    DESCRIPTION "a group." ::= { 1 62 10 }
bGroup OBJECT-GROUP
    OBJECTS { bObj } STATUS current
    DESCRIPTION "b group." ::= { 1 62 11 }
complyCompliance MODULE-COMPLIANCE
    STATUS current DESCRIPTION "The compliance."
    MODULE
        MANDATORY-GROUPS { aGroup }
        GROUP bGroup
            DESCRIPTION "Optional."
    ::= { 1 62 20 }
END
"""
        data = self._fmt(compliance_mib)
        mc = data["objects"]["complyCompliance"]
        assert mc["class"] == "modulecompliance"
        assert mc["members"] is not None
        member_objects = [m["object"] for m in mc["members"]]
        assert "aGroup" in member_objects
        assert "bGroup" in member_objects
        assert "complyCompliance" not in member_objects  # own name must not appear

    # Item 5 — conformance group members in JSON
    def test_group_members_present(self):
        data = self._fmt(_GROUP_MIB)
        assert "fooGroup" in data["objects"]
        group = data["objects"]["fooGroup"]
        member_objects = {m["object"] for m in group["members"]}
        assert member_objects == {"fooObj", "barObj"}


# v0.4.5 — missing_dependencies field and dry_run mode
# ---------------------------------------------------------------------------

_MISSING_DEP_MIB = """
MISSING-DEP-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    SomeSymbol FROM GHOST-MIB ;
missingDepMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Imports from a MIB that can never be found."
    ::= { 1 500 }
END
"""


class TestMissingDependencies:
    """CompileResult.missing_dependencies is populated correctly."""

    @pytest.mark.asyncio
    async def test_missing_mib_has_self_in_missing_dependencies(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({}))
        results = await compiler.compile("GHOST-MIB")
        r = next(x for x in results if x.name == "GHOST-MIB")
        assert r.status == "missing"
        assert r.missing_dependencies == ["GHOST-MIB"]

    @pytest.mark.asyncio
    async def test_blocked_module_lists_missing_deps(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"MISSING-DEP-MIB": _MISSING_DEP_MIB}))
        results = await compiler.compile("MISSING-DEP-MIB")
        r = next(x for x in results if x.name == "MISSING-DEP-MIB")
        assert r.status == "failed"
        assert "GHOST-MIB" in r.missing_dependencies

    @pytest.mark.asyncio
    async def test_compiled_module_has_empty_missing_dependencies(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        results = await compiler.compile("TEST-MIB")
        r = next(x for x in results if x.name == "TEST-MIB")
        assert r.status == "compiled"
        assert r.missing_dependencies == []

    @pytest.mark.asyncio
    async def test_missing_dependencies_default_empty(self):
        r = CompileResult(name="X", status="compiled")
        assert r.missing_dependencies == []

    @pytest.mark.asyncio
    async def test_blocked_chain_lists_correct_missing_dep(self, tmp_path: Path):
        """DOWNSTREAM blocked by UPSTREAM blocked by GHOST; each lists its own blocker."""
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(
            MockReader(
                {
                    "MISSING-DEP-MIB": _MISSING_DEP_MIB,
                    "DOWNSTREAM-MIB": DOWNSTREAM_IMPORTING_UPSTREAM.replace(
                        "UPSTREAM-MIB", "MISSING-DEP-MIB"
                    ).replace("upstreamMIB", "missingDepMIB"),
                }
            )
        )
        results = await compiler.compile("DOWNSTREAM-MIB")
        by_name = {r.name: r for r in results}
        assert "GHOST-MIB" in by_name["MISSING-DEP-MIB"].missing_dependencies
        assert "MISSING-DEP-MIB" in by_name["DOWNSTREAM-MIB"].missing_dependencies


class TestCachedStatus:
    """Issue #15 — modules served from the disk cache report status='cached'."""

    @pytest.mark.asyncio
    async def test_warm_cache_reports_cached_status(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path / "out", cache_dir=tmp_path / "cache", formats=["json"]
        )
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))

        first = await compiler.compile("TEST-MIB")
        assert all(r.status == "compiled" for r in first)

        second = await compiler.compile("TEST-MIB")
        by_name = {r.name: r for r in second}
        assert by_name["TEST-MIB"].status == "cached"
        assert by_name["TEST-MIB"].is_dependency is False
        # Output is still written for cached modules.
        assert (tmp_path / "out" / "TEST-MIB.json").is_file()

    @pytest.mark.asyncio
    async def test_cached_modules_still_report_warnings(self, tmp_path: Path):
        """Warnings persisted in the cache round-trip through a cached result."""
        mib_text = MINIMAL_V2
        config = CompilerConfig(
            output_dir=tmp_path / "out", cache_dir=tmp_path / "cache", formats=["json"]
        )
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": mib_text}))
        await compiler.compile("TEST-MIB")
        second = await compiler.compile("TEST-MIB")
        r = next(x for x in second if x.name == "TEST-MIB")
        assert r.status == "cached"
        assert r.warnings == []


class TestDryRun:
    """CompilerConfig.dry_run=True: full resolve/parse, no file writes."""

    @pytest.mark.asyncio
    async def test_dry_run_no_output_files(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path / "out", cache_dir=None, formats=["json"], dry_run=True
        )
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        results = await compiler.compile("TEST-MIB")
        assert not (tmp_path / "out").exists()
        r = next(x for x in results if x.name == "TEST-MIB")
        assert r.output_paths == []

    @pytest.mark.asyncio
    async def test_dry_run_status_still_compiled(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"], dry_run=True)
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        results = await compiler.compile("TEST-MIB")
        r = next(x for x in results if x.name == "TEST-MIB")
        assert r.status == "compiled"

    @pytest.mark.asyncio
    async def test_dry_run_detects_missing_dependency(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"], dry_run=True)
        compiler = MibCompiler(config).add_reader(MockReader({"MISSING-DEP-MIB": _MISSING_DEP_MIB}))
        results = await compiler.compile("MISSING-DEP-MIB")
        r = next(x for x in results if x.name == "MISSING-DEP-MIB")
        assert r.status == "failed"
        assert "GHOST-MIB" in r.missing_dependencies
        assert not any(f.exists() for f in tmp_path.iterdir()) if tmp_path.exists() else True

    @pytest.mark.asyncio
    async def test_dry_run_no_manifest_or_oid_index(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path,
            cache_dir=None,
            formats=["json"],
            dry_run=True,
            emit_manifest=True,
            emit_oid_index=True,
        )
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        await compiler.compile("TEST-MIB")
        assert not (tmp_path / "manifest.json").exists()
        assert not (tmp_path / "oid_index.json").exists()

    @pytest.mark.asyncio
    async def test_dry_run_false_writes_files(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path, cache_dir=None, formats=["json"], dry_run=False
        )
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        results = await compiler.compile("TEST-MIB")
        r = next(x for x in results if x.name == "TEST-MIB")
        assert len(r.output_paths) == 1
        assert r.output_paths[0].exists()


class TestPublicApiExports:
    """trishul_smi top-level exports are discoverable."""

    def test_mibcompiler_importable(self):
        from trishul_smi import MibCompiler as _MibCompiler

        assert _MibCompiler is MibCompiler

    def test_compilerconfig_importable(self):
        from trishul_smi import CompilerConfig as _Config

        assert _Config is CompilerConfig

    def test_compileresult_importable(self):
        from trishul_smi import CompileResult as _Result

        assert _Result is CompileResult

    def test_reader_classes_importable(self):
        from trishul_smi import FileReader, HttpReader, ZipReader

        assert FileReader is not None
        assert HttpReader is not None
        assert ZipReader is not None

    def test_error_classes_importable(self):
        from trishul_smi import (
            CircularDependencyError,
            MibCacheError,
            MibNotFoundError,
            ParseError,
            TrishulError,
            WriterError,
        )

        assert issubclass(MibNotFoundError, TrishulError)
        assert issubclass(ParseError, TrishulError)
        assert issubclass(CircularDependencyError, TrishulError)
        assert issubclass(WriterError, TrishulError)
        assert issubclass(MibCacheError, TrishulError)


class TestV030BaseLibExplicitRequest:
    """v0.3.0 Item 6 — explicitly requested BASE_MIBS are honoured."""

    @pytest.mark.asyncio
    async def test_explicit_base_mib_compiled(self, tmp_path: Path):
        snmpv2_mib_text = MINIMAL_V2.replace("TEST-MIB", "SNMPv2-MIB").replace("testMIB", "snmpMIB")
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"SNMPv2-MIB": snmpv2_mib_text}))
        results = await compiler.compile("SNMPv2-MIB")
        assert any(r.name == "SNMPv2-MIB" for r in results)
        assert (tmp_path / "SNMPv2-MIB.json").exists()

    @pytest.mark.asyncio
    async def test_base_mib_as_dep_still_skipped(self, tmp_path: Path):
        """When SNMPv2-SMI appears only as a transitive dep (not explicit), skip it."""
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        # MINIMAL_V2 imports SNMPv2-SMI — resolver should NOT try to fetch it
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        results = await compiler.compile("TEST-MIB")
        names = [r.name for r in results]
        assert "TEST-MIB" in names
        assert "SNMPv2-SMI" not in names
