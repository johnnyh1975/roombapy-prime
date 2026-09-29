"""CloudError sits above every cloud error without moving any of them,
and every one of them says why it was raised."""
from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import roombapy_prime
from roombapy_prime import auth, mqtt_client, rest_client
from roombapy_prime.errors import CloudError, CloudErrorReason, reason_for_status


@pytest.mark.parametrize(
    "error",
    [
        auth.AuthError, auth.AuthCredentialsError, auth.AuthRateLimitedError,
        auth.AuthSSLError, auth.AuthConnectionError, auth.AuthTimeoutError,
        rest_client.RestError, rest_client.RestSSLError,
        rest_client.RestConnectionError, rest_client.RestTimeoutError,
        rest_client.RestHTTPError, rest_client.RestClientError,
        rest_client.RestRateLimitedError, rest_client.RestServerError,
        mqtt_client.ShadowError, mqtt_client.ShadowSSLError,
        mqtt_client.ShadowConnectionError, mqtt_client.SubscriptionRejectedError,
    ],
    ids=lambda c: c.__name__,
)
def test_every_cloud_error_is_a_cloud_error(error: type[Exception]) -> None:
    assert issubclass(error, CloudError)


def test_the_families_stay_apart() -> None:
    """The new base must not make one family catch another's errors."""
    assert not issubclass(rest_client.RestError, auth.AuthError)
    assert not issubclass(auth.AuthError, rest_client.RestError)
    assert not issubclass(mqtt_client.ShadowError, rest_client.RestError)
    assert issubclass(auth.AuthCredentialsError, auth.AuthError)
    assert issubclass(rest_client.RestSSLError, rest_client.RestError)
    # An answer with an error status is not a broken connection.
    for http in (rest_client.RestClientError, rest_client.RestRateLimitedError,
                 rest_client.RestServerError):
        assert issubclass(http, rest_client.RestHTTPError)
        assert not issubclass(http, rest_client.RestConnectionError)


# ── reason (0.4.0) ─────────────────────────────────────────────────────────
#
# `reason` is what an application translates from, so it is interface:
# a closed set of names, one per cause, never UNKNOWN from this library.

_PACKAGE = Path(roombapy_prime.__file__).parent


def test_every_reason_name_is_its_own_value() -> None:
    """The value is what a translation file keys on; tying it to the
    name leaves no second spelling to drift."""
    for member in CloudErrorReason:
        assert member.value == member.name.lower()
        assert member.value.replace("_", "").isalpha()
    assert len({m.value for m in CloudErrorReason}) == len(CloudErrorReason)


def test_a_reason_is_a_plain_string_for_the_caller() -> None:
    """StrEnum: usable directly as a key -- f"cloud_{reason}" -- without
    `.value`, and equal to the string a caller wrote down."""
    assert f"cloud_{CloudErrorReason.TIMEOUT}" == "cloud_timeout"
    assert CloudErrorReason.TIMEOUT == "timeout"


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (400, CloudErrorReason.REQUEST_REFUSED),
        (403, CloudErrorReason.REQUEST_REFUSED),
        (404, CloudErrorReason.REQUEST_REFUSED),
        (429, CloudErrorReason.RATE_LIMITED),
        (499, CloudErrorReason.REQUEST_REFUSED),
        (500, CloudErrorReason.SERVER_ERROR),
        (503, CloudErrorReason.SERVER_ERROR),
        (200, CloudErrorReason.RESPONSE_MALFORMED),
        (302, CloudErrorReason.RESPONSE_MALFORMED),
    ],
)
def test_reason_for_status(status: int, reason: CloudErrorReason) -> None:
    assert reason_for_status(status) is reason


def test_a_cloud_error_built_without_a_reason_says_unknown() -> None:
    """The one place UNKNOWN comes from: code outside this library."""
    assert CloudError("x").reason is CloudErrorReason.UNKNOWN
    assert CloudError("x", reason=CloudErrorReason.TIMEOUT).reason is CloudErrorReason.TIMEOUT
    assert str(CloudError("x", reason=CloudErrorReason.TIMEOUT)) == "x"


