"""Tests for the shared CLI scaffolding.

These three helpers are now reached by all ten diagnostic scripts, so a
regression here breaks every one of them at once -- which is exactly
why they deserve tests the duplicated copies never had.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from roombapy_prime_tools._cli import (
    add_account_arguments,
    require_blid,
    resolve_credentials,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_account_arguments(parser)
    return parser


class TestAddAccountArguments:
    def test_explicit_flags_win(self, monkeypatch):
        monkeypatch.setenv("ROOMBAPY_PRIME_BLID", "FROM_ENV")

        args = _parser().parse_args(["--blid", "FROM_FLAG"])

        assert args.blid == "FROM_FLAG"

    def test_environment_variables_are_used_when_flags_are_absent(self, monkeypatch):
        """The reason these exist: a tester runs several commands in a
        row, and retyping a 32-character BLID is where transcription
        errors come from."""
        monkeypatch.setenv("ROOMBAPY_PRIME_USERNAME", "env@example.com")
        monkeypatch.setenv("ROOMBAPY_PRIME_BLID", "ENVBLID")
        monkeypatch.setenv("ROOMBAPY_PRIME_COUNTRY", "FR")

        args = _parser().parse_args([])

        assert args.username == "env@example.com"
        assert args.blid == "ENVBLID"
        assert args.country_code == "FR"

    def test_country_code_defaults_to_us(self, monkeypatch):
        monkeypatch.delenv("ROOMBAPY_PRIME_COUNTRY", raising=False)

        assert _parser().parse_args([]).country_code == "US"

    def test_blid_is_not_argparse_required(self, monkeypatch):
        """Deliberately not required=True -- several scripts have a
        read-only reconnaissance action that legitimately runs without
        a target device, and that's the stage a tester should start
        with."""
        monkeypatch.delenv("ROOMBAPY_PRIME_BLID", raising=False)

        assert _parser().parse_args([]).blid is None


class TestRequireBlid:
    def test_exits_with_a_clear_message_when_missing(self, capsys):
        with pytest.raises(SystemExit) as exc:
            require_blid(argparse.Namespace(blid=None))

        assert exc.value.code == 1
        assert "ROOMBAPY_PRIME_BLID" in capsys.readouterr().out

    def test_passes_when_present(self):
        require_blid(argparse.Namespace(blid="BLID123"))  # must not raise


class TestResolveCredentials:
    def test_uses_the_flag_and_never_prompts_for_a_supplied_username(self, monkeypatch):
        monkeypatch.setenv("ROOMBAPY_PRIME_PASSWORD", "envpass")

        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            username, password = resolve_credentials(argparse.Namespace(username="a@b.c"))

        assert (username, password) == ("a@b.c", "envpass")

    def test_prompts_for_whatever_is_missing(self, monkeypatch):
        monkeypatch.delenv("ROOMBAPY_PRIME_PASSWORD", raising=False)

        with patch("builtins.input", return_value="typed@example.com"), \
             patch("roombapy_prime_tools._cli.getpass.getpass", return_value="typedpass"):
            username, password = resolve_credentials(argparse.Namespace(username=None))

        assert (username, password) == ("typed@example.com", "typedpass")

    def test_password_is_never_read_from_a_command_line_argument(self):
        """It would land in shell history and in any pasted terminal
        output -- and these runs are routinely pasted into issue
        reports."""
        parser = _parser()
        flags = {a.option_strings[0] for a in parser._actions if a.option_strings}

        assert not any("password" in f for f in flags)
