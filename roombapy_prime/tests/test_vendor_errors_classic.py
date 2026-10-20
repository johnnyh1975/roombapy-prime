"""The Classic app's error texts (vendor_errors_classic)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from roombapy_prime import vendor_errors_classic as classic
from roombapy_prime.vendor_errors import prime_help_id, vendor_error
from roombapy_prime.vendor_errors_classic import (
    LANGUAGES,
    classic_dock_text,
    classic_help_id,
    classic_not_ready_text,
    classic_vendor_error,
)

_PLACEHOLDER = re.compile(r"%\d?\$?s|%robotName|​")


class TestTheClassicNamespace:
    def test_error_2_on_a_j7_is_its_rubber_brushes_not_the_prime_main_brush(self):
        """The reason for this table: the Prime app's 2 is "Main Brush
        stuck", a part a j7 does not have."""
        j7 = classic_vendor_error(2, "en", sku="j755020")

        assert j7["title"] == "Brushes need cleaning"
        assert "green brushes" in j7["content"]
        assert vendor_error(2)["title"] == "Main Brush stuck"

    def test_a_69x_gets_its_bristle_brush(self):
        """The heading stays the app's rubber one; the label follows
        the history line the SKU replaced."""
        found = classic_vendor_error(2, "de", sku="R694020")

        assert "Hauptbürsten" in found["content"]
        assert found["history"] == "Hauptbürsten stecken fest"
        assert found["label"] == "Hauptbürsten stecken fest"
        assert found["title"] == "Gummibürsten müssen gereinigt werden"

    @pytest.mark.parametrize("sku", ["Q011020", "y0110"])
    def test_an_essential_gets_its_centre_brush_on_the_status_card(self, sku):
        found = classic_vendor_error(2, "en", sku=sku)

        assert found["title"] == "Center Brush needs cleaning"
        assert found["label"] == "Center Brush needs cleaning"
        assert found["history"] == "Debris extractors stuck"

    def test_the_history_prefix_is_three_upper_cased_characters(self):
        """Not a regex like the status card's: T72 matches "t72...",
        and only the history knows 85."""
        assert classic_vendor_error(85) is None
        assert classic_vendor_error(85, sku="t72000")["label"] == (
            "Path to charging station blocked"
        )

    def test_a_short_sku_is_its_own_prefix(self):
        assert classic_vendor_error(2, sku="R6")["label"] == "Brushes need cleaning"

    def test_a_clean_base_changes_36(self):
        """Barely: the app's `_evac` text is the default one with its
        full stop -- and, in German, without "Absaugstation"."""
        plain = classic_vendor_error(36, "de")
        evac = classic_vendor_error(36, "de", clean_base=True)

        assert "Absaugstation" in plain["content"]
        assert "Absaugstation" not in evac["content"]
        assert evac["content"].endswith("(Fehler\u00a036).")

    def test_imperial_units_change_74s_history(self):
        assert classic_vendor_error(74)["label"] == "Max m² reached"
        assert classic_vendor_error(74, imperial=True)["label"] == "Max Sq. Ft. reached"


class TestTheLabel:
    def test_a_shared_heading_gives_way_to_the_history_line(self):
        """Every start refusal is headed "Unable to start"."""
        found = classic_vendor_error(206, "en")

        assert found["title"] == "Unable to start"
        assert found["label"] == "@val's debris extractors were stuck."

    def test_a_code_the_status_card_never_shows_is_named_by_its_history(self):
        found = classic_vendor_error(1000, "en")

        assert "title" not in found
        assert found["label"] == "Left edge-sweeping brush stuck"
        assert "edge-sweeping brush" in found["content"]

    def test_a_shared_heading_without_history_stays(self):
        assert classic_vendor_error(110)["label"] == "Charging error"


class TestNotReady:
    def test_not_ready_is_its_own_namespace(self):
        """notReady 15 is a low battery; error 15 a reboot."""
        assert classic_not_ready_text(15)["label"] == "Low battery"
        assert classic_vendor_error(15)["label"] == "Robot rebooted"

    def test_the_69x_variant_reaches_not_ready_6(self):
        assert "main brushes" in classic_not_ready_text(6, sku="R694020")["content"]
        assert "debris extractors" in classic_not_ready_text(6)["content"]

    def test_the_key_is_the_value_the_robot_reports(self):
        """The app files refusals under the wire value: 33 is Bumped,
        although the readiness state BUMPED is index 21."""
        assert classic_not_ready_text(33)["label"] == "Bumper stuck"
        assert classic_not_ready_text(39)["label"] == "Clean Map problem"

    @pytest.mark.parametrize("value", [None, "x", 999])
    def test_an_unknown_value_has_no_text(self, value):
        assert classic_not_ready_text(value) is None

    def test_as_shipped_keeps_the_placeholder(self):
        assert "%s" in classic_not_ready_text(15, as_shipped=True)["content"]


class TestDock:
    def test_the_code_is_filled_in(self):
        assert classic_dock_text(450, "de")["content"].endswith("(450)")
        assert classic_dock_text(451, "en")["content"].endswith("(451)")

    def test_the_robot_name_stays_a_placeholder(self):
        assert classic_dock_text(451)["content"].startswith("Fill the tank so @val")
        assert classic_dock_text(301)["label"] == "@val's auto evac dock is ready."

    def test_dock_codes_are_not_robot_errors(self):
        """The Classic app never reads 450-463 from the error field."""
        assert classic_vendor_error(450) is None
        assert classic_dock_text(450)["label"] == "Dock: tank missing"

    @pytest.mark.parametrize("value", [None, "x", 2])
    def test_an_unknown_code_has_no_text(self, value):
        assert classic_dock_text(value) is None

    def test_as_shipped_is_the_app_text(self):
        assert classic_dock_text(450, as_shipped=True)["content"].endswith("(%s)")


