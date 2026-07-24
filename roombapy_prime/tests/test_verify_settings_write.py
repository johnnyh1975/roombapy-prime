"""Targeted tests for verify_settings_write.py -- the mapping/argparse
logic that's actually testable without a real device. The staged
write itself (--toggle) is, by nature, not automatable -- same
reasoning as this project's other verify_*_write.py test files."""

from __future__ import annotations

from roombapy_prime.models import RobotSettings
from roombapy_prime.verify_settings_write import _TARGET_SETTINGS


def test_target_settings_has_exactly_the_five_confirmed_fields():
    assert set(_TARGET_SETTINGS) == {
        "child_lock", "eco_charge", "sched_hold", "no_auto_passes", "vac_high",
    }


def test_target_settings_wire_keys_match_the_real_capture():
    """The wire keys here must match the real, confirmed capture
    (chairstacker's raw_shadows.json) exactly -- a typo here would
    silently write to a nonexistent field instead of the intended
    setting."""
    assert _TARGET_SETTINGS == {
        "child_lock": "childLock",
        "eco_charge": "ecoCharge",
        "sched_hold": "schedHold",
        "no_auto_passes": "noAutoPasses",
        "vac_high": "vacHigh",
    }


def test_every_target_attribute_actually_exists_on_robot_settings():
    """Catches a rename in RobotSettings (models/robot_info.py) that
    this script's own mapping wasn't updated for -- getattr() would
    otherwise silently return None via a typo'd attribute name rather
    than raising, which _list_settings/_send_toggle would then
    misreport as "setting is None"."""
    settings = RobotSettings()
    for attr_name in _TARGET_SETTINGS:
        assert hasattr(settings, attr_name), f"RobotSettings has no attribute {attr_name!r}"


def test_wire_keys_are_all_distinct():
    assert len(set(_TARGET_SETTINGS.values())) == len(_TARGET_SETTINGS)