def test_a_reason_passed_to_one_error_does_not_leak_into_its_class() -> None:
    error = mqtt_client.ShadowError("x", reason=CloudErrorReason.TIMEOUT)
    assert error.reason is CloudErrorReason.TIMEOUT
    assert mqtt_client.ShadowError.reason is CloudErrorReason.UNKNOWN
    assert mqtt_client.ShadowError("y").reason is CloudErrorReason.UNKNOWN


def _library_modules() -> list[str]:
    return [
        info.name
        for info in pkgutil.walk_packages([str(_PACKAGE)], prefix="roombapy_prime.")
        if ".tests" not in info.name
    ]


def _library_cloud_errors() -> set[type[CloudError]]:
    for name in _library_modules():
        importlib.import_module(name)
    found: set[type[CloudError]] = set()
    stack: list[type[CloudError]] = [CloudError]
    while stack:
        for sub in stack.pop().__subclasses__():
            if sub.__module__.startswith("roombapy_prime.") and ".tests" not in sub.__module__:
                found.add(sub)
            stack.append(sub)
    return found


def _unknown_by_default() -> set[str]:
    """Error classes whose every raise must name its reason."""
    return {cls.__name__ for cls in _library_cloud_errors() if cls.reason is CloudErrorReason.UNKNOWN}


def test_only_the_catch_all_classes_leave_the_reason_to_each_raise() -> None:
    """Every other class names the reason its name implies. A new error
    class without one lands here -- either give it one, or add it to
    this set and let the guard below hold each raise to it."""
    assert _unknown_by_default() == {"ShadowError"}


def unnamed_reasons(source: str, filename: str, needs_reason: set[str]) -> list[str]:
    """Constructions of an error class in `needs_reason` without a
    `reason=`, and any use of CloudErrorReason.UNKNOWN."""
    problems: list[str] = []
    for node in ast.walk(ast.parse(source, filename)):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "UNKNOWN"
            and isinstance(node.value, ast.Name)
            and node.value.id == "CloudErrorReason"
        ):
            problems.append(f"{filename}:{node.lineno}: CloudErrorReason.UNKNOWN")
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if name in needs_reason and not any(k.arg == "reason" for k in node.keywords):
            problems.append(f"{filename}:{node.lineno}: {name}(...) without reason=")
    return problems


def test_every_raise_in_the_library_names_its_reason() -> None:
    """THE GUARD. A ShadowError raised without `reason=` would reach an
    application as UNKNOWN -- untranslatable, and the application would
    be back to showing English text. errors.py defines UNKNOWN and is
    the only module that may name it."""
    needs_reason = _unknown_by_default()
    problems: list[str] = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        relative = path.relative_to(_PACKAGE)
        if relative.parts[0] == "tests" or relative.name == "errors.py":
            continue
        problems += unnamed_reasons(path.read_text(encoding="utf-8"), str(relative), needs_reason)
    assert problems == []


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('raise ShadowError("x")', True),
        ('raise mqtt_client.ShadowError("x") from exc', True),
        ('err = ShadowError("x")', True),
        ('raise ShadowError("x", reason=CloudErrorReason.UNKNOWN)', True),
        ('raise ShadowError("x", reason=CloudErrorReason.TIMEOUT)', False),
        ('raise ShadowError("x", reason=reason)', False),
        ('raise RestError("x")', False),
    ],
)
def test_the_guard_can_fail(source: str, found: bool) -> None:
    """Counter-check: each case the guard exists for, written the way
    it would appear in the library."""
    assert bool(unnamed_reasons(source, "example.py", {"ShadowError"})) is found


@pytest.mark.parametrize("error", sorted(_library_cloud_errors(), key=lambda c: c.__name__),
                         ids=lambda c: c.__name__)
def test_every_error_class_default_is_a_member_of_the_set(error: type[CloudError]) -> None:
    """Class defaults are members of the set, never a stray string."""
    assert isinstance(error.reason, CloudErrorReason)
