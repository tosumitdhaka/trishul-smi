"""Lark Transformer: converts a parsed Tree into a MibModule dataclass."""
from __future__ import annotations

from lark import Transformer, Token, Tree
from lark.visitors import v_args

from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.models.mib_type import MibType


def _str(token: Token | str) -> str:
    return str(token).strip('"')


def _unquote(token: Token | str) -> str:
    s = str(token)
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('\\"', '"')
    return s


def _resolve_oid(components: list) -> tuple[str, list[int]]:
    """Convert oid_component list into (dotted_string, int_path).
    Named arcs without a number (e.g. 'enterprises') are left as-is in the
    dotted string and skipped in the int path (resolved at a later stage).
    """
    parts_str: list[str] = []
    parts_int: list[int] = []
    for comp in components:
        if isinstance(comp, Tree):
            if comp.data == "named_arc":
                # LOWER_ID ( NUMBER )
                num = int(str(comp.children[1]))
                parts_str.append(str(num))
                parts_int.append(num)
            elif comp.data == "name_arc":
                parts_str.append(str(comp.children[0]))
                # can't convert to int yet — skip
            elif comp.data == "number_arc":
                num = int(str(comp.children[0]))
                parts_str.append(str(num))
                parts_int.append(num)
    return ".".join(parts_str), parts_int


