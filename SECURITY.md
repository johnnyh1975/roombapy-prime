# Security

## Reporting a vulnerability

If you find a security issue in this library itself (not in iRobot's
service), please open a GitHub issue. There's no dedicated security
contact yet — this is a small, single-maintainer draft project.

## Credential handling — what this library does and doesn't do

- **Nothing is logged.** Username/password are used once during login
  and then discarded; only the resulting session token/credentials are
  kept in memory for the connection's lifetime.
- **`auto_refresh=True`** on `PrimeFactory.create_prime_robot()` keeps
  your username and password in a closure for the lifetime of the
  `PrimeRobot` object, so it can re-log-in before the MQTT token
  expires. This is an explicit tradeoff: convenience (no manual
  re-login) against keeping credentials in memory longer than the
  default. It's opt-in and off by default for that reason.
- **TLS is verified properly** for the cloud connection (via `certifi`)
  — this is a genuine point of difference from `roombapy`'s local-MQTT
  client, which disables certificate verification (`ssl.CERT_NONE`),
  correctly for its local-network use case but not appropriate for a
  connection that goes over the internet. See
  [`docs/internal/ROOMBAPY_COMPARISON.md`](docs/internal/ROOMBAPY_COMPARISON.md).
- **`roombapy_prime.diagnostics`** (the live validation script) never
  writes credentials to its report, and redacts any literal occurrence
  of the username/password from error text as a defense-in-depth
  measure before building the shareable GitHub issue link. Prefer the
  `ROOMBAPY_PRIME_PASSWORD` environment variable over the interactive
  prompt if scripting this — never pass a password as a command-line
  argument (it would land in your shell history).
- **The example scripts** in `examples/` read credentials from
  environment variables for the same reason — none of them accept a
  password as a CLI argument.

### Hardening added after a dedicated security review

- **URL path segments are properly encoded.** Every identifier this
  library embeds into a URL path (BLIDs, map IDs, favorite IDs, etc.)
  is passed through `urllib.parse.quote()` before being interpolated —
  a value containing `/` or `..` can't redirect a request to an
  unintended path. A no-op for any legitimate identifier this API
  actually uses.
- **Credential-bearing fields are excluded from default object
  representations.** `CloudCredentials`, `ConnectionToken`, and
  `RobotLoginEntry` mark their secret fields `repr=False`, so an
  accidental `print()`/log statement/exception traceback involving
  one of these objects won't leak the actual secret value.
- **`--dump-config`'s redaction covers both literal credential values
  and a specific list of known-sensitive field names** (password,
  access keys, session/IoT tokens, per-device certificates, precise
  location data, and more) — reviewed and expanded as new
  credential-bearing models were added, not left to whatever the
  first pass happened to cover.

## Known unknowns

**Confirmed live, not just "should work":** login, MQTT/shadow
connection, and basic mission control (`send_simple_command()`) have
all been tested against two independent real accounts — see the
README's confidence table for exactly what is and isn't confirmed.
What genuinely remains unverified against a live server: map editing,
and the write paths for schedules/DND settings. If you're testing one
of those for the first time, standard precautions still apply: use an
account you're comfortable testing with if possible, and prefer a
dedicated throwaway password you can rotate afterward if that's an
option for your iRobot account.
