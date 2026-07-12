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
  [`docs/ROOMBAPY_COMPARISON.md`](docs/ROOMBAPY_COMPARISON.md).
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

## Known unknowns

This library's authentication and signing code has never been run
against a real account (see the README's confidence table). If you're
the first to try it, standard precautions apply: use an account
you're comfortable testing with if possible, and prefer a dedicated
throwaway password you can rotate afterward if that's an option for
your iRobot account.
