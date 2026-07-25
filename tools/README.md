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

pip install "roombapy-prime-tools@git+https://github.com/johnnyh1975/roombapy-prime.git@v0.1.11a26#subdirectory=tools"
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
| `…-verify-region-commands` | **The open blocker.** Staged attempts at room-specific cleaning. |
| `…-verify-region-commands-session` | The above as one guided session — one login, prompts between stages. |
| `…-verify-map-edit` | Renames one room and reverts it. |
| `…-verify-favorite-write` | Create/update/delete saved routines. |
| `…-verify-schedule-write` | Resend and disable schedules. |
| `…-verify-virtual-wall-write` | Keep-out zones and virtual walls. **Never run by anyone yet.** |
| `…-verify-settings-write` | Child lock, eco charge, schedule hold, … **Never run by anyone yet.** |

Every script has `--help`, and it is worth reading before a first run.

The last two are the easiest way to contribute something genuinely new — both
start read-only.

## Region commands: the current focus

Room-specific cleaning does not work yet, and this is where most effort goes.
The session runner walks the staged attempts with checks that run *before* the
robot moves at all: a stale map version on the stored favorite, a
pad/operating-mode mismatch, and whether our own processing drops any field the
favorite actually carries. After sending, it reads the robot's own readiness
status — where a refusal surfaces, if there is one.

```bash
roombapy-prime-verify-region-commands-session \
    --i-understand-this-will-move-my-robot \
    --i-understand-this-is-experimental-and-unconfirmed
```

It lists your eligible favorites and you pick one by number. **Watch the robot
while it runs.** If anything looks unexpected, the fastest stop is the real
iRobot app or the button on the robot.

A result of "still nothing, even with all the checks clean" is itself worth
reporting — it rules out things that cannot currently be ruled out.

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
