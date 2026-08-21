"""The local-channel check.

App 2.2.4 carried 46 local-socket serializers; 3.0.0 has none. Whether
current *firmware* still listens is a question about the robot, not the
app, and nobody has asked it.

The tool must send nothing. A tester running it should not be able to
move their robot by accident.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestItSendsNothingButDiscovery:
    def test_the_only_payload_is_the_discovery_word(self):
        """Nine bytes the protocol defines as a discovery request, and
        nothing else anywhere in the module."""
        import inspect

        from roombapy_prime_tools import verify_local_channel

        source = inspect.getsource(verify_local_channel)

        assert 'b"irobotmcs"' in source
        for forbidden in ("sendall", "send_command", '"start"', '"dock"'):
            assert forbidden not in source, (
                f"{forbidden} would mean this tool can move a robot"
            )

    def test_stage_four_does_not_connect(self):
        """Even when TLS completes, no MQTT CONNECT is attempted --
        a login is still an interaction, and the finding is already
        made by then."""
        import inspect

        from roombapy_prime_tools import verify_local_channel

        source = inspect.getsource(verify_local_channel._run)

        assert "Not attempting MQTT CONNECT" in source


class TestDiscovery:
    def test_a_json_answer_is_parsed(self):
        from roombapy_prime_tools.verify_local_channel import _discover

        sock = MagicMock()
        sock.recvfrom.side_effect = [
            (b'{"hostname": "iRobot-ABC", "ip": "192.168.1.50"}',
             ("192.168.1.50", 5678)),
            TimeoutError(),
        ]

        with patch("socket.socket", return_value=sock):
            found = _discover()

        assert found[0]["hostname"] == "iRobot-ABC"
        assert found[0]["_from"] == "192.168.1.50"

    def test_a_non_json_answer_is_reported_not_dropped(self):
        """Something else on port 5678 is a finding too, and silently
        discarding it would hide it."""
        from roombapy_prime_tools.verify_local_channel import _discover

        sock = MagicMock()
        sock.recvfrom.side_effect = [
            (b"\x00\x01not json", ("192.168.1.9", 5678)),
            TimeoutError(),
        ]

        with patch("socket.socket", return_value=sock):
            found = _discover()

        assert "raw" in found[0]

    def test_silence_is_an_empty_list_not_an_error(self):
        from roombapy_prime_tools.verify_local_channel import _discover

        sock = MagicMock()
        sock.recvfrom.side_effect = TimeoutError()

        with patch("socket.socket", return_value=sock):
            assert _discover() == []


class TestTheHandshakeReportsHowItFails:
    def test_a_refused_port_says_so(self):
        from roombapy_prime_tools.verify_local_channel import _port_open

        with patch(
            "socket.create_connection",
            side_effect=ConnectionRefusedError("refused"),
        ):
            ok, detail = _port_open("192.168.1.50", 8883)

        assert not ok
        assert "ConnectionRefusedError" in detail

    def test_an_ssl_error_is_passed_through_verbatim(self):
        """The exact error names which part of the handshake failed.
        Summarising it to 'TLS failed' throws away the finding."""
        import ssl

        from roombapy_prime_tools.verify_local_channel import _tls_handshake

        with patch(
            "socket.create_connection",
            side_effect=ssl.SSLError("CERTIFICATE_VERIFY_FAILED unusual"),
        ):
            ok, detail = _tls_handshake("192.168.1.50", 8883)

        assert not ok
        assert "unusual" in detail


class TestTheDiscoveryStageIsTheAnswer:
    """The discovery reply carries SKU and firmware. Whether a robot
    still answers locally depends on its firmware, not on anyone's app
    version — so a run that gets stage 1 and fails stage 2 has already
    produced the number nobody has collected.
    """

    def test_sku_and_firmware_are_surfaced(self):
        import inspect

        from roombapy_prime_tools import verify_local_channel

        source = inspect.getsource(verify_local_channel._run)

        assert "sku" in source
        assert "firmware" in source
        assert "Please include the sku and firmware" in source

    def test_several_firmware_spellings_are_tried(self):
        """`sw`, `softwareVer`, `firmware` — the reply's own key is not
        documented, and reading one spelling would report `?` on a
        robot that answered perfectly well."""
        import inspect

        from roombapy_prime_tools import verify_local_channel

        source = inspect.getsource(verify_local_channel._run)

        for key in ('"sw"', '"softwareVer"', '"firmware"'):
            assert key in source


class TestThePortIsNotAssumed:
    """8883 is the Classic local-MQTT port and what samm-git's helper
    targets. Nothing in the 2.2.4 analysis names a control port for
    Prime — 5678 appears 24 times and that is discovery.
    """

    def test_more_than_one_port_is_tried(self):
        from roombapy_prime_tools.verify_local_channel import _CONTROL_PORTS

        assert len(_CONTROL_PORTS) > 1
        assert 8883 in _CONTROL_PORTS

    def test_the_assumption_is_labelled(self):
        """A borrowed constant that reads as established is how this
        project has been wrong before."""
        import inspect

        from roombapy_prime_tools import verify_local_channel

        source = inspect.getsource(verify_local_channel)

        assert "8883 IS AN ASSUMPTION" in source


class TestItClaimsNoCredentials:
    """An earlier docstring said the cloud login fetches the local
    password. The tool never logs in at all — a claim that would have
    sent someone looking for credentials they do not need.
    """

    def test_no_credential_environment_variables(self):
        import inspect

        from roombapy_prime_tools import verify_local_channel

        source = inspect.getsource(verify_local_channel)

        assert "ROOMBAPY_PRIME_PASSWORD" not in source
        assert "No credentials, no account, no cloud" in source


class TestTheTlsTwelveFallback:
    """`CertificateVerify` is a TLS 1.3 **server** message. In TLS 1.2
    the server never sends one — it appears only from the client during
    mutual authentication.

    So if the robot's broken CertificateVerify is a flaw in its 1.3
    implementation, capping at 1.2 sidesteps the message entirely. That
    would replace samm-git's native C helper with one line, which
    matters for anything shipping into a Home Assistant container.

    Hypothesis, not finding — and free to test.
    """

    def test_a_version_cap_can_be_passed(self):
        import inspect

        from roombapy_prime_tools.verify_local_channel import _tls_handshake

        params = inspect.signature(_tls_handshake).parameters

        assert "max_version" in params

    def test_the_cap_reaches_the_context(self):
        import ssl
        from unittest.mock import MagicMock, patch

        from roombapy_prime_tools.verify_local_channel import _tls_handshake

        context = MagicMock()
        with patch("ssl.SSLContext", return_value=context), patch(
            "socket.create_connection", side_effect=OSError("nope")
        ):
            _tls_handshake("h", 8883, max_version=ssl.TLSVersion.TLSv1_2)

        assert context.maximum_version == ssl.TLSVersion.TLSv1_2

    def test_no_cap_means_no_restriction(self):
        """The default attempt must not silently downgrade — if 1.3
        works, that is itself the answer."""
        from unittest.mock import MagicMock, patch

        from roombapy_prime_tools.verify_local_channel import _tls_handshake

        context = MagicMock()
        context.maximum_version = "untouched"
        with patch("ssl.SSLContext", return_value=context), patch(
            "socket.create_connection", side_effect=OSError("nope")
        ):
            _tls_handshake("h", 8883)

        assert context.maximum_version == "untouched"

    def test_the_run_tries_both(self):
        import inspect

        from roombapy_prime_tools import verify_local_channel

        source = inspect.getsource(verify_local_channel._run)

        assert "TLSv1_2" in source
        assert "retrying with TLS 1.2" in source