class TestLanguagesAndInput:
    def test_a_region_and_an_unknown_language(self):
        assert classic_vendor_error(15, "pt-BR")["label"] == "Robot reiniciado"
        assert classic_vendor_error(15, "pt_PT") == classic_vendor_error(15, "pt")
        assert classic_vendor_error(15, "sv") == classic_vendor_error(15, "en")
        assert classic_vendor_error(15, None) == classic_vendor_error(15, "en")  # type: ignore[arg-type]

    @pytest.mark.parametrize("code", [None, "x", 99999])
    def test_no_text_for_no_code(self, code):
        assert classic_vendor_error(code) is None

    def test_a_string_code_is_read(self):
        assert classic_vendor_error("15") == classic_vendor_error(15)

    def test_the_answer_is_a_copy(self):
        classic_vendor_error(15)["label"] = "changed"

        assert classic_vendor_error(15)["label"] == "Robot rebooted"

    def test_as_shipped_keeps_the_fields_the_app_has(self):
        shipped = classic_vendor_error(74, as_shipped=True)

        assert shipped["history_feet"] == "Max Sq. Ft. reached"
        assert "label" not in shipped
        assert "content_unlinked" in classic_vendor_error(1000, as_shipped=True)

    def test_a_code_is_never_grouped_like_a_number(self):
        """The French 1015 writes "(Erreur 1 015)"."""
        assert classic_vendor_error(1015, "fr")["content"].endswith("(Erreur 1015)")


class TestTheTable:
    def _every_text(self):
        table = classic._table()
        for code in table["robot"]:
            for lang in LANGUAGES:
                yield classic_vendor_error(code, lang)
                yield classic_vendor_error(code, lang, sku="R694020", clean_base=True)
        for n in table["not_ready"]:
            for lang in LANGUAGES:
                yield classic_not_ready_text(n, lang, sku="R694020")
        for code in table["dock"]:
            for lang in LANGUAGES:
                yield classic_dock_text(code, lang)

    def test_every_language_has_every_code(self):
        table = classic._table()
        for section in ("robot", "not_ready", "dock"):
            for code, entry in table[section].items():
                assert set(entry) == set(LANGUAGES), (section, code)

    def test_no_raw_placeholder_reaches_a_caller(self):
        for found in self._every_text():
            assert found is not None
            assert found.get("label")
            for text in found.values():
                assert not _PLACEHOLDER.search(text), text

    def test_the_table_ships_with_the_package(self):
        pyproject = tomllib.loads(
            (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
        )
        data = pyproject["tool"]["setuptools"]["package-data"]["roombapy_prime"]

        assert "vendor_errors_classic.json" in data


class TestHelpId:
    @pytest.mark.parametrize(
        ("code", "key"),
        [
            (2, "Error 2"),
            ("26", "Error 26"),
            (1010, "Error 1010"),
            (101, "Charging Error 1"),
            (121, "Charging Error 21"),
            (206, "Start-Refuse 6"),
        ],
    )
    def test_an_error_code(self, code, key):
        assert classic_help_id(code) == key

    def test_a_refusal(self):
        assert classic_help_id(not_ready=15) == "Start-Refuse 15"

    @pytest.mark.parametrize("code", [None, 0, -1, "x"])
    def test_no_key_for_no_error(self, code):
        assert classic_help_id(code) is None
        assert classic_help_id(not_ready=code) is None


class TestPrimeHelpId:
    """The Prime catalogue (G185020, 19.10.2026) files charging errors
    under the full code, where the Classic one subtracts 100."""

    @pytest.mark.parametrize(
        ("code", "key"),
        [(517, "Error 517"), (2, "Error 2"), (101, "Charging Error 101"), (222, "Start-Refuse 22")],
    )
    def test_a_code(self, code, key):
        assert prime_help_id(code) == key

    def test_charging_differs_from_classic(self):
        assert prime_help_id(104) != classic_help_id(104)

    @pytest.mark.parametrize("code", [None, 0, "x"])
    def test_no_key_for_no_error(self, code):
        assert prime_help_id(code) is None


class TestTheTableIsReadAtImport:
    """0.8.0 read the 500 KB table at the first lookup. Roomba+ 4.5.0b2's
    first lookup was a sensor's first state write, and Home Assistant
    reported a blocking read_text in its event loop. From 0.8.1 a lookup
    reads no file."""

    @staticmethod
    def _reimport():
        import importlib

        return importlib.reload(classic)

    def test_a_lookup_after_the_import_opens_no_file(self, monkeypatch):
        module = self._reimport()

        def no_reading(*_a, **_kw):
            raise AssertionError("the table was read at a lookup")

        monkeypatch.setattr(type(module._TABLE), "read_text", no_reading)
        assert module.classic_vendor_error(2)["label"] == "Brushes need cleaning"
        assert module.classic_dock_text(450)

    def test_an_unreadable_table_does_not_break_the_import(self, monkeypatch):
        def broken(*_a, **_kw):
            raise OSError("gone")

        monkeypatch.setattr(type(classic._TABLE), "read_text", broken)
        module = self._reimport()  # must not raise
        with pytest.raises(OSError):
            module.classic_vendor_error(2)  # the lookup raises, as in 0.8.0
        monkeypatch.undo()
        self._reimport()
