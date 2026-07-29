# Contributing

Thanks for your interest in contributing.

**The full contributor guide — coding standards, architecture rules, how to
add a new spec Decision, how to preserve determinism, the review checklist,
and commit standards — lives at [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md).**
Read that before opening a pull request; it is not duplicated here.

## Quick start

1. Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) first — it explains
   why this codebase is shaped the way it is (determinism, the canonical vs.
   legacy engine split, the single-forward-pass state machine).
2. Set up your environment: see the [README](README.md#installation).
3. Any change to trading logic must trace to an approved decision in
   [`SMC_SPECIFICATION.md`](SMC_SPECIFICATION.md) — this project does not
   guess business logic. If you're proposing new SMC behaviour, the
   specification is amended and approved *before* any code is written.
4. Run the full test suite (`make test` or `pytest`) before and after your
   change, and follow the golden-file discipline in
   [`docs/TESTING.md`](docs/TESTING.md) if your change touches pipeline
   output.

## Reporting bugs / requesting features

Please use the issue templates under `.github/ISSUE_TEMPLATE/` — they ask for
exactly the information needed to reproduce or evaluate a request quickly.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

## Security issues

Do not open a public issue for a suspected vulnerability — see
[`SECURITY.md`](SECURITY.md) for private reporting instructions.
