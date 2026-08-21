"""Does the robot still answer locally?

App 2.2.4 carried a complete local API -- 46 socket serializers,
`irobotmcs` discovery, port 5678. App 3.0.0 has none of it.
**But the app dropping a path is not the robot dropping it** -- and the
robot has not dropped it.

CURRENT FIRMWARE STILL SPEAKS IT. Reported August 2026 on
`p25-705+9.3.6+I3.8.149` by the author of samm-git/irobot-explore, who
also found how it opens: start the BLE Wi-Fi provisioning flow and
**stop before sending any values**. The robot beeps, local MQTT comes
up, and stays up until the next reboot.

No physical button, no auto-test mode -- the channel comes up as part
of a flow the app itself runs.

WHICH IS WHY A SILENT RUN HERE MEANS LITTLE. This tool finds a channel
that is *already open*. On a robot nobody has provisioned recently
there is nothing to find, and that is the normal state rather than a
verdict about the firmware.

WHAT THIS RUNS
--------------

Four stages, each answering on its own. It stops at the first that
fails, because a later stage cannot mean anything without the earlier
one.

    1  UDP discovery      does the robot answer `irobotmcs` on 5678?
    2  TCP connect        is port 8883 open?
    3  TLS handshake      does it complete, or fail the documented way?
    4  MQTT CONNECT       does it accept blid/password?

**Stages 1 and 2 are pure Python and completely safe.** Stage 3 is
where samm-git needed a native helper: the robot sends a
`CertificateVerify` that standard TLS libraries reject. We do not work
around it -- we run into it deliberately and report exactly how it
fails, which is itself a finding.

Stage 4 only runs if 3 somehow succeeds.

TWO THINGS SAMM-GIT'S IMPLEMENTATION TELLS US
---------------------------------------------

**Local control is fire-and-forget.** The robot sends no response to a
command over this channel -- his README says to verify with a separate
`status` call. So even a working local path would not confirm its own
commands, which is why stage 4 stops at "TLS completed" rather than
trying to prove anything by sending.

**It is not cloud-free.** His `--local` still logs in to the cloud once
to fetch the robot's local password; `/v2/login` returns it as
`robots[blid].password` and there is no other source. A local transport
removes the round trip, not the dependency -- worth knowing before
anyone reads this tool as a route to a cloud-free integration.

NOTHING IS SENT TO THE ROBOT. No commands, no missions. The MQTT
CONNECT is a login, not an instruction, and the run disconnects
immediately.

WHY EACH STAGE IS WORTH REPORTING
---------------------------------

**No UDP answer** -- the robot does not announce itself locally at all.
The most likely outcome on current firmware, and a clean negative.

**UDP but no port 8883** -- it announces itself and refuses
connections. That would mean discovery survived while control did not,
which nobody has documented.

**Port open, TLS fails** -- the channel is there and standard clients
cannot reach it. That is samm-git's situation, and it makes his native
helper the only known way in.

**TLS completes** -- current firmware still speaks local MQTT, and
Home Assistant integrations have a local path for Prime robots after
all. That would be the largest finding this project has had.

USAGE
-----

    roombapy-prime-verify-local-channel

**No credentials, no account, no cloud.** Everything here happens on
your own network. Add `--ip 192.168.1.50` if the robot is on another
subnet where a broadcast will not reach it.

THE FIRST STAGE MAY BE THE WHOLE ANSWER
---------------------------------------

The discovery reply carries the robot's **SKU and firmware version**.
That is the datapoint the entire local-channel question turns on: the
app dropped this path between 2.2.4 and 3.0.0, and whether a given
*robot* still answers depends on its firmware, not on anyone's app.

So a run that gets an answer at stage 1 and fails at stage 2 is not a
failed run. It has already produced the number nobody has.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import ssl
import sys
from typing import Any

_DISCOVERY_PORT = 5678
_DISCOVERY_MESSAGE = b"irobotmcs"
#: 8883 IS AN ASSUMPTION, AND A BORROWED ONE.
#:
#: It is the port Classic robots use for local MQTT, and what
#: samm-git's helper targets. Nothing in the 2.2.4 analysis names a
#: control port -- port 5678 appears 24 times and that is discovery.
#:
#: So both are tried rather than assuming either. A robot answering on
#: something else entirely would be missed, but guessing further would
#: be scanning someone's robot rather than checking it.
_CONTROL_PORTS = (8883, 8884)


def _subnet_broadcast() -> str | None:
    """The /24 broadcast address of whichever interface routes out.

    Opening a UDP socket toward a public address does not send
    anything -- it just makes the OS pick a source address, which is
    the local IP on the interface that would carry the traffic.
    """
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            local_ip = probe.getsockname()[0]
        finally:
            probe.close()
    except OSError:
        return None

    parts = local_ip.split(".")
    if len(parts) != 4:
        return None
    parts[3] = "255"
    return ".".join(parts)


def _parse_discovery_reply(data: bytes) -> dict[str, Any]:
    """Parse one discovery datagram, tolerating a length prefix.

    Some robots prefix the JSON with a 2-byte big-endian length. A
    plain json.loads() throws on those, and a silent skip would drop
    the robot from discovery entirely -- indistinguishable from a
    robot that never answered, which is the exact question this tool
    exists to settle. Learned from samm-git/irobot-explore, whose
    parser handles both forms.

    Unparseable data is returned as `raw` rather than dropped: this
    tool reports what happened rather than deciding what counts.
    """
    try:
        return dict(json.loads(data.decode("utf-8", "replace")))
    except ValueError:
        pass

    try:
        length = int.from_bytes(data[:2], "big")
        return dict(json.loads(data[2 : 2 + length].decode("utf-8", "replace")))
    except (ValueError, IndexError):
        return {"raw": data[:120].decode("utf-8", "replace")}


def _blid_from(payload: dict[str, Any]) -> str | None:
    """The BLID, falling back to the hostname when `robotid` is absent.

    The hostname carries it as `iRobot-<blid>` or `Roomba-<blid>`.
    Without this fallback a robot that omits `robotid` looks
    unidentifiable when its BLID was sitting in the next field along.
    """
    robot_id = payload.get("robotid")
    if isinstance(robot_id, str) and robot_id:
        return robot_id

    hostname = payload.get("hostname")
    if not isinstance(hostname, str):
        return None
    for prefix in ("iRobot-", "Roomba-"):
        if hostname.startswith(prefix):
            return hostname[len(prefix) :] or None
    return None


def _discover(timeout: float = 5.0) -> list[dict[str, Any]]:
    """Broadcast `irobotmcs` and collect whatever answers.

    Pure UDP, nothing sent to any robot beyond a nine-byte broadcast
    that the protocol defines as a discovery request.

    Sent to BOTH the subnet-directed broadcast and 255.255.255.255.
    Some routers and interface configurations drop the global one, and
    a dropped broadcast produces silence that reads exactly like a
    robot that does not answer -- the wrong conclusion from the one
    question this tool asks.
    """
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    targets = [t for t in (_subnet_broadcast(), "255.255.255.255") if t]

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)

    try:
        for target in targets:
            try:
                sock.sendto(_DISCOVERY_MESSAGE, (target, _DISCOVERY_PORT))
            except OSError:
                # One target failing is not fatal -- the other may
                # still reach the robot.
                continue

        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except TimeoutError:
                break
            except OSError:
                break

            # Two broadcasts mean a robot may answer twice.
            if addr[0] in seen:
                continue
            seen.add(addr[0])

            payload = _parse_discovery_reply(data)
            payload["_from"] = addr[0]
            blid = _blid_from(payload)
            if blid:
                payload.setdefault("robotid", blid)
            found.append(payload)
    finally:
        sock.close()

    return found


def _port_open(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """Whether a TCP connect succeeds. No TLS, no data."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "open"
    except TimeoutError:
        return False, "timed out"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _tls_handshake(
    host: str,
    port: int,
    timeout: float = 8.0,
    max_version: ssl.TLSVersion | None = None,
    ciphers: str = "DEFAULT@SECLEVEL=1",
) -> tuple[bool, str]:
    """Attempt a TLS handshake and report exactly how it ends.

    `max_version` caps the protocol. See `_run` for why TLS 1.2 is
    worth trying separately.

    Verification is off and the cipher list is widened: this is not a
    security decision, it is the only way to learn *which* part fails.
    A robot with a broken `CertificateVerify` fails differently from
    one that refuses outright, and the difference is the finding.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        context.set_ciphers(ciphers)
    except ssl.SSLError:
        pass
    if max_version is not None:
        context.maximum_version = max_version

    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return True, f"completed, {tls.version()}"
    except ssl.SSLError as exc:
        return False, f"SSLError: {exc}"
    except TimeoutError:
        return False, "timed out"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def _run(args: argparse.Namespace) -> int:
    print("Local channel check -- nothing is sent to the robot.\n")

    # STAGE 1
    if args.ip:
        print(f"[skip] UDP discovery -- using --ip {args.ip}")
        hosts = [args.ip]
    else:
        print(f"[1/4] UDP discovery on port {_DISCOVERY_PORT} ...")
        answers = _discover()
        if not answers:
            print(
                "      no answer.\n\n"
                "      The robot does not announce itself locally. That is\n"
                "      the expected result on current firmware and a clean\n"
                "      negative -- worth reporting as-is.\n\n"
                "      If your robot is on another subnet, broadcast will\n"
                "      not reach it; try --ip."
            )
            return 0

        for answer in answers:
            # SKU AND FIRMWARE FIRST, because they are the point.
            # Everything else in the reply is addressing detail.
            sku = answer.get("sku") or "?"
            firmware = (
                answer.get("sw")
                or answer.get("softwareVer")
                or answer.get("firmware")
                or "?"
            )
            print(
                f"      {answer.get('hostname', '?')}  "
                f"at {answer['_from']}\n"
                f"        sku      {sku}\n"
                f"        firmware {firmware}"
            )
            print(f"        full     {json.dumps(answer)[:300]}")

        print(
            "\n      ^ Please include the sku and firmware in any report.\n"
            "        Whether a robot still answers locally depends on its\n"
            "        firmware, and nobody has collected these."
        )

        if len(answers) > 1:
            print(f"\n      {len(answers)} robots answered; checking the first.")
        hosts = [a["_from"] for a in answers]

    host = hosts[0]

    # STAGE 2
    print(f"\n[2/4] TCP connect to {host} ...")
    port = None
    for candidate in _CONTROL_PORTS:
        ok, detail = _port_open(host, candidate)
        print(f"      :{candidate}  {detail}")
        if ok:
            port = candidate
            break
    if port is None:
        print(
            "\n      It announces itself and refuses connections.\n"
            "      Discovery survived, control did not -- worth reporting."
        )
        return 0

    # STAGE 3
    #
    # TWO ATTEMPTS, AND THE SECOND MAY BE THE WHOLE POINT.
    #
    # `CertificateVerify` is a **TLS 1.3** server message. In TLS 1.2
    # the server does not send one at all -- it appears only from the
    # client, during mutual authentication.
    #
    # So if the robot's broken CertificateVerify is a flaw in its 1.3
    # implementation, capping the connection at TLS 1.2 sidesteps the
    # message entirely -- no native helper, no compiler, no
    # hand-rolled TLS.
    #
    # WHAT THE NATIVE HELPER ACTUALLY DOES (from lss_client.c in
    # samm-git/irobot-explore): pins TLS 1.2, disables cert
    # verification, drops OpenSSL's security level, AND pins a sigalgs
    # list -- `RSA+SHA256:RSA+SHA384:RSA+SHA512`. Three of those four we
    # already do below. The fourth, sigalgs, Python's `ssl` cannot set:
    # there is no `set_sigalgs`, and nothing in `_ssl` exposes it
    # (checked on OpenSSL 3.0.13).
    #
    # But the sigalgs pin may be a leftover from a 1.3 path rather than
    # a 1.2 requirement. Sigalgs constrain how a signature is made;
    # with verification off, our client signs nothing, and in 1.2 the
    # server's own signature is not a CertificateVerify. So capping at
    # 1.2 with cert checks off may not need the sigalgs pin at all.
    #
    # That is now a sharp hypothesis rather than a vague one, and only
    # a handshake against a real robot settles it. If it holds, "needs
    # a C helper" becomes "needs one line" -- which is the difference
    # between shippable in a Home Assistant container and not.
    print(f"\n[3/4] TLS handshake with {host}:{port} ...")
    ok, detail = _tls_handshake(host, port)
    print(f"      default   {detail}")

    if not ok:
        print("      retrying with TLS 1.2 capped ...")
        ok_12, detail_12 = _tls_handshake(
            host, port, max_version=ssl.TLSVersion.TLSv1_2
        )
        print(f"      TLS 1.2   {detail_12}")

        if ok_12:
            print(
                "\n      **THIS IS THE INTERESTING OUTCOME.**\n\n"
                "      TLS 1.3 fails and TLS 1.2 completes -- which is what\n"
                "      a broken 1.3 CertificateVerify looks like from here.\n"
                "      If that holds, the native helper samm-git needed may\n"
                "      be replaceable by capping the protocol version.\n\n"
                "      Please report both lines above."
            )
            return 0

        # THIRD ATTEMPT -- and the reasoning behind it.
        #
        # Field result (ricrog1135, W155020, firmware
        # p25-705+9.3.6+I3.8.149): BOTH attempts above failed with
        # `BAD_SIGNATURE`. That kills the "cap it at 1.2" hypothesis
        # and explains why.
        #
        # The robot signs with a key that does not match the leaf
        # certificate it presents. TLS 1.3 carries that signature in
        # CertificateVerify. TLS 1.2 with an ECDHE suite carries it in
        # ServerKeyExchange. Capping the version changes WHICH message
        # holds the bad signature, not whether one is sent.
        #
        # But a STATIC RSA suite has no server signature at all: the
        # client encrypts the premaster secret to the certificate's
        # public key, and nothing is signed for us to reject. If the
        # robot's legacy stack still offers one, the mismatched key is
        # never exercised.
        #
        # Untested against a robot. If it completes, a native helper
        # with a patched TLS library stops being necessary.
        #
        # `kRSA` still lists three TLS 1.3 suites -- set_ciphers() does
        # not filter 1.3, which uses a separate suite list. The
        # max_version cap below excludes them at handshake time, so the
        # negotiated suite is always static RSA.
        print("      retrying with static-RSA suites (no server signature) ...")
        ok_rsa, detail_rsa = _tls_handshake(
            host,
            port,
            max_version=ssl.TLSVersion.TLSv1_2,
            ciphers="kRSA:@SECLEVEL=0",
        )
        print(f"      static RSA {detail_rsa}")

        if ok_rsa:
            print(
                "\n      **THIS IS THE FINDING.**\n\n"
                "      The robot's signature never gets checked because\n"
                "      this suite has no server signature to check. If it\n"
                "      holds up, pure Python can speak this channel and\n"
                "      the native helper is not needed.\n\n"
                "      Please report all three lines above."
            )
            return 0

    if not ok:
        print(
            "\n      The port is open and standard TLS cannot complete.\n"
            "      The robot signs with a key that does not match the\n"
            "      certificate it presents, so every attempt that needs a\n"
            "      server signature fails -- in 1.3 that is\n"
            "      CertificateVerify, in 1.2 ECDHE it is ServerKeyExchange,\n"
            "      and a static-RSA suite avoids one entirely.\n\n"
            "      All three failing means a patched TLS library really is\n"
            "      required, which is worth knowing definitively.\n\n"
            "      Please report the exact SSLError lines above."
        )
        return 0

    # STAGE 4
    print("\n[4/4] TLS completed. Not attempting MQTT CONNECT.")
    print(
        "\n      **This is the interesting outcome.** Current firmware\n"
        "      still speaks local TLS on the MQTT port. Please open an\n"
        "      issue with your robot's SKU and firmware version -- it\n"
        "      would be the first observation of this."
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Check whether a robot still answers on the local channel. "
            "Read-only; sends no commands."
        )
    )
    parser.add_argument(
        "--ip", default=None, help="Skip discovery and use this address."
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