class MibTransformer(Transformer):
    """Walks the Lark parse tree and builds a MibModule."""

    # ------------------------------------------------------------------
    # Module-level
    # ------------------------------------------------------------------

    def start(self, children: list) -> MibModule:
        return children[0]

    def module_definition(self, children: list) -> MibModule:
        module_name: str = ""
        imports: dict[str, list[str]] = {}
        objects: dict[str, MibObject] = {}
        types: dict[str, MibType] = {}
        notifications: dict[str, MibObject] = {}

        for child in children:
            if isinstance(child, str):
                module_name = child
            elif isinstance(child, dict) and "__imports__" in child:
                imports = child["__imports__"]
            elif isinstance(child, MibObject):
                if child.object_type == "NOTIFICATION-TYPE":
                    notifications[child.name] = child
                else:
                    objects[child.name] = child
            elif isinstance(child, MibType):
                types[child.name] = child
            # Skip None (unhandled assignments)

        # Detect language from imports
        language = "SMIv1"
        smiv2_markers = {"SNMPv2-SMI", "SNMPv2-TC", "SNMPv2-CONF"}
        if any(m in imports for m in smiv2_markers):
            language = "SMIv2"

        return MibModule(
            name=module_name,
            language=language,  # type: ignore[arg-type]
            imports=imports,
            objects=objects,
            types=types,
            notifications=notifications,
        )

    def module_name(self, children: list) -> str:
        return str(children[0])

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def imports(self, children: list) -> dict:
        result: dict[str, list[str]] = {}
        for clause in children:
            if isinstance(clause, dict):
                result.update(clause)
        return {"__imports__": result}

    def import_clause(self, children: list) -> dict[str, list[str]]:
        symbols: list[str] = children[0]
        module: str = str(children[1])
        return {module: symbols}

    def symbol_list(self, children: list) -> list[str]:
        return [str(c) for c in children]

    def symbol(self, children: list) -> str:
        return str(children[0])

    def module_ref(self, children: list) -> str:
        return str(children[0])

    # ------------------------------------------------------------------
    # MODULE-IDENTITY
    # ------------------------------------------------------------------

    def module_identity_assignment(self, children: list) -> MibObject:
        name = str(children[0])
        oid_str, oid_path = _resolve_oid(self._find_oid(children))
        return MibObject(
            name=name,
            oid=oid_str,
            oid_path=oid_path,
            object_type="MODULE-IDENTITY",
        )

    # ------------------------------------------------------------------
    # OBJECT-IDENTITY
    # ------------------------------------------------------------------

    def object_identity_assignment(self, children: list) -> MibObject:
        name = str(children[0])
        status = self._find_token(children, "status_value")
        description = self._find_quoted(children, offset=1)
        oid_str, oid_path = _resolve_oid(self._find_oid(children))
        return MibObject(
            name=name,
            oid=oid_str,
            oid_path=oid_path,
            object_type="OBJECT-IDENTITY",
            status=status,
            description=description,
        )

    # ------------------------------------------------------------------
    # OBJECT-TYPE
    # ------------------------------------------------------------------

    def object_type_assignment(self, children: list) -> MibObject:
        name = str(children[0])
        syntax = self._find_syntax(children)
        access = self._find_access(children)
        status = self._find_token(children, "status_value") or self._find_token(children, "smiv1_status_value")
        description = self._find_quoted(children, offset=0)
        oid_str, oid_path = _resolve_oid(self._find_oid(children))
        index = self._find_index(children)
        augments = self._find_augments(children)
        return MibObject(
            name=name,
            oid=oid_str,
            oid_path=oid_path,
            object_type="OBJECT-TYPE",
            syntax=syntax,
            max_access=access,
            status=status,
            description=description,
            index=index,
            augments=augments,
        )

    # ------------------------------------------------------------------
    # NOTIFICATION-TYPE
    # ------------------------------------------------------------------

    def notification_type_assignment(self, children: list) -> MibObject:
        name = str(children[0])
        status = self._find_token(children, "status_value")
        description = self._find_quoted(children, offset=0)
        oid_str, oid_path = _resolve_oid(self._find_oid(children))
        return MibObject(
            name=name,
            oid=oid_str,
            oid_path=oid_path,
            object_type="NOTIFICATION-TYPE",
            status=status,
            description=description,
        )

    # ------------------------------------------------------------------
    # TEXTUAL-CONVENTION
    # ------------------------------------------------------------------

    def textual_convention_assignment(self, children: list) -> MibType:
        name = str(children[0])
        status = self._find_token(children, "status_value")
        description = self._find_quoted(children, offset=0)
        syntax = self._find_syntax(children)
        return MibType(
            name=name,
            base_type=syntax or "",
            description=description,
        )

    # ------------------------------------------------------------------
    # Type assignment (TypeName ::= SomeBaseType)
    # ------------------------------------------------------------------

    def type_assignment(self, children: list) -> MibType:
        name = str(children[0])
        syntax = self._find_syntax(children)
        return MibType(name=name, base_type=syntax or "")

    # ------------------------------------------------------------------
    # Value assignment (name OBJECT IDENTIFIER ::= { ... })
    # ------------------------------------------------------------------

    def value_assignment(self, children: list) -> MibObject | None:
        name = str(children[0])
        oid_list = self._find_oid(children)
        if oid_list:
            oid_str, oid_path = _resolve_oid(oid_list)
            return MibObject(
                name=name,
                oid=oid_str,
                oid_path=oid_path,
                object_type="OBJECT IDENTIFIER",
            )
        return None

    # ------------------------------------------------------------------
    # GROUP / COMPLIANCE / CAPABILITIES — collect as plain MibObjects
    # ------------------------------------------------------------------

    def object_group_assignment(self, children: list) -> MibObject:
        return self._simple_oid_object(children, "OBJECT-GROUP")

    def notification_group_assignment(self, children: list) -> MibObject:
        return self._simple_oid_object(children, "NOTIFICATION-GROUP")

    def module_compliance_assignment(self, children: list) -> MibObject:
        return self._simple_oid_object(children, "MODULE-COMPLIANCE")

    def agent_capabilities_assignment(self, children: list) -> MibObject:
        return self._simple_oid_object(children, "AGENT-CAPABILITIES")

    def trap_type_assignment(self, children: list) -> MibObject:
        name = str(children[0])
        # TRAP-TYPE ::= INTEGER; use the integer as a pseudo-OID
        number = next((str(c) for c in children if isinstance(c, Token) and c.type == "NUMBER"), "0")
        return MibObject(
            name=name,
            oid=number,
            oid_path=[int(number)],
            object_type="TRAP-TYPE",
        )

    def assignment(self, children: list):
        return children[0] if children else None

    # ------------------------------------------------------------------
    # OID value passthrough
    # ------------------------------------------------------------------

    def oid_value(self, children: list) -> list:
        return children  # list of Tree nodes (named_arc / name_arc / number_arc)

    def named_arc(self, children: list) -> Tree:
        return Tree("named_arc", children)

    def name_arc(self, children: list) -> Tree:
        return Tree("name_arc", children)

    def number_arc(self, children: list) -> Tree:
        return Tree("number_arc", children)

    # ------------------------------------------------------------------
    # Syntax helpers — return human-readable type string
    # ------------------------------------------------------------------

    def syntax_type(self, children: list) -> str:
        return str(children[0]) if children else ""

    def integer_type(self, _):    return "INTEGER"
    def octet_string_type(self, _): return "OCTET STRING"
    def oid_type(self, _):        return "OBJECT IDENTIFIER"
    def null_type(self, _):       return "NULL"
    def ip_address_type(self, _): return "IpAddress"
    def counter32_type(self, _):  return "Counter32"
    def counter64_type(self, _):  return "Counter64"
    def gauge32_type(self, _):    return "Gauge32"
    def unsigned32_type(self, _): return "Unsigned32"
    def timeticks_type(self, _):  return "TimeTicks"
    def opaque_type(self, _):     return "Opaque"
    def integer32_type(self, _):  return "Integer32"
    def network_address_type(self, _): return "NetworkAddress"
    def counter_type(self, _):    return "Counter"   # SMIv1
    def gauge_type(self, _):      return "Gauge"     # SMIv1
    def named_type(self, children: list) -> str:
        return str(children[0])
    def sequence_of_type(self, children: list) -> str:
        return f"SEQUENCE OF {children[0]}"
    def sequence_type(self, _):   return "SEQUENCE"
    def bits_type(self, _):       return "BITS"
    def choice_type(self, _):     return "CHOICE"

    def status_value(self, children: list) -> str:
        return str(children[0])

    def smiv1_status_value(self, children: list) -> str:
        return str(children[0])

    def access_value(self, children: list) -> str:
        return str(children[0])

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_oid(self, children: list) -> list:
        for child in children:
            if isinstance(child, list) and child and isinstance(child[0], Tree):
                return child
        return []

    def _find_syntax(self, children: list) -> str | None:
        for child in children:
            if isinstance(child, str) and child not in ("", "current", "deprecated",
                    "obsolete", "mandatory", "optional",
                    "read-only", "read-write", "read-create",
                    "not-accessible", "accessible-for-notify", "write-only"):
                # heuristic: first plain string that isn’t a keyword is the syntax
                if not child.startswith('"') and child[0].isupper() or child in (
                    "INTEGER", "OCTET STRING", "OBJECT IDENTIFIER", "NULL",
                    "IpAddress", "Counter32", "Counter64", "Gauge32",
                    "Unsigned32", "TimeTicks", "Opaque", "Integer32",
                    "NetworkAddress", "Counter", "Gauge", "SEQUENCE", "BITS",
                ) or " OF " in child:
                    return child
        return None

    def _find_access(self, children: list) -> str | None:
        access_vals = {"read-only", "read-write", "read-create",
                       "not-accessible", "accessible-for-notify", "write-only"}
        for child in children:
            if isinstance(child, str) and child in access_vals:
                return child
        return None

    def _find_token(self, children: list, rule: str) -> str | None:
        status_vals = {"current", "deprecated", "obsolete", "mandatory", "optional"}
        for child in children:
            if isinstance(child, str) and child in status_vals:
                return child
        return None

    def _find_quoted(self, children: list, offset: int = 0) -> str | None:
        quoted = [c for c in children if isinstance(c, str) and c.startswith('"')]
        idx = offset
        if idx < len(quoted):
            return _unquote(quoted[idx])
        return None

    def _find_index(self, children: list) -> list[str] | None:
        for child in children:
            if isinstance(child, Tree) and child.data == "index_part":
                return [str(c.children[0]) for c in child.children
                        if isinstance(c, Tree) and c.data == "index_item"]
        return None

    def _find_augments(self, children: list) -> str | None:
        for child in children:
            if isinstance(child, Tree) and child.data == "augments_part":
                return str(child.children[0])
        return None

    def _simple_oid_object(self, children: list, object_type: str) -> MibObject:
        name = str(children[0])
        oid_str, oid_path = _resolve_oid(self._find_oid(children))
        return MibObject(name=name, oid=oid_str, oid_path=oid_path, object_type=object_type)
