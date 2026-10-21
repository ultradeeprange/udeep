# Releasing

How a version reaches PyPI, so that `pip install "udeep[mpl]"` works on any machine. Each step that publishes
something is done by a person; the workflows build, check and wait.

## Once, before the first release

1. **PyPI.** On pypi.org, signed in as the project's owner: *Your account → Publishing → Add a new pending
   publisher*, with PyPI project name `udeep`, owner `ultradeeprange`, repository `udeep`, workflow `publish.yml`,
   environment `pypi`. PyPI then trusts that workflow, and only it, to create the project and upload to it. No
   token is stored anywhere.
2. **GitHub.** In the repository's settings, *Environments → pypi*: add the owner as a required reviewer, so that
   an upload waits for approval.
3. **Zenodo (optional).** To have each GitHub release archived with a DOI, switch on the repository in Zenodo's
   GitHub settings before publishing the release.

## Each release

1. **The version.** It follows the profile draft the code implements (CHANGELOG.md): 0.3.x for the v0.3 draft.
   Tag `v0.3.0` exists, so the next is `0.3.1`. Change it in `pyproject.toml`, `core/Cargo.toml` (then
   `cargo update -p udeep-core` for `core/Cargo.lock`), `julia/UDeepCore/Project.toml` and `CITATION.cff`, and the
   number `udeep_version()` returns in `core/src/ffi.rs` (0.3.1 is 301) with the tests that check it
   (`grep -rn "== 300\|\"300\"" tests`).
2. **The words.** In `README.md`, replace the install line from git with `pip install "udeep[mpl]"` and remove
   "no PyPI release yet"; on the About page, the row for installable packages. In `CHANGELOG.md`, put the version
   and date where it says Unreleased.
3. **The tag.** Push `v0.3.1`. The `wheels` workflow then builds the wheels for Linux, Windows and macOS and the
   source distribution, checks the page PyPI will show, installs every package on fresh machines as a newcomer
   would (`tools/first_install.py`, then `python -m udeep.selftest`), and attaches the packages to a **draft**
   release. If any of that fails, there is no draft.
4. **Look.** Read the draft release, and the `first-install` jobs' output: which machines took a ready-made wheel,
   which built from source, and that every self-test passed.
5. **Publish.** Publish the draft release on GitHub. The `publish` workflow downloads its packages, waits for the
   `pypi` environment's approval, and uploads them.
6. **Check from outside.** On any machine with Python 3.11 or later and nothing else:

       python first_install.py                  (tools/first_install.py, copied there on its own)

   or `pip install "udeep[mpl]"` and `python -m udeep.selftest`.

A release on PyPI cannot be replaced: a fault found afterwards is fixed in the next version.
