

class TestAFavouriteCommandCarriesItsInitiator:
    """`_favorite_from_json` read eleven fields of a stored command def
    and dropped `initiator`, so `to_json()` omitted the key entirely.

    The one region command CONFIRMED working on hardware
    (@Echovictor37) carries `initiator: "rmtApp"`. That was the only
    difference between the confirmed payload and the one a favourite
    button sends — and @chairstacker reports his button does nothing.

    Not proof. A PUBACK with no effect has had several causes in this
    project, and this one is untested against a robot. But sending a
    field the server stored, in a payload otherwise identical to one
    that works, is the change to make before looking further.
    """

    @staticmethod
    def _parse(command):
        from roombapy_prime.rest_client import PrimeRestClient

        return PrimeRestClient._favorite_from_json({
            "favorite_id": "f1", "name": "Test", "commanddefs": [command],
        })

    def test_the_stored_initiator_survives_the_round_trip(self):
        favorite = self._parse({
            "command": "start", "robot_id": "BLID",
            "initiator": "rmtApp", "regions": [],
        })

        assert favorite.command_defs[0].to_json()["initiator"] == "rmtApp"

    def test_a_command_def_with_no_initiator_gets_the_default(self):
        """`localApp`, matching every other command this library sends.
        Omitting the key entirely is what the confirmed payload does
        not do."""
        favorite = self._parse({
            "command": "start", "robot_id": "BLID", "regions": [],
        })

        assert favorite.command_defs[0].to_json()["initiator"] == "localApp"

    def test_the_payload_matches_the_confirmed_shape(self):
        """Field for field against the command in
        `send_routine_command_via_cmd_topic`'s docstring, which is the
        one observed to work on a real robot."""
        favorite = self._parse({
            "command": "start", "robot_id": "BLID",
            "p2map_id": "BLID-1", "initiator": "rmtApp",
            "regions": [{"region_id": "14", "type": "rid",
                         "params": {"operatingMode": 2}}],
        })
        payload = favorite.command_defs[0].to_json()

        assert set(payload) >= {
            "command", "robot_id", "p2map_id", "regions", "initiator",
        }
        assert payload["command"] == "start"
        assert payload["regions"][0]["type"] == "rid"


class TestAMalformedCommandDoesNotTakeTheFavouriteWithIt:
    """The parser reads a favourite's command tolerantly — an unknown
    command becomes None rather than dropping the favourite. Three
    shapes still crashed:

      - a command def with no `command` key: `to_json()` raised
        AttributeError on the None the parser had just produced, so a
        favourite that survived parsing died the moment its button was
        pressed
      - a command whose value is a string the enum does not cover: same
        crash, different route
      - a command def that is a string but not valid JSON: `json.loads`
        raised out of the parser, and the caller catches per favourite —
        so one bad command def deleted a working favourite from the list

    Tolerant on the way in and strict on the way out is not tolerance.
    """

    @staticmethod
    def _parse(commanddefs):
        from roombapy_prime.rest_client import PrimeRestClient

        return PrimeRestClient._favorite_from_json({
            "favorite_id": "f1", "name": "Test", "commanddefs": commanddefs,
        })

    def test_a_command_def_with_no_command_serialises(self):
        payload = self._parse([{}]).command_defs[0].to_json()

        assert payload["command"] is None

    def test_an_unmodelled_command_is_sent_as_it_came(self):
        """Not invented, not dropped. The server stored it."""
        payload = self._parse([{"command": "somethingNew"}]).command_defs[0].to_json()

        assert payload["command"] == "somethingNew"

    def test_invalid_json_drops_only_that_command(self):
        favourite = self._parse([
            "not json",
            '{"command": "start", "robot_id": "BLID"}',
        ])

        assert len(favourite.command_defs) == 1
        assert favourite.command_defs[0].to_json()["command"] == "start"

    def test_a_non_string_non_dict_entry_is_skipped(self):
        assert self._parse([123]).command_defs == []

    def test_the_favourite_itself_survives(self):
        """The point of all four: the user keeps their favourite."""
        favourite = self._parse(["not json"])

        assert favourite.favorite_id == "f1"
        assert favourite.name == "Test"
