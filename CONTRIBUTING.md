# Contributing to UDeep

UDeep is a draft standard and its reference tools. It is a research preview, kept by one person, and the most
useful help is not always code.

## Ways to help, most useful first

1. **Report what your screen shows.** Run the [device checks](https://ultradeeprange.github.io/udeep/check/) and
   [open a device report](https://github.com/ultradeeprange/udeep/issues/new?template=device-report.yml). It takes
   about two minutes. The profile's evidence so far comes from very few screens.
2. **Read the draft profile and say where it is wrong or unclear.** The text is
   [`profile/udeep-figure-profile-v0.3.md`](profile/udeep-figure-profile-v0.3.md).
   [Comment on the profile](https://github.com/ultradeeprange/udeep/issues/new?template=profile-comment.yml), naming
   the section.
3. **Try it on your own data** and report what did not work, or what the figure hid that it should have shown.
4. **Code.** See below.

Questions that are not faults belong in [Discussions](https://github.com/ultradeeprange/udeep/discussions).

## How evidence is kept

A device report becomes one record in [`evidence/devices/`](evidence/), and [`evidence/README.md`](evidence/README.md)
is the table made from the records. A record holds what was reported and nothing else: a value the report did not
give is written as not recorded, and is never filled in afterwards. Whether a result counts is worked out from the
page's controls, as the profile's Annex A requires.

## Working on the code

The Python package is the reference implementation. The Rust core, the Julia package and the JavaScript port are
checked against it through the golden vectors in `src/udeep/golden.json`.

```sh
python -m pip install -e ".[test]"
cargo build --release --features jpeg,png --manifest-path core/Cargo.toml    # the Rust core; optional, but most tests use it
python -m pytest
cargo test --release --features jpeg,png --manifest-path core/Cargo.toml
julia --project=julia/UDeepCore -e 'using Pkg; Pkg.test()'
```

Rebuild the core before running the Python tests after any change to the Rust code. `pip install -e .` does not
build it; an ordinary `pip install .` does, where `cargo` is present (see `setup.py`).

A change is ready when:

- **the tests pass**, and a change in behaviour comes with a test that fails without it;
- **the implementations still agree.** If the reference changes what it computes, regenerate the golden vectors
  (`python -m udeep.golden`) and bring the other implementations into line in the same change;
- **the profile and the code say the same thing.** A change to what a file means is a change to the profile, and
  is listed in its Annex E with the date;
- **generated files are regenerated, not edited**: `python tools/build_all.py` remakes them all in the right order
  (the colour tables, the golden vectors, the live view's copies, the profile and reference pages, the evidence
  table and the site's chrome), and GitHub checks on every push that they are current;
- **data in a figure is real or is labelled.** Synthetic and simulated data are marked as such in the figure's
  caption and in the code that makes them. No measurement, citation or device result is invented.

Keep a pull request to one subject. If you are unsure whether something is wanted, open an issue first.

## Tools used in writing a contribution

You may use whatever tools you like, including AI assistants; much of this repository was written with one. You
are responsible for what you submit: that you have read it, that it does what you say, and that you have the right
to contribute it.

## Licences

Code is under the BSD 3-Clause licence ([`LICENSE`](LICENSE)); the profile, the site's text and the documentation
are under CC BY 4.0 ([`LICENSE-docs.txt`](LICENSE-docs.txt)). By contributing you agree that your contribution is
offered under the same licence as the part of the repository it changes.

## Security

A file that makes a reader crash, hang or use far more memory than it should is a security fault: report it
privately, as [`SECURITY.md`](SECURITY.md) says, not in a public issue.

## Conduct

The [code of conduct](CODE_OF_CONDUCT.md) applies everywhere the project is discussed.
