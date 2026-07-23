"""Tests for the testable parts of verify_favorite_write.py --
_build_recolored_favorite()'s core logic. The actual purpose of the
script (writing a real favorite change to a real account) is by
nature not automatable to test -- that's the whole point of the
staged-risk approach described in its own module docstring."""

from __future__ import annotations

import pytest

from roombapy_prime.models.favorites import FavoriteV1
from roombapy_prime.models.mission_control import MissionCommandType, RoutineCommand
from roombapy_prime.verify_favorite_write import _build_recolored_favorite


def test_build_recolored_favorite_actually_executes_without_crashing():
    """Directly exercises _build_recolored_favorite() end-to-end
    against a real FavoriteV1 instance with a real RoutineCommand in
    its command_defs (matching what get_favorites() actually returns,
    per rest_client.py's own _favorite_from_json()) -- same lesson as
    this project's other staged write scripts: an executing test
    catches real construction bugs a syntax check alone cannot."""
    favorite = FavoriteV1(
        favorite_id="f1",
        name="Living Room",
        color="#0000FF",
        command_defs=[RoutineCommand(command_type=MissionCommandType.CLEAN, asset_id="BLID123")],
    )

    modified, original_color = _build_recolored_favorite(favorite, "#FF0000")

    assert original_color == "#0000FF"
    assert modified.color == "#FF0000"
    # everything else, including command_defs, must be untouched.
    assert modified.name == "Living Room"
    assert modified.command_defs == favorite.command_defs


def test_build_recolored_favorite_does_not_mutate_the_original():
    favorite = FavoriteV1(favorite_id="f1", name="Living Room", color="#0000FF")

    _build_recolored_favorite(favorite, "#FF0000")

    assert favorite.color == "#0000FF"


class TestDeleteById:
    """delete_by_id() -- added specifically because a favorite created
    during --create-and-delete-test's own stage 3 was NOT visible in
    the real app (chairstacker), meaning its own in-app confirmation
    step could never be answered. This standalone command deletes by
    favorite_id directly instead, no app visibility required."""

    @pytest.mark.asyncio
    async def test_confirmed_deletion_calls_delete_favorite_with_correct_id(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from roombapy_prime.verify_favorite_write import delete_by_id

        mock_robot = MagicMock()
        mock_robot.blid = "BLID123"
        mock_robot.delete_favorite = AsyncMock(return_value={"status": "ok"})
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session

        with patch(
            "roombapy_prime.verify_favorite_write.PrimeFactory.create_prime_robot",
            AsyncMock(return_value=mock_robot),
        ), patch(
            "roombapy_prime.verify_favorite_write.aiohttp.ClientSession", return_value=mock_session
        ), patch("builtins.input", return_value="y"):
            await delete_by_id("user@example.com", "pw", "US", "BLID123", "fav-abc-123")

        mock_robot.delete_favorite.assert_awaited_once_with("fav-abc-123")

    @pytest.mark.asyncio
    async def test_declined_confirmation_does_not_call_delete_favorite(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from roombapy_prime.verify_favorite_write import delete_by_id

        mock_robot = MagicMock()
        mock_robot.blid = "BLID123"
        mock_robot.delete_favorite = AsyncMock()
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session

        with patch(
            "roombapy_prime.verify_favorite_write.PrimeFactory.create_prime_robot",
            AsyncMock(return_value=mock_robot),
        ), patch(
            "roombapy_prime.verify_favorite_write.aiohttp.ClientSession", return_value=mock_session
        ), patch("builtins.input", return_value="n"):
            await delete_by_id("user@example.com", "pw", "US", "BLID123", "fav-abc-123")

        mock_robot.delete_favorite.assert_not_called()
