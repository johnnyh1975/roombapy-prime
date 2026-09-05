"""A Gigya lockout is detected, and deliberately NOT reclassified.

@jpatchMC hit this with two Prime robots on one account -- the first
person to run that configuration. Each config entry does its own full
login, so one account is two logins and a restart fires both at once.
Both entries failed with "Account Temporarily Locked Out" while the
iRobot app kept working. He reset his password several times to rule out
a wrong one, then fixed it by disabling both robots for ten minutes.

THE OBVIOUS FIX WAS MEASURED AND WITHDRAWN. Raising AuthRateLimitedError
looks right -- a lockout is not a wrong password. But it reaches Home
Assistant as ConfigEntryNotReady, which retries on
`2 ** min(tries, 4) * 5` seconds: **11 attempts per entry in ten
minutes, 22 across his two**, against an account locked precisely
because of too many attempts. AuthCredentialsError produces zero: Home
Assistant stops and asks.

So the detection below changes the MESSAGE, not the class. These tests
guard the detection; the classification is asserted at the end so a
future well-meaning change has to argue with the measurement first.
"""
from __future__ import annotations

import pytest

from roombapy_prime.auth import (
    AuthCredentialsError,
    AuthRateLimitedError,
    _is_temporary_lockout,
)


class TestLockoutIsNotBadCredentials:
    def test_the_lockout_code_is_recognised(self) -> None:
        assert _is_temporary_lockout({"errorCode": 403041}, "whatever")

    def test_a_wrong_password_is_not_a_lockout(self) -> None:
        """403042 sits next to it and means the password IS wrong.

        Treating that as a rate limit would retry a bad password
        forever and never ask the owner to fix it.
        """
        assert not _is_temporary_lockout(
            {"errorCode": 403042}, "Invalid LoginID or password"
        )

    def test_the_reported_wording_matches(self) -> None:
        """The exact message from the field report."""
        assert _is_temporary_lockout({}, "Account Temporarily Locked Out")

    def test_the_code_wins_without_matching_words(self) -> None:
        """The message is localised; the code is not.

        An account whose Gigya profile is not English returns the same
        403041 with words no phrase list can match.
        """
        assert _is_temporary_lockout(
            {"errorCode": 403041}, "Konto vorübergehend gesperrt"
        )

    def test_unrelated_failures_stay_credential_errors(self) -> None:
        for message in (
            "Invalid LoginID or password",
            "Missing required parameter",
            "Account pending registration",
        ):
            assert not _is_temporary_lockout({"errorCode": 400002}, message), message

    def test_the_two_error_classes_are_distinct(self) -> None:
        """Neither inherits from the other.

        A caller catching one must not silently swallow the other.
        """
        assert not issubclass(AuthRateLimitedError, AuthCredentialsError)
        assert not issubclass(AuthCredentialsError, AuthRateLimitedError)

    def test_a_lockout_is_still_a_credentials_error(self) -> None:
        """Guard the decision, not just the detection.

        Reclassifying this to AuthRateLimitedError turns zero automatic
        retries into 11 per config entry in ten minutes -- against an
        account locked for too many attempts. If this test ever fails,
        read the comment in _login_gigya() before changing it back.
        """
        import inspect

        from roombapy_prime import auth

        source = inspect.getsource(auth._login_gigya)
        lockout_branch = source.split("_is_temporary_lockout")[1]
        raised = lockout_branch.split("raise ")[1].split("(")[0]
        assert raised == "AuthCredentialsError", raised


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
