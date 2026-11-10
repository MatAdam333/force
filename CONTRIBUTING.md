# Contributing to Force

Thanks for wanting to help. Bug reports and ideas go through the
[issue forms](https://github.com/zalandemeter/force/issues/new/choose); security problems through
[SECURITY.md](.github/SECURITY.md). For code, open a pull request against `main`: CI runs the tests on it.

## The repository

| Path | What it is |
|---|---|
| `force/` | the app: the Python package behind the `force` command, the daemon and the cockpit |
| `crates/sidebar/` | Rust: the sidebar, the header and the popup dialogs (`force-sidebar`, `force-header`, `force-dialog`) |
| `crates/intro/` | Rust: the startup intro (`force-intro`) and the sources its film is made from |
| `share/` | files the app installs: tmux configs, helper scripts, the installer template, the agent guide (`skill.md`) |
| `systemd/` | the user units |
| `tests/` | the test suite (pytest) |
| `build/`, `scripts/` | the release build: the builder container, `build-release.sh`, the installer filler, the notices generator |
| `website/` | https://useforce.cc: the landing page, the docs page and the tools that build them ([website/README.md](website/README.md)) |
| `docs/` | the design ([SPEC.md](docs/SPEC.md), cited in the code as `SPEC §N`) and [OPERATING.md](docs/OPERATING.md) |
| `.github/` | CI, release and website workflows, issue forms |

## Working on it

You need Python 3.11+, tmux, git and Rust (stable).

```sh
./install.sh               # developer install from this checkout: test venv, then ./deploy.sh and setup
./deploy.sh                # after a change: tests, build, a new release beside the old ones, atomic switch
```

Running sessions keep running through a deploy. `./deploy.sh --rollback` goes back one release.

Tests:

```sh
python3 -m pytest -q                                              # ~2 minutes; tests never touch your live Force
cargo test --manifest-path crates/sidebar/Cargo.toml
cargo test --manifest-path crates/intro/Cargo.toml
python3 website/tools/build.py /tmp/site                         # the website builds
```

Tests run their own tmux servers (sockets named `forcetest-*`) and never use the live ones.

After a change to a `Cargo.lock`, regenerate `THIRD-PARTY-NOTICES.md` with `scripts/notices.sh`
(`tests/test_notices.py` fails until you do).

## Releases

1. Set the version in `pyproject.toml` (plain `X.Y.Z`) and merge to `main`.
2. Tag it and push the tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The release workflow checks that the tag matches `pyproject.toml`, runs the tests, builds the tarball in the
builder container (static binaries, Force's own tmux), attests its provenance, publishes the GitHub Release with
the tarball, its `.sha256` and `install.sh`, and redeploys the website so that
`curl -fsSL https://useforce.cc/install.sh | sh` installs the new version. Installed copies find it with
`force update` and in their daily check.

To build a release locally: `scripts/build-release.sh [X.Y.Z]` (needs Docker; output in `build/out/dist`).
