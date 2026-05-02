# MIB Compiler Output Comparison — Sample Objects

> **Compilers:** pysmi 1.5.11, pysmi 2.0.0, trishul-smi 0.2.0  
> **Source MIB:** IF-MIB (IF-MIB RFC 2863)  
> **Objects shown:** `ifDescr` (OBJECT-TYPE), `linkDown` (NOTIFICATION-TYPE)  
> **Last updated:** 2026-05-02

Both JSON (texts enabled) and pysnmp `.py` output are shown side-by-side for each object. All three compilers were run against the same local MIB file with no network access.

---

## 1. `ifDescr` — OBJECT-TYPE column

### JSON output

**pysmi 1.5 and pysmi 2.0** produce identical output for this object:

```json
{
  "class": "objecttype",
  "description": "A textual string containing information about the\n            interface.  This string should include the name of the\n            manufacturer, the product name and the version of the\n            interface hardware/software.",
  "maxaccess": "read-only",
  "name": "ifDescr",
  "nodetype": "column",
  "oid": "1.3.6.1.2.1.2.2.1.2",
  "status": "current",
  "syntax": {
    "class": "type",
    "constraints": {
      "size": [
        {
          "max": 255,
          "min": 0
        }
      ]
    },
    "type": "DisplayString"
  }
}
```

**trishul-smi:**

```json
{
  "oid": "1.3.6.1.2.1.2.2.1.2",
  "oid_path": [1, 3, 6, 1, 2, 1, 2, 2, 1, 2],
  "object_type": "OBJECT-TYPE",
  "syntax": "DisplayString",
  "max_access": "read-only",
  "status": "current",
  "description": "A textual string containing information about the interface. This string should include the name of the manufacturer, the product name and the version of the interface hardware/software.",
  "index": null,
  "augments": null
}
```

**Differences:**

| Field | pysmi 1.5 / 2.0 | trishul-smi |
|---|---|---|
| Schema style | Flat root-level entry; `"class"` discriminator | Uniform field set; `"object_type"` discriminator |
| `oid_path` | Absent | Present as integer array |
| `nodetype` | `"column"` | Absent |
| `syntax` | Nested object with `class`, `constraints`, `type` | Flat string `"DisplayString"` |
| Constraints | Inline under `syntax.constraints` | Absent from objects; found in `types` section |
| `index` / `augments` | Absent | Always present (null for non-row objects) |
| Description whitespace | Original `\n` indentation preserved | Normalised to single spaces |

### pysnmp output

**pysmi 1.5 and pysmi 2.0** produce identical `.py` output:

```python
class _IfDescr_Type(DisplayString):
    """Custom type ifDescr based on DisplayString"""
    subtypeSpec = DisplayString.subtypeSpec
    subtypeSpec += ConstraintsUnion(
        ValueSizeConstraint(0, 255),
    )

_IfDescr_Type.__name__ = "DisplayString"
_IfDescr_Object = MibTableColumn
ifDescr = _IfDescr_Object(
    (1, 3, 6, 1, 2, 1, 2, 2, 1, 2),
    _IfDescr_Type()
)
ifDescr.setMaxAccess("read-only")
if mibBuilder.loadTexts:
    ifDescr.setStatus("current")
```

**trishul-smi:**

```python
class _ifDescr_Type(DisplayString):
    subtypeSpec = DisplayString.subtypeSpec
    subtypeSpec += ConstraintsUnion(
        ValueSizeConstraint(0, 255),
    )

_ifDescr_Type.__name__ = "DisplayString"

ifDescr = MibTableColumn(
    (1, 3, 6, 1, 2, 1, 2, 2, 1, 2,),
    _ifDescr_Type()
).setMaxAccess('read-only')
if mibBuilder.loadTexts: ifDescr.setStatus('current')

if mibBuilder.loadTexts: ifDescr.setDescription(
    """A textual string containing information about the
                interface.  This string should include the name of the
                manufacturer, the product name and the version of the
                interface hardware/software."""
)
```

**Differences:**

