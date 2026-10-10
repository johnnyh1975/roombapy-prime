"""vendor_reference: the shipped extract of the vendor's value sets.

The module ships in the package and is the source guards compare value
sets against, yet no test loaded it: it stood at 0 % coverage until
0.4.0. These pin what it answers and, above all, that a wrong name is an
error rather than a quiet None -- the property its docstring exists for.
"""
from __future__ import annotations

import pytest

from roombapy_prime import vendor_reference as vr


def test_an_enum_maps_member_names_to_wire_values() -> None:
    assert vr.enum_values("DryDurType")["four"] == 4
    assert vr.wire_values("DryDurType") == {2, 3, 4, 5, 6}


def test_an_unknown_enum_is_an_error_not_an_empty_answer() -> None:
    with pytest.raises(vr.VendorReferenceError, match="not in the app 3.2.0 extract"):
        vr.enum_values("DryDurTyp")
    assert issubclass(vr.VendorReferenceError, LookupError)


def test_has_enum_answers_the_absence_question_without_raising() -> None:
    assert vr.has_enum("DryDurType") is True
    assert vr.has_enum("NoSuchEnumAnywhere") is False


def test_a_capability_gate_names_its_key_path() -> None:
    gate = vr.capability_gate("appType")
    assert gate["keyPath"] == "digiCap.appVer"
    with pytest.raises(vr.VendorReferenceError, match="not a known capability gate"):
        vr.capability_gate("noSuchGate")


def test_the_tables_are_the_documented_size() -> None:
    assert len(vr.writable_settings()) == 27
    commands = vr.command_wire_values()
    assert commands["DOCK"] == "dock"
    assert all(isinstance(v, str) for v in commands.values())


def test_answers_are_copies_the_caller_cannot_corrupt() -> None:
    """The extract is cached; handing out the cached dicts would let one
    caller's edit change every later answer."""
    vr.enum_values("DryDurType")["four"] = 99
    vr.writable_settings().clear()
    assert vr.enum_values("DryDurType")["four"] == 4
    assert len(vr.writable_settings()) == 27


def test_the_extract_is_app_3_2_0s() -> None:
    """What 3.2.0 added, from each section of the file: a capability
    gate, three writable settings, an enum, a member of an old enum and
    a serialiser key."""
    assert vr.capability_gate("sealForce")["keyPath"] == "cap.sealForce"
    assert {"sprayMode", "dryDMode", "sanitizationMode"} <= set(vr.writable_settings())
    assert vr.enum_values("SprayModeOption") == {"ignore": 0, "avoid": 1, "clean": 2}
    assert vr.enum_values("DockPadWashingType")["deepHotWaterWashSupported"] == 4
    assert vr.enum_values("Initiator")["Google"] == "google"
    assert vr.has_enum("IrobotRegionType") is False
