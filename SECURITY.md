# Security

UDeep reads files that come from anyone: gain-map JPEGs, 16-bit PNGs and the XMP metadata inside them. A file that
makes one of its readers crash, hang, use far more memory than the image it holds, or read or write anything
outside what it was given is a security fault, and is to be reported privately.

What any reader of these files should guard against, whoever wrote it, is in section 16 of the profile
(`profile/udeep-figure-profile-v0.3.md`). It also says what the digests do not do: they tie a file to its data and
to its SDR figure, and do not show who made it.

## Reporting one

Use GitHub's private reporting for this repository:
<https://github.com/ultradeeprange/udeep/security/advisories/new> (or the **Security** tab, then **Report a
vulnerability**). Please do not open a public issue for it. Give the function that misbehaves, what it did, and the smallest file that makes
it happen (attach it, or say how to make it from a valid file).

A report is acknowledged within a week. This is a project kept by one person, so a fix can take longer; you will
be told what is happening, and credited in the changelog unless you would rather not be. A fix is disclosed in a
GitHub security advisory and in the changelog when it is published; the aim is to do that within 90 days of the
report.

## What is in place

- The readers refuse a file that claims more pixels than its data could hold, before anything makes room for
  them, and refuse a damaged file with an error rather than a crash. Tests that damage valid files thousands of
  times run with every build (`core/tests/malformed.rs`, `tests/test_malformed.py`), and the Rust readers have been
  run under a coverage-guided fuzzer (CHANGELOG, 2026-10-14).
- The metadata parser refuses XML with a document-type declaration, so entity expansion and external entities
  never reach the XML library.
- The Rust core's dependencies are checked on every push for published security advisories, licences and sources
  (`core/deny.toml`). The GitHub actions the workflows use are pinned to exact commits.

Only the current `main` is looked after: there is no released version yet.