| Aspect | pysmi 1.5 / 2.0 | trishul-smi |
|---|---|---|
| Type class naming | `_IfDescr_Type` (PascalCase) | `_ifDescr_Type` (camelCase) |
| Intermediate `_Object` variable | Yes (`_IfDescr_Object = MibTableColumn`) | No — direct instantiation |
| Fluent `.setMaxAccess()` | Separate statement | Chained on constructor |
| `setDescription()` call | Absent (no `--generate-mib-texts` equivalent in pysnmp mode) | Always emitted (use `--no-texts` to suppress) |

---

## 2. `linkDown` — NOTIFICATION-TYPE

### JSON output

**pysmi 1.5 and pysmi 2.0** produce identical output:

```json
{
  "class": "notificationtype",
  "description": "A linkDown trap signifies that the SNMP entity, acting in\n            an agent role, has detected that the ifOperStatus object for\n            one of its communication links is about to enter the down\n            state from some other state (but not from the notPresent\n            state).  This other state is indicated by the included value\n            of ifOperStatus.",
  "name": "linkDown",
  "objects": [
    { "module": "IF-MIB", "object": "ifIndex" },
    { "module": "IF-MIB", "object": "ifAdminStatus" },
    { "module": "IF-MIB", "object": "ifOperStatus" }
  ],
  "oid": "1.3.6.1.6.3.1.1.5.3",
  "status": "current"
}
```

**trishul-smi:**

```
linkDown: null   (absent from the objects dict)
```

trishul-smi does not emit `NOTIFICATION-TYPE` objects in JSON output. Only the `NOTIFICATION-GROUP` (`linkUpDownNotificationsGroup`) is present, without its member list.

**Impact:** Any consumer that looks up trap OIDs from JSON output — NMS platforms, trap decoders, documentation generators — will find no entry for `linkDown` or `linkUp`. This is the most significant correctness gap in trishul-smi's JSON format.

### pysnmp output

**pysmi 1.5 and pysmi 2.0:**

```python
# Notification objects

linkDown = NotificationType(
    (1, 3, 6, 1, 6, 3, 1, 1, 5, 3)
)
linkDown.setObjects(
      *(("IF-MIB", "ifIndex"),
        ("IF-MIB", "ifAdminStatus"),
        ("IF-MIB", "ifOperStatus"))
)
if mibBuilder.loadTexts:
    linkDown.setStatus(
        "current"
    )
```

**trishul-smi:**

```python
# --- NOTIFICATION-TYPE ------------------------------------------------

linkDown = NotificationType(
    (1, 3, 6, 1, 6, 3, 1, 1, 5, 3,)
)  # TODO: add .setObjects() from OBJECTS clause
if mibBuilder.loadTexts: linkDown.setStatus('current')

if mibBuilder.loadTexts: linkDown.setDescription(
    """A linkDown trap signifies that the SNMP entity, acting in
                an agent role, has detected that the ifOperStatus object for
                one of its communication links is about to enter the down
                state from some other state (but not from the notPresent
                state).  This other state is indicated by the included value
                of ifOperStatus."""
)
```

**Differences:**

| Aspect | pysmi 1.5 / 2.0 | trishul-smi |
|---|---|---|
| Object created | Yes | Yes |
| `.setObjects()` with bound varbinds | **Yes** — `ifIndex`, `ifAdminStatus`, `ifOperStatus` | **No** — left as `# TODO` comment |
| `setDescription()` | Absent | Present |

The missing `.setObjects()` call in trishul-smi's pysnmp output is a confirmed bug (the `TODO` comment in the template acknowledges it). A pysnmp runtime loading this module will have a `linkDown` notification with no varbind definitions — traps sent using it will carry no variable bindings.

---

## Summary Table

| Feature | pysmi 1.5 | pysmi 2.0 | trishul-smi |
|---|---|---|---|
| `ifDescr` in JSON | ✅ Full; nested syntax constraints | ✅ Identical to 1.5 | ✅ Present; flat syntax string; adds `oid_path` |
| `linkDown` in JSON | ✅ Present with `objects` list | ✅ Identical to 1.5 | ❌ Absent |
| `ifDescr` pysnmp | ✅ `_Type` subclass + `subtypeSpec` | ✅ Identical to 1.5 | ✅ Same pattern; lowercase naming; adds `setDescription` |
| `linkDown` pysnmp | ✅ `.setObjects()` wired | ✅ Identical to 1.5 | ⚠️ Object created; `.setObjects()` missing (TODO) |
