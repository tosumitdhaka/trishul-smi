"""Tests for trishul_smi.resolver.oid_resolver — full OID resolution pass."""

from __future__ import annotations

from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.resolver.oid_resolver import WELL_KNOWN_OIDS, resolve_oids


def _obj(name: str, oid_path: list[int], oid_parent: str | None = None) -> MibObject:
    oid = ".".join(str(n) for n in oid_path)
    return MibObject(
        name=name,
        oid=oid,
        oid_path=oid_path,
        oid_parent=oid_parent,
        object_type="OBJECT-TYPE",
    )


def _module(name: str, objects: dict[str, MibObject]) -> MibModule:
    return MibModule(name=name, language="SMIv2", objects=objects)


# ---------------------------------------------------------------------------
# Well-known seeds
# ---------------------------------------------------------------------------


class TestWellKnownSeeds:
    def test_mib2_in_well_known(self):
        assert "mib-2" in WELL_KNOWN_OIDS
        assert WELL_KNOWN_OIDS["mib-2"] == [1, 3, 6, 1, 2, 1]

    def test_enterprises_in_well_known(self):
        assert WELL_KNOWN_OIDS["enterprises"] == [1, 3, 6, 1, 4, 1]

    def test_internet_in_well_known(self):
        assert WELL_KNOWN_OIDS["internet"] == [1, 3, 6, 1]

    def test_iso_in_well_known(self):
        assert WELL_KNOWN_OIDS["iso"] == [1]

    def test_snmp_modules_in_well_known(self):
        assert "snmpModules" in WELL_KNOWN_OIDS


# ---------------------------------------------------------------------------
# Single-module resolution
# ---------------------------------------------------------------------------


class TestSingleModuleResolution:
    def test_no_parent_leaf_unchanged(self):
        """Object with no oid_parent keeps its oid_path."""
        obj = _obj("internet", [1, 3, 6, 1])
        m = _module("TEST-MIB", {"internet": obj})
        resolve_oids([m])
        assert obj.oid_path == [1, 3, 6, 1]
        assert obj.oid == "1.3.6.1"

    def test_well_known_parent_resolved(self):
        """{ mib-2 1 } → [1,3,6,1,2,1,1]."""
        obj = _obj("system", [1], oid_parent="mib-2")
        m = _module("TEST-MIB", {"system": obj})
        resolve_oids([m])
        assert obj.oid_path == [1, 3, 6, 1, 2, 1, 1]
        assert obj.oid == "1.3.6.1.2.1.1"

    def test_enterprises_child_resolved(self):
        """{ enterprises 99 } → [1,3,6,1,4,1,99]."""
        obj = _obj("myOrg", [99], oid_parent="enterprises")
        m = _module("TEST-MIB", {"myOrg": obj})
        resolve_oids([m])
        assert obj.oid_path == [1, 3, 6, 1, 4, 1, 99]

    def test_chain_within_module(self):
        """Root defined in same module, then child references it."""
        root = _obj("aRoot", [1, 3, 6])
        child = _obj("aChild", [1], oid_parent="aRoot")
        m = _module("A-MIB", {"aRoot": root, "aChild": child})
        resolve_oids([m])
        assert child.oid_path == [1, 3, 6, 1]

    def test_unresolvable_parent_leaves_path_unchanged(self):
        """Object whose parent is not found must not crash; oid_path unchanged."""
        obj = _obj("orphan", [99], oid_parent="noSuchParent")
        m = _module("TEST-MIB", {"orphan": obj})
        resolve_oids([m])
        assert obj.oid_path == [99]

    def test_resolved_oid_string_matches_path(self):
        obj = _obj("sysDescr", [1], oid_parent="system")
        system = _obj("system", [1], oid_parent="mib-2")
        m = _module("TEST-MIB", {"system": system, "sysDescr": obj})
        resolve_oids([m])
        assert obj.oid == ".".join(str(n) for n in obj.oid_path)


# ---------------------------------------------------------------------------
# Cross-module resolution
# ---------------------------------------------------------------------------


class TestCrossModuleResolution:
    def test_cross_module_child_resolved(self):
        """Module B's object references a name defined in module A."""
        root_obj = _obj("aRoot", [1, 3, 6])
        mod_a = _module("A-MIB", {"aRoot": root_obj})

        child_obj = _obj("bObj", [1], oid_parent="aRoot")
        mod_b = _module("B-MIB", {"bObj": child_obj})

        resolve_oids([mod_a, mod_b])
        assert child_obj.oid_path == [1, 3, 6, 1]
        assert child_obj.oid == "1.3.6.1"

    def test_modules_must_be_in_topo_order(self):
        """A child module listed before its parent leaves the child unresolved."""
        child_obj = _obj("bObj", [1], oid_parent="aRoot")
        mod_b = _module("B-MIB", {"bObj": child_obj})

        root_obj = _obj("aRoot", [1, 3, 6])
        mod_a = _module("A-MIB", {"aRoot": root_obj})

        resolve_oids([mod_b, mod_a])
        # bObj processed before aRoot is known → stays [1]
        assert child_obj.oid_path == [1]

    def test_three_hop_chain(self):
        """grandparent → parent → child resolved across three modules."""
        # gp ::= { iso 3 } → iso=[1], so gp=[1, 3]
        gp = _obj("gp", [3], oid_parent="iso")
        m1 = _module("M1", {"gp": gp})

        parent = _obj("par", [10], oid_parent="gp")
        m2 = _module("M2", {"par": parent})

        child = _obj("ch", [5], oid_parent="par")
        m3 = _module("M3", {"ch": child})

        resolve_oids([m1, m2, m3])
        assert gp.oid_path == [1, 3]
        assert parent.oid_path == [1, 3, 10]
        assert child.oid_path == [1, 3, 10, 5]


# ---------------------------------------------------------------------------
# Notifications resolved as well
# ---------------------------------------------------------------------------


class TestNotificationsResolved:
    def test_notification_parent_resolved(self):
        notif = MibObject(
            name="linkDown",
            oid="3",
            oid_path=[3],
            oid_parent="mib-2",
            object_type="NOTIFICATION-TYPE",
        )
        m = MibModule(name="IF-MIB", language="SMIv2", notifications={"linkDown": notif})
        resolve_oids([m])
        assert notif.oid_path == [1, 3, 6, 1, 2, 1, 3]


# ---------------------------------------------------------------------------
# Idempotency and edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_module_list(self):
        resolve_oids([])  # must not raise

    def test_module_with_no_objects(self):
        m = MibModule(name="EMPTY-MIB", language="SMIv2")
        resolve_oids([m])  # must not raise

    def test_multi_arc_local_path(self):
        """Local arcs [1, 2, 3] appended to parent [1, 3, 6, 1]."""
        obj = _obj("deep", [1, 2, 3], oid_parent="internet")
        m = _module("TEST-MIB", {"deep": obj})
        resolve_oids([m])
        assert obj.oid_path == [1, 3, 6, 1, 1, 2, 3]
