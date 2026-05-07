"""Lark Transformer: converts a parsed Tree into a MibModule dataclass.

Design: every grammar rule method receives already-transformed children.
Syntax types (integer_type, octet_string_type, …) return _SyntaxInfo objects
directly, so assignment methods use typed extractors — no string sniffing.
Description/status/access fields use typed wrappers for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from lark import Token, Transformer, Tree

from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.models.mib_type import MibType
from trishul_smi.parser._constants import SMIv2_MARKERS

# ---------------------------------------------------------------------------
# Internal typed wrapper dataclasses
# ---------------------------------------------------------------------------


@dataclass
class _SyntaxInfo:
    value: str
    constraint: _ConstraintInfo | None = None


@dataclass
class _DescriptionInfo:
    value: str


@dataclass
class _StatusInfo:
    value: str


@dataclass
class _AccessInfo:
    value: str


@dataclass
class _IndexInfo:
    columns: list[str]


@dataclass
class _AugmentsInfo:
    row: str


@dataclass
class _OrganizationInfo:
    value: str


@dataclass
class _ContactInfo:
    value: str


@dataclass
class _LastUpdatedInfo:
    value: str


@dataclass
class _MembersInfo:
    names: list[str]


@dataclass
class _RevisionInfo:
    date: str
    description: str


@dataclass
class _ConstraintInfo:
    kind: str  # "size" | "range" | "enum" | "bits" | "union"
    data: Any  # list of [low,high] pairs, list of [name,int] pairs, or list of _ConstraintInfo

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "union":
            return {
                "kind": "union",
                "data": [m.to_dict() if isinstance(m, _ConstraintInfo) else m for m in self.data],
            }
        return {"kind": self.kind, "data": self.data}


@dataclass
class _DisplayHintInfo:
    value: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unquote(token: Token | str) -> str:
    s = str(token)
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('\\"', '"')
    return s


def _resolve_oid(components: list[Any]) -> tuple[str, list[int], str | None]:
    """Convert oid_component list → (dotted_string, local_int_arcs, parent_name).

    parent_name is the first name_arc (e.g. 'mib-2' in { mib-2 1 }) — used by
    oid_resolver.py to look up the absolute prefix from already-resolved modules.
    named_arc (e.g. iso(1)) is self-contained and has no separate parent.
    """
    parts_str: list[str] = []
    parts_int: list[int] = []
    parent_name: str | None = None
    for comp in components:
        if not isinstance(comp, Tree):
            continue
        if comp.data == "named_arc":
            num = int(str(comp.children[1]))
            parts_str.append(str(num))
            parts_int.append(num)
        elif comp.data == "name_arc":
            name = str(comp.children[0])
            parts_str.append(name)
            if parent_name is None:
                parent_name = name  # capture only the first name arc
        elif comp.data == "number_arc":
            num = int(str(comp.children[0]))
            parts_str.append(str(num))
            parts_int.append(num)
    return ".".join(parts_str), parts_int, parent_name


# ---------------------------------------------------------------------------
# Transformer
# ---------------------------------------------------------------------------


class MibTransformer(Transformer[Token, MibModule]):
    """Walks the Lark parse tree and builds a MibModule."""

    def start(self, children: list[Any]) -> MibModule:
        return cast(MibModule, children[0])

    def module_definition(self, children: list[Any]) -> MibModule:
        module_name: str = ""
        imports: dict[str, list[str]] = {}
        objects: dict[str, MibObject] = {}
        types: dict[str, MibType] = {}
        notifications: dict[str, MibObject] = {}
        organization: str | None = None
        contactinfo: str | None = None
        lastupdated: str | None = None
        revisions: list[dict[str, str]] = []
        description: str | None = None

        def _process_child(child: Any) -> None:
            nonlocal module_name, imports, organization, contactinfo, lastupdated, description
            if isinstance(child, list):
                for item in child:
                    _process_child(item)
            elif isinstance(child, str):
                module_name = child
            elif isinstance(child, dict) and "__imports__" in child:
                imports = child["__imports__"]
            elif isinstance(child, MibObject):
                if child.object_type == "NOTIFICATION-TYPE":
                    notifications[child.name] = child
                else:
                    objects[child.name] = child
                if child.object_type == "MODULE-IDENTITY" and child.description:
                    description = child.description
            elif isinstance(child, MibType):
                types[child.name] = child
            elif isinstance(child, _OrganizationInfo):
                organization = child.value
            elif isinstance(child, _ContactInfo):
                contactinfo = child.value
            elif isinstance(child, _LastUpdatedInfo):
                lastupdated = child.value
            elif isinstance(child, _RevisionInfo):
                revisions.append({"date": child.date, "description": child.description})

        for child in children:
            _process_child(child)

        language = "SMIv2" if any(m in imports for m in SMIv2_MARKERS) else "SMIv1"

        return MibModule(
            name=module_name,
            language=language,  # type: ignore[arg-type]
            imports=imports,
            objects=objects,
            types=types,
            notifications=notifications,
            organization=organization,
            contactinfo=contactinfo,
            lastupdated=lastupdated,
            revisions=revisions,
            description=description,
        )

    def module_name(self, children: list[Any]) -> str:
        return str(children[0])

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def imports(self, children: list[Any]) -> dict[str, Any]:
        result: dict[str, list[str]] = {}
        for clause in children:
            if isinstance(clause, dict):
                result.update(clause)
        return {"__imports__": result}

    def import_clause(self, children: list[Any]) -> dict[str, list[str]]:
        return {str(children[1]): children[0]}

    def symbol_list(self, children: list[Any]) -> list[str]:
        return [str(c) for c in children]

    def symbol(self, children: list[Any]) -> str:
        return str(children[0])

    def module_ref(self, children: list[Any]) -> str:
        return str(children[0])

    # ------------------------------------------------------------------
    # MODULE-IDENTITY
    # ------------------------------------------------------------------

    def module_identity_assignment(self, children: list[Any]) -> list[Any]:
        name = str(children[0])
        oid_str, oid_path, oid_parent = _resolve_oid(self._oid(children))
        # QUOTED_STRING positional order: LAST-UPDATED[0], ORGANIZATION[1],
        # CONTACT-INFO[2], DESCRIPTION[3]
        quoted = [c for c in children if isinstance(c, Token) and c.type == "QUOTED_STRING"]
        description = _unquote(quoted[3]) if len(quoted) > 3 else None
        obj = MibObject(
            name=name,
            oid=oid_str,
            oid_path=oid_path,
            object_type="MODULE-IDENTITY",
            oid_parent=oid_parent,
            description=description,
        )
        result: list[Any] = [obj]
        if len(quoted) > 0:
            result.append(_LastUpdatedInfo(_unquote(quoted[0])))
        if len(quoted) > 1:
            result.append(_OrganizationInfo(_unquote(quoted[1])))
        if len(quoted) > 2:
            result.append(_ContactInfo(_unquote(quoted[2])))
        result.extend(c for c in children if isinstance(c, _RevisionInfo))
        return result

    # ------------------------------------------------------------------
    # OBJECT-IDENTITY
    # ------------------------------------------------------------------

    def object_identity_assignment(self, children: list[Any]) -> MibObject:
        name = str(children[0])
        oid_str, oid_path, oid_parent = _resolve_oid(self._oid(children))
        return MibObject(
            name=name,
            oid=oid_str,
            oid_path=oid_path,
            object_type="OBJECT-IDENTITY",
            status=self._status(children),
            description=self._description(children),
            oid_parent=oid_parent,
        )

    # ------------------------------------------------------------------
    # OBJECT-TYPE
    # ------------------------------------------------------------------

    def object_type_assignment(self, children: list[Any]) -> MibObject:
        name = str(children[0])
        oid_str, oid_path, oid_parent = _resolve_oid(self._oid(children))
        index_info = next((c for c in children if isinstance(c, _IndexInfo)), None)
        augments_info = next((c for c in children if isinstance(c, _AugmentsInfo)), None)
        syntax_info = next((c for c in children if isinstance(c, _SyntaxInfo)), None)
        constraint = syntax_info.constraint if syntax_info else None
        return MibObject(
            name=name,
            oid=oid_str,
            oid_path=oid_path,
            object_type="OBJECT-TYPE",
            syntax=self._syntax(children),
            max_access=self._access(children),
            status=self._status(children),
            description=self._description(children),
            index=index_info.columns if index_info else None,
            augments=augments_info.row if augments_info else None,
            oid_parent=oid_parent,
            constraints=constraint.to_dict() if constraint else None,
        )

    # ------------------------------------------------------------------
    # NOTIFICATION-TYPE
    # ------------------------------------------------------------------

    def notification_type_assignment(self, children: list[Any]) -> MibObject:
        name = str(children[0])
        oid_str, oid_path, oid_parent = _resolve_oid(self._oid(children))
        return MibObject(
            name=name,
            oid=oid_str,
            oid_path=oid_path,
            object_type="NOTIFICATION-TYPE",
            status=self._status(children),
            description=self._description(children),
            oid_parent=oid_parent,
            members=self._members(children),
        )

    # ------------------------------------------------------------------
    # TEXTUAL-CONVENTION
    # ------------------------------------------------------------------

    def textual_convention_assignment(self, children: list[Any]) -> MibType:
        name = str(children[0])
        display_hint = next((c.value for c in children if isinstance(c, _DisplayHintInfo)), None)
        # Constraint may be a direct child (integer_enum via constraint rule) or
        # embedded in _SyntaxInfo (size_constraint on OCTET STRING / named_type).
        constraint: _ConstraintInfo | None = next(
            (c for c in children if isinstance(c, _ConstraintInfo)), None
        )
        if constraint is None:
            syntax_node = next((c for c in children if isinstance(c, _SyntaxInfo)), None)
            if syntax_node is not None:
                constraint = syntax_node.constraint
        return MibType(
            name=name,
            base_type=self._syntax(children) or "",
            description=self._description(children),
            display_hint=display_hint,
            status=self._status(children),
            constraints=constraint.to_dict() if constraint else None,
        )

    # ------------------------------------------------------------------
    # Type / Value assignments
    # ------------------------------------------------------------------

    def type_assignment(self, children: list[Any]) -> MibType:
        name = str(children[0])
        syntax_node = next((c for c in children if isinstance(c, _SyntaxInfo)), None)
        return MibType(
            name=name,
            base_type=syntax_node.value if syntax_node is not None else "",
            constraints=syntax_node.constraint.to_dict()
            if syntax_node is not None and syntax_node.constraint is not None
            else None,
        )

    def value_assignment(self, children: list[Any]) -> MibObject | None:
        name = str(children[0])
        oid_list = self._oid(children)
        if oid_list:
            oid_str, oid_path, oid_parent = _resolve_oid(oid_list)
            return MibObject(
                name=name,
                oid=oid_str,
                oid_path=oid_path,
                object_type="OBJECT IDENTIFIER",
                oid_parent=oid_parent,
            )
        return None

    # ------------------------------------------------------------------
    # GROUP / COMPLIANCE / CAPABILITIES
    # ------------------------------------------------------------------

    def object_group_assignment(self, children: list[Any]) -> MibObject:
        obj = self._simple_oid_object(children, "OBJECT-GROUP")
        obj.members = self._members(children)
        return obj

    def notification_group_assignment(self, children: list[Any]) -> MibObject:
        obj = self._simple_oid_object(children, "NOTIFICATION-GROUP")
        obj.members = self._members(children)
        return obj

    def module_compliance_assignment(self, children: list[Any]) -> MibObject:
        obj = self._simple_oid_object(children, "MODULE-COMPLIANCE")
        # Flatten items from compliance_module lists into a single sequence.
        flat: list[Any] = []
        for c in children:
            if isinstance(c, list):
                flat.extend(c)
            else:
                flat.append(c)
        seen: set[str] = {obj.name}  # exclude the object's own name
        groups: list[str] = []
        for c in flat:
            if isinstance(c, _MembersInfo):
                for name in c.names:
                    if name not in seen:
                        seen.add(name)
                        groups.append(name)
            elif isinstance(c, str) and c not in seen:
                seen.add(c)
                groups.append(c)
        obj.members = groups or None
        return obj

    def agent_capabilities_assignment(self, children: list[Any]) -> MibObject:
        return self._simple_oid_object(children, "AGENT-CAPABILITIES")

    def trap_type_assignment(self, children: list[Any]) -> MibObject:
        name = str(children[0])
        number = next(
            (str(c) for c in children if isinstance(c, Token) and c.type == "NUMBER"), "0"
        )
        return MibObject(
            name=name,
            oid=number,
            oid_path=[int(number)],
            object_type="TRAP-TYPE",
            members=self._members(children),
        )

    def assignment(self, children: list[Any]) -> Any:
        return children[0] if children else None

    # ------------------------------------------------------------------
    # Clause wrappers — typed info objects
    # ------------------------------------------------------------------

    def description_clause(self, children: list[Any]) -> _DescriptionInfo:
        return _DescriptionInfo(_unquote(children[0]))

    def max_access_clause(self, children: list[Any]) -> _AccessInfo:
        info = next((c for c in children if isinstance(c, _AccessInfo)), None)
        return info if info is not None else _AccessInfo(str(children[0]))

    def index_clause(self, children: list[Any]) -> Any:
        return children[0] if children else None

    def index_part(self, children: list[Any]) -> _IndexInfo:
        info = next((c for c in children if isinstance(c, _IndexInfo)), None)
        return info if info is not None else _IndexInfo([])

    def index_list(self, children: list[Any]) -> _IndexInfo:
        return _IndexInfo([c for c in children if isinstance(c, str)])

    def index_item(self, children: list[Any]) -> str:
        return str(children[-1])

    def augments_part(self, children: list[Any]) -> _AugmentsInfo:
        return _AugmentsInfo(str(children[0]))

    def revision(self, children: list[Any]) -> _RevisionInfo:
        quoted = [c for c in children if isinstance(c, Token) and c.type == "QUOTED_STRING"]
        date = _unquote(quoted[0]) if quoted else ""
        desc = _unquote(quoted[1]) if len(quoted) > 1 else ""
        return _RevisionInfo(date=date, description=desc)

    def compliance_module(self, children: list[Any]) -> list[Any]:
        # Pass through _MembersInfo (from mandatory_groups) and str (from compliance_group).
        return [c for c in children if isinstance(c, (_MembersInfo, str))]

    def mandatory_groups(self, children: list[Any]) -> _MembersInfo:
        names = next((c for c in children if isinstance(c, list)), [])
        return _MembersInfo(names)

    def compliance_item(self, children: list[Any]) -> Any:
        return children[0] if children else None

    def compliance_group(self, children: list[Any]) -> str:
        return str(children[0])

    def compliance_object(self, _: list[Any]) -> None:
        return None

    def capabilities_module(self, _: list[Any]) -> None:
        return None

    def variation(self, _: list[Any]) -> None:
        return None

    def trap_variables_clause(self, children: list[Any]) -> _MembersInfo:
        return _MembersInfo(children[0] if children else [])

    def units_clause(self, _: list[Any]) -> None:
        return None

    def reference_clause(self, _: list[Any]) -> None:
        return None

    def display_hint_clause(self, children: list[Any]) -> _DisplayHintInfo:
        return _DisplayHintInfo(_unquote(children[0]))

    def defval_clause(self, _: list[Any]) -> None:
        return None

    def objects_clause(self, children: list[Any]) -> _MembersInfo:
        return _MembersInfo(children[0] if children else [])

    def scalar_value(self, _: list[Any]) -> None:
        return None

    def write_syntax_clause(self, _: list[Any]) -> None:
        return None

    def min_access_clause(self, _: list[Any]) -> None:
        return None

    def access_clause(self, _: list[Any]) -> None:
        return None

    def creation_requires_clause(self, _: list[Any]) -> None:
        return None

    def object_list(self, children: list[Any]) -> list[str]:
        return [str(c) for c in children]

    # ------------------------------------------------------------------
    # OID passthrough
    # ------------------------------------------------------------------

    def oid_value(self, children: list[Any]) -> list[Any]:
        return children

    def named_arc(self, children: list[Any]) -> Tree[Any]:
        return Tree("named_arc", children)

    def name_arc(self, children: list[Any]) -> Tree[Any]:
        return Tree("name_arc", children)

    def number_arc(self, children: list[Any]) -> Tree[Any]:
        return Tree("number_arc", children)

    # ------------------------------------------------------------------
    # Syntax type rules — each returns _SyntaxInfo
    # ------------------------------------------------------------------

    def syntax_type(self, children: list[Any]) -> _SyntaxInfo:
        val = children[0] if children else ""
        return val if isinstance(val, _SyntaxInfo) else _SyntaxInfo(str(val))

    def integer_type(self, children: list[Any]) -> _SyntaxInfo:
        c = next((x for x in children if isinstance(x, _ConstraintInfo)), None)
        return _SyntaxInfo("INTEGER", constraint=c)

    def octet_string_type(self, children: list[Any]) -> _SyntaxInfo:
        c = next((x for x in children if isinstance(x, _ConstraintInfo)), None)
        return _SyntaxInfo("OCTET STRING", constraint=c)

    def oid_type(self, _: list[Any]) -> _SyntaxInfo:
        return _SyntaxInfo("OBJECT IDENTIFIER")

    def null_type(self, _: list[Any]) -> _SyntaxInfo:
        return _SyntaxInfo("NULL")

    def ip_address_type(self, _: list[Any]) -> _SyntaxInfo:
        return _SyntaxInfo("IpAddress")

    def counter32_type(self, children: list[Any]) -> _SyntaxInfo:
        c = next((x for x in children if isinstance(x, _ConstraintInfo)), None)
        return _SyntaxInfo("Counter32", constraint=c)

    def counter64_type(self, children: list[Any]) -> _SyntaxInfo:
        c = next((x for x in children if isinstance(x, _ConstraintInfo)), None)
        return _SyntaxInfo("Counter64", constraint=c)

    def gauge32_type(self, children: list[Any]) -> _SyntaxInfo:
        c = next((x for x in children if isinstance(x, _ConstraintInfo)), None)
        return _SyntaxInfo("Gauge32", constraint=c)

    def unsigned32_type(self, children: list[Any]) -> _SyntaxInfo:
        c = next((x for x in children if isinstance(x, _ConstraintInfo)), None)
        return _SyntaxInfo("Unsigned32", constraint=c)

    def timeticks_type(self, children: list[Any]) -> _SyntaxInfo:
        c = next((x for x in children if isinstance(x, _ConstraintInfo)), None)
        return _SyntaxInfo("TimeTicks", constraint=c)

    def opaque_type(self, children: list[Any]) -> _SyntaxInfo:
        c = next((x for x in children if isinstance(x, _ConstraintInfo)), None)
        return _SyntaxInfo("Opaque", constraint=c)

    def integer32_type(self, children: list[Any]) -> _SyntaxInfo:
        c = next((x for x in children if isinstance(x, _ConstraintInfo)), None)
        return _SyntaxInfo("Integer32", constraint=c)

    def network_address_type(self, _: list[Any]) -> _SyntaxInfo:
        return _SyntaxInfo("NetworkAddress")

    def counter_type(self, _: list[Any]) -> _SyntaxInfo:
        return _SyntaxInfo("Counter")

    def gauge_type(self, _: list[Any]) -> _SyntaxInfo:
        return _SyntaxInfo("Gauge")

    def bits_type(self, _: list[Any]) -> _SyntaxInfo:
        return _SyntaxInfo("BITS")

    def sequence_type(self, _: list[Any]) -> _SyntaxInfo:
        return _SyntaxInfo("SEQUENCE")

    def choice_type(self, _: list[Any]) -> _SyntaxInfo:
        return _SyntaxInfo("CHOICE")

    def named_type(self, children: list[Any]) -> _SyntaxInfo:
        c = next((x for x in children if isinstance(x, _ConstraintInfo)), None)
        return _SyntaxInfo(str(children[0]), constraint=c)

    def sequence_of_type(self, children: list[Any]) -> _SyntaxInfo:
        return _SyntaxInfo(f"SEQUENCE OF {children[0]}")

    def tagged_type(self, children: list[Any]) -> _SyntaxInfo:
        info = next((child for child in children if isinstance(child, _SyntaxInfo)), None)
        return info if info is not None else _SyntaxInfo("")

    def tag(self, _: list[Any]) -> None:
        return None

    def tag_class(self, _: list[Any]) -> None:
        return None

    def tag_mode(self, _: list[Any]) -> None:
        return None

    def status_value(self, children: list[Any]) -> _StatusInfo:
        return _StatusInfo(str(children[0]))

    def smiv1_status_value(self, children: list[Any]) -> _StatusInfo:
        return _StatusInfo(str(children[0]))

    def access_value(self, children: list[Any]) -> _AccessInfo:
        return _AccessInfo(str(children[0]))

    def syntax_clause(self, children: list[Any]) -> _SyntaxInfo | None:
        return next((c for c in children if isinstance(c, _SyntaxInfo)), None)

    # ------------------------------------------------------------------
    # Constraint handlers
    # ------------------------------------------------------------------

    def constraint(self, children: list[Any]) -> _ConstraintInfo:
        # child is either a list-of-ranges (from range_items) or _ConstraintInfo (from integer_enum)
        for child in children:
            if isinstance(child, _ConstraintInfo):
                return child
            if isinstance(child, list):
                # list of [low, high] pairs from range_items
                if len(child) == 1:
                    return _ConstraintInfo(kind="range", data=child)
                return _ConstraintInfo(
                    kind="union", data=[_ConstraintInfo(kind="range", data=[r]) for r in child]
                )
        return _ConstraintInfo(kind="range", data=[])

    def size_constraint(self, children: list[Any]) -> _ConstraintInfo:
        for child in children:
            if isinstance(child, list):
                if len(child) == 1:
                    return _ConstraintInfo(kind="size", data=child)
                return _ConstraintInfo(
                    kind="union", data=[_ConstraintInfo(kind="size", data=[r]) for r in child]
                )
        return _ConstraintInfo(kind="size", data=[])

    def range_items(self, children: list[Any]) -> list[list[int | str]]:
        return [c for c in children if isinstance(c, list)]

    def range(self, children: list[Any]) -> list[int | str]:
        # range_bound ".." range_bound → [low, high]
        return [children[0], children[1]]

    def single_value(self, children: list[Any]) -> list[int | str]:
        # single range_bound → [val, val]
        return [children[0], children[0]]

    def range_bound(self, children: list[Any]) -> int | str:
        val = str(children[0])
        if val in ("MIN", "MAX"):
            return val
        if val.startswith("'") and val[-1:].upper() == "H":
            return int(val[1:-2], 16)
        try:
            return int(val)
        except ValueError:
            return val

    def integer_enum(self, children: list[Any]) -> _ConstraintInfo:
        items = next((c for c in children if isinstance(c, list)), [])
        return _ConstraintInfo(kind="enum", data=items)

    def enum_items(self, children: list[Any]) -> list[list[Any]]:
        return [c for c in children if isinstance(c, list)]

    def enum_item(self, children: list[Any]) -> list[Any]:
        return [str(children[0]), int(str(children[1]))]

    def named_bits(self, children: list[Any]) -> _ConstraintInfo:
        items = [c for c in children if isinstance(c, list)]
        return _ConstraintInfo(kind="bits", data=items)

    def named_bit(self, children: list[Any]) -> list[Any]:
        return [str(children[0]), int(str(children[1]))]

    # ------------------------------------------------------------------
    # Private typed extractors
    # ------------------------------------------------------------------

    def _oid(self, children: list[Any]) -> list[Any]:
        for child in children:
            if isinstance(child, list) and child and isinstance(child[0], Tree):
                return child
        return []

    def _syntax(self, children: list[Any]) -> str | None:
        info = next((c for c in children if isinstance(c, _SyntaxInfo)), None)
        return info.value if info else None

    def _description(self, children: list[Any]) -> str | None:
        info = next((c for c in children if isinstance(c, _DescriptionInfo)), None)
        return info.value if info else None

    def _status(self, children: list[Any]) -> str | None:
        info = next((c for c in children if isinstance(c, _StatusInfo)), None)
        return info.value if info else None

    def _access(self, children: list[Any]) -> str | None:
        info = next((c for c in children if isinstance(c, _AccessInfo)), None)
        return info.value if info else None

    def _members(self, children: list[Any]) -> list[str] | None:
        """Extract member list from children: _MembersInfo (from objects_clause /
        trap_variables_clause) or a plain list[str] (from inline object_list in
        OBJECT-GROUP / NOTIFICATION-GROUP grammar rules)."""
        mi = next((c for c in children if isinstance(c, _MembersInfo)), None)
        if mi is not None:
            return mi.names or None
        # object_list returns list[str]; OID components are non-str — check first element
        plain = next(
            (c for c in children if isinstance(c, list) and (not c or isinstance(c[0], str))),
            None,
        )
        return plain or None

    def _simple_oid_object(self, children: list[Any], object_type: str) -> MibObject:
        name = str(children[0])
        oid_str, oid_path, oid_parent = _resolve_oid(self._oid(children))
        return MibObject(
            name=name,
            oid=oid_str,
            oid_path=oid_path,
            object_type=object_type,
            oid_parent=oid_parent,
            status=self._status(children),
            description=self._description(children),
        )
