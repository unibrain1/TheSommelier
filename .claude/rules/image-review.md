# Image review rules

Reviewers and agents read these rules when they check a change to this
repo. The devkit Family B template gives the sections. The CI review reads
this file. Each rule is Blocking or Important.

## Image and Dockerfile

- hadolint must pass on each Dockerfile.
- Pin each base image to a version tag, not `latest`.
- Do not copy a secret into the image. Do not pass a secret as a build
  argument.
- Keep `.dockerignore` up to date, so that the build context holds no
  secret and no local file.

## App and tests

- The `lint` and `test` commands in `.claude/project.md` must pass.
- Important: new functionality has no test. A change to app behavior has a
  test that fails without the change.
- Blocking: a script swallows an error instead of failing loudly.
- Blocking: the JSON data contracts between scripts do not agree.
- Blocking: the pipeline runs a step before the step it depends on.
- Blocking: a script copies a shared utility from `wine_utils.py`.
- Important: an error message goes to stdout instead of stderr.
- Important: a broad exception catch hides a programming error.

## Secrets

- Read each secret from the environment at run time. Do not write a
  secret in the code or in the image.
- Do not read, change, or commit an `.env` file.
- Blocking: a secret or credential can go into a log or the output.
- Blocking: unsanitized input can cause command injection.
- Important: a 1Password reference is used where a literal value is
  expected.

## Docs

- Important: CLAUDE.md is not updated for a new script, env var or
  pipeline change. When a change alters the build, the image tags, or the
  deploy, update CLAUDE.md in the same change.
