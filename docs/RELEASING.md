# Releasing MCUscope to PyPI

The one rule that shapes everything else: **a PyPI version number is single-use forever.**
Deleting or yanking a release does not free the number, so a botched upload costs you that version permanently.
Every step below exists to make the first upload of a version boring.

`.github/workflows/release.yml` does the work.
Nothing here needs a stored API token: publishing uses PyPI trusted publishing (OIDC), so the credential is minted per run and expires.

## One-time setup

These are browser steps on your accounts; they cannot be done from the repo.

- [ ] A [PyPI](https://pypi.org/) account with 2FA.
- [ ] Create the GitHub environment `pypi` under **Settings > Environments**.
  Consider adding yourself as a **required reviewer**, so a tag push pauses for approval instead of publishing straight away.
- [ ] Register a **pending publisher** on PyPI (Account > Publishing).
  It has to be "pending" rather than a normal trusted publisher because the `mcuscope` project does not exist on the index yet, so there is nothing to attach a publisher to:
    - PyPI project name: `mcuscope`
    - Owner: `dwatman`
    - Repository: `mcuscope`
    - Workflow: `release.yml`
    - Environment: `pypi`

The environment name must match exactly, in both the PyPI publisher config and GitHub, or the upload is rejected with a confusing OIDC error.

The name `mcuscope` was unclaimed on PyPI as of 2026-07-28.
Registering the pending publisher does not reserve it; the first successful upload does.

TestPyPI is deliberately not used.
In `0.x` a bad release costs nothing but the next patch number, which is cheaper than maintaining a second account and a second pending publisher for a rehearsal.

## Per-release checklist

- [ ] Decide the version.
  Stay on `0.x` while `docs/SPEC.md` can still change: the REST API, wire protocol and CLI exit codes are a published contract, and `1.0.0` is a promise to freeze them.
- [ ] Bump `host/mcuscope/__init__.py` (`__version__`).
  This is the only place a version is written; the wheel name, `mcu --version` and the PyPI metadata all derive from it.
- [ ] Roll `CHANGELOG.md`:
    - [ ] turn `## [Unreleased]` into `## [<version>] - <YYYY-MM-DD>` and open a fresh empty `[Unreleased]` above it.
    - [ ] add the two link references at the bottom: `[Unreleased]` comparing against the new tag, `[<version>]` pointing at its release.
- [ ] Check whether anything in `README.md` or `host/README.md` has gone stale.
  Remember `host/README.md` is the PyPI long description, so anything wrong there is served on the PyPI page itself, and its images need absolute URLs.
- [ ] Confirm CI is green on the commit you are about to tag.
- [ ] Commit the above.

## Dry run

- [ ] Run the **Release** workflow manually (Actions > Release > Run workflow).
  It builds, runs the tests, lint, the package-data sentinel check and `twine check`, then stops without uploading.
  Nothing is published and no PyPI credential is used.
- [ ] Download the `release-dist` artifact from that run if you want to install the exact wheel locally and smoke it:

```bash
pip install ./mcuscope-<version>-py3-none-any.whl
mcuscoped --sim        # then open the printed URL, confirm the web UI loads
mcu --version
```

The web UI check is the one that matters: it is package data rather than importable code, so a wheel missing it installs and imports perfectly and only breaks when a browser opens it.

The dry run's `twine check` runs the newest twine and packaging, while the publish action ships its own pinned copies, so a metadata version the build backend has just started emitting can pass the dry run and fail the upload step.
Real instance 2026-08-31: hatchling 1.32 emitted `Metadata-Version: 2.5`, action v1.14.1 rejected it; keep `gh-action-pypi-publish` at a release newer than the hatchling in use.

## Release

- [ ] Tag and push:

```bash
git tag v<version>
git push origin v<version>
```

That triggers the workflow, which refuses to continue unless `__version__` matches the tag, then runs the tests, lint, the package-data sentinel check and `twine check` before publishing.
If you added a required reviewer to the `pypi` environment, approve the run when it pauses.

## After

- [ ] Check `https://pypi.org/project/mcuscope/`.
- [ ] Install clean from the real index and smoke it again:

```bash
uv tool install mcuscope     # or: pipx install mcuscope
mcuscoped --sim
```

The GitHub release is created by the workflow itself (notes extracted from that version's `CHANGELOG.md` section, wheel and sdist attached), so there is nothing to write by hand.
Just check it looks right on the releases page.

## If it goes wrong

You can **yank** a release, which hides it from new installs while leaving it available to anything that pinned it.
You cannot re-upload the same version with fixed content, and you cannot free the number by deleting it.
The fix for a bad release is always to yank it and publish the next patch version.
