# Fork distribution and update identity

The distributable source is `https://github.com/carlosesl1/hermes-webui`.
The project retains the original `nesquena/hermes-webui` credits and historical
issue links. Those links are not installation or update targets.

## Guaranteed source path (no published image required)

Clone this fork, select the intended commit, and build locally using the
[README build command](../README.md#build-locally). Review `git remote get-url
origin` and `git rev-parse HEAD` before building. Use a clean checkout, not a
working tree with private overlays. The Dockerfile copies that checkout; it does
not replace the WebUI with an upstream clone. A successful build still depends
on Docker and its external base/package downloads; this is not an offline or
bit-for-bit reproducible build guarantee.

The Dockerfile accepts `HERMES_SOURCE`, `HERMES_REVISION` (full lowercase 40-digit
Git SHA), and `HERMES_VERSION`. It records them in OCI labels
`org.opencontainers.image.{source,revision,version}` and in `api/_version.py` as
`__source__`, `__revision__`, and `__version__`. The existing no-Git version badge
continues to read that file. Builds without revision/version arguments explicitly
record `unknown`; plain Compose builds do not infer the SHA from an excluded
`.git` directory. Use `docker compose build --build-arg HERMES_REVISION="$(git
rev-parse HEAD)" --build-arg HERMES_VERSION="$(git describe --tags --always)"` for
stamped Compose builds (source defaults to this fork).

For an exported source archive, run the same metadata writer with explicit
`HERMES_SOURCE`, `HERMES_REVISION`, `HERMES_VERSION` environment variables:
`python3 scripts/write_distribution_metadata.py api/_version.py`.
Never label modified source with an unchanged clean commit SHA.

Read back a built image without starting the application:

```sh
docker image inspect "$IMAGE" --format '{{json .Config.Labels}}'
docker run --rm --entrypoint python3 "$IMAGE" -c \
  'from pathlib import Path; print(Path("/apptoo/api/_version.py").read_text())'
```

These identify WebUI source, not the effective Hermes Agent revision/provider,
mounted overlays, running assets, or a certified compatibility matrix.

## Publication is not deployment

`Release & Docker` preserves the existing `v*` stable and `exp-v*` experimental
tag-release behavior, including version tags and their respective floating
aliases. Branch pushes alone do not publish anything.

A maintainer may select **Run workflow** (`workflow_dispatch`) to publish the
selected ref as `ghcr.io/carlosesl1/hermes-webui:sha-<full-commit-sha>`. Manual
runs do not create a GitHub Release, change `latest`/`experimental`, or deploy.
The workflow must be present on the default branch for the normal GitHub manual
run UI. Package permissions and a successful run are prerequisites; no existing
published image is asserted here. Standard builds target amd64 and arm64.

A SHA tag identifies source but registry tags are technically mutable (and base
images/dependencies can change on rebuild). For immutable delivery, record the
successful build's **digest** and pull/run `ghcr.io/carlosesl1/hermes-webui@sha256:...`.
Verify the source/revision labels before rollout. Publication, installation,
state backups, compatibility checks and rollback are separate operator actions.

## Update semantics

- Git installs retain the existing `origin`-based fetch/apply path.
- No-Git installs check stable tags only on their baked `__source__`. Legacy
  source exports without that field default to this fork, never the old upstream.
  Explicit invalid or unsupported sources fail closed without a network query.
- Missing matching release tags, SHA-only images, experimental descriptors and
  unavailable metadata do not imply “up to date”; the existing no-Git/manual
  update status remains. The tags API is bounded to its first 100 entries.
- Every shipped Compose topology builds this fork locally; multi-container
  layouts use `hermes-webui:local`, not the upstream WebUI image. The manual
  update banner suggests `docker compose build --pull hermes-webui`; first
  obtain/review the intended source revision in the operator checkout.
- Images cannot Git-update themselves. Build/pull a reviewed replacement and
  recreate using the operator's existing volume/back-up/rollback procedure.
  A push or a successful image publication never changes a running container.

## Offline verification

With isolated `HOME`, `HERMES_HOME`, `HERMES_WEBUI_STATE_DIR` and
`HERMES_DISABLE_LAZY_INSTALLS=1`, run:

```sh
./scripts/test.sh tests/test_distribution_identity.py tests/test_issue4356_no_git_update_check.py
```

These tests mock only HTTP transport and inspect real generated metadata; they
do not certify a registry publication, full Docker build, or runtime startup.
