# roombapy-prime-tools

Diagnostic and field-test tooling for
[roombapy-prime](https://github.com/johnnyh1975/roombapy-prime).

> **These scripts move a real robot.** That is why they are a separate
> distribution: the library itself is installed into Home Assistant setups via
> `ha_roomba_plus`, and robot-moving commands have no business on the PATH of
> every one of those installations. Installing the library alone gives you
> **no** console scripts at all.

## What this is for

Almost everything this project knows about the Prime/V4 protocol came from
someone running one of these against their own robot and reporting back. If you
own a Prime robot, that is by far the most useful thing you can do — and the
read-only stages cannot change anything, so it costs you nothing but a few
minutes.

## Install

One command; it pulls the library in as a dependency:

```bash
python3 -m venv ~/roombapy-test-venv
source ~/roombapy-test-venv/bin/activate

pip install roombapy-prime-tools
```

Requires Python 3.11+. You will need to re-run the `source` line each time you
open a new terminal.

**These are not part of Home Assistant.** They run on your own computer,
outside HA entirely. HACS does not install them and does not need to.

## Account details

Set these once per terminal rather than retyping a 32-character BLID on every
command — every script reads them:

```bash
export ROOMBAPY_PRIME_USERNAME="you@example.com"
export ROOMBAPY_PRIME_BLID="YOUR_ROBOT_BLID"
export ROOMBAPY_PRIME_COUNTRY="US"      # your own: DE, FR, IT, ES ...
```

The country code matters — it selects which regional endpoint you authenticate
against, and the default is `US`.

The password is deliberately **not** a command-line argument: it would end up
in your shell history and in any terminal output you paste into an issue. Set
`ROOMBAPY_PRIME_PASSWORD` for unattended runs, or let it prompt.

Any of these can be overridden per-run with `--username`/`--blid`/
`--country-code`.

## Start here

```bash
roombapy-prime-validate
```

Read-only. Logs in, reads state, shadows, favorites, maps and mission history,
then prints a report. Sends nothing to the robot. Its output alone answers
several currently open questions.

Add `--dump-config diagnose.json` to save the raw responses. **Review that file
before sharing it** — redaction catches the known cases, but cannot guarantee
every surprise in an unfamiliar response shape.

## The staged safety model

Every script that can change something follows the same pattern:

1. **Stage 0** — reconnaissance. Reads only, sends nothing, needs no flag.
2. **Stage 1+** — each requires an explicit `--i-understand-this-...` flag
   **and** an interactive confirmation showing the exact payload before
   anything is sent.

Start at stage 0. A stage-0 result is a genuinely useful contribution on its
own — several findings in this project came from nothing more.

Everything validates *before* asking for your password, so an incomplete
command tells you what is missing without making you type credentials first.

## The scripts

| Command | What it does |
|---|---|
| `roombapy-prime-validate` | Full read-only validation run. **Start here.** |
| `…-verify-named-shadows` | Dumps all nine device shadows. The single richest source of protocol data. |
| `…-verify-commands` | Basic mission commands (start/stop/dock/…) with before/after state. |
| `…-verify-mission-timeline` | Watches the live mission/event topics. Read-only unless `--start-mission`. |
| `…-verify-region-commands` | Room-specific cleaning, staged. **Confirmed working** — see below. `--list-rooms` shows rooms *and* zones, with the source of each name. |
| `…-verify-region-commands-session` | The above as one guided session — one login, prompts between stages. |
| `…-verify-map-edit` | Renames one room and reverts it. |
| `…-verify-writes` | One entry point for the write operations with no dedicated verifier — twelve built from decompiled APK code and never fired at hardware. |
| `…-verify-local-channel` | Does the robot still answer on the local API app 3.0.0 dropped? Read-only. |
| `…-verify-favorite-write` | Create/update/delete saved routines. |
| `…-verify-schedule-write` | Resend and disable schedules. |
| `…-verify-virtual-wall-write` | Keep-out zones and virtual walls. **Writes work** — the HTTP 500 was solved before 0.2.0b1. Untested: a write carrying a *changed* list. |
| `…-name-clean-zone` | Names a clean zone. **Full-list rewrite** — anything omitted is deleted. Dry run by default. |
| `…-verify-settings-write` | Child lock, eco charge, schedule hold, … Writes confirmed; most effects untested. |

Every script has `--help`, and it is worth reading before a first run.

The last two are the easiest way to contribute something genuinely new — both
start read-only.

## Region commands: solved, and worth re-running

Room-specific cleaning works. It took three rounds to establish, and two
things were required that are not obvious:

- **`initiator` is mandatory.** A stored favorite does not carry one, so
  resending one unchanged is accepted, acknowledged and silently ignored.
- **The wire keys are `start` and `region_id`**, not `clean` and `id`.

```bash
roombapy-prime-verify-region-commands-session \
    --i-understand-this-will-move-my-robot \
    --i-understand-this-is-experimental-and-unconfirmed
```

It lists your eligible favorites and you pick one by number. **Watch the
robot while it runs.** The fastest stop is the real iRobot app or the
button on the robot.

Two notes from the sessions that got this working:

- A favorite made from **specific rooms** tells you far more than a
  whole-home one. If the robot cleans everything, you have only learned
  that the command arrived.
- Check the delivery confirmation in the output. Every stage that
  received one started a mission; every stage that did not got nothing.
  A "nothing happened" without a confirmation says nothing about the
  payload.

## Virtual walls: reads work, writes do not

Stage 0 (`--list-maps`, then `--list-walls`) works and is read-only.
It is how the zone types were confirmed against real data.

`--update-unchanged` **works.** The HTTP 500 this paragraph described
was solved before 0.2.0b1 — `virwall` starts with a COUNT of the walls —
and confirmed on two accounts, including the write / re-read / write
round trip that separates "accepted" from "stored".

**What is still untested: a write carrying a CHANGED list.** Every
confirmed write resent its zones unchanged, and `set_virtual_wall`
replaces the whole shared list — anything omitted is deleted.

## Developing from an unreleased checkout

The pin above names a git tag, which does not exist yet while a version is
still in development. From a checkout, install the core editable first and skip
dependency resolution here:

```bash
pip install -e .                     # from the repository root
pip install -e ./tools --no-deps
```

Without `--no-deps`, pip fetches a second, unrelated copy of the core from
GitHub and shadows your editable one — which then fails in ways that look like
a code bug rather than an install problem. The CI does exactly this, for
exactly this reason.

## Versioning

This distribution pins an exact core version. The scripts reach deep into the
library, so a mismatched pair fails confusingly rather than cleanly —
`scripts/check_version_pin.py` in the repository root enforces that the two
stay in step.
