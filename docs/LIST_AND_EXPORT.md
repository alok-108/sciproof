# Listing manifests and exporting them

## `sciproof list`: the directory is the ledger

There is no database. A manifest is a file, so a directory of manifests is the history of your
experiments.

```bash
sciproof list                 # the manifests in the current directory, newest first
sciproof list -r results/     # also in subdirectories (hidden directories are skipped)
sciproof list --check         # verify every manifest (integrity only, nothing is executed)
```

```
  candidate.sciproof.json  captured 2026-10-19T05:37:44+00:00  exit 0  in 2  out 1  claims 0  PASS
  baseline.sciproof.json   captured 2026-10-19T05:37:43+00:00  exit 0  in 2  out 1  claims 0  FAIL

summary: 2 manifest(s): 1 PASS, 1 FAIL
```

Each line shows the capture time, the exit code of the recorded run, the number of inputs, outputs
and claims, whether a `.sig` file sits next to it, and the Git commit (with `+dirty` if the tree had
uncommitted changes). A file that is not a valid manifest is listed as `INVALID` with the reason;
it does not stop the listing. Symbolic links are not followed, and at most 1,000 manifests are
read.

With `--check`, the exit status is the worst result: `0` if all `PASS`, `1` if any `FAIL` or is
invalid, `2` if any is `INCONCLUSIVE`. This makes `sciproof list --check` a one-line check of every
recorded experiment in a repository. `--format json` and `--format markdown` are available.

## `sciproof export`: for other tools

```bash
sciproof export baseline.sciproof.json -o baseline.intoto.json
```

writes the manifest as an [in-toto Statement v1](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md):

* `subject`: the output files with their SHA-256 (the files of a directory output are listed as
  `dir/file`; outputs that were absent are left out).
* `predicateType`: `https://hayagrivai-labs.github.io/sciproof/docs/export/#experiment-v1`, a type
  defined by SciProof. It does not claim to be SLSA provenance: SciProof records an experiment, not
  a build.
* `predicate`: the experiment name and capture time, the SHA-256 of the manifest file, the command,
  exit code and duration, the inputs (`materials`) with their hashes, Python, platform, recorded
  environment variables and packages, the Git state, how each output is compared (`exact` or the
  numeric tolerance), and the claims as text.

**The Statement is not signed and is not an attestation by itself.** In-toto Statements are
normally wrapped in a signed envelope (DSSE) by the tool that attests them; that step is left to
such a tool, and `sciproof sign` signs the manifest itself. The Statement keeps the hashes but not
the verdict: run `sciproof verify` on the manifest for that.

```bash
sciproof export baseline.sciproof.json --format openlineage
```

writes the run as two [OpenLineage](https://openlineage.io/) run events, `START` and then
`COMPLETE` (or `FAIL` if the recorded command failed), as a JSON array:

* `run.runId` is a UUID (version 5) derived from the SHA-256 of the manifest, so exporting the
  same manifest again gives the same run; `eventTime` of the end is the capture time and the start
  is that minus the recorded duration (derived, not measured separately).
* `job` is `{namespace: "sciproof", name: <experiment name>}`.
* `inputs` and `outputs` are datasets in the namespace `sciproof-files`, named by their path
  relative to the manifest's directory; a directory is one dataset, and outputs that were absent
  are left out.
* `schemaURL` is the one shown in the OpenLineage specification's own run event example.

This is **lineage only**: which files were read and written, and whether the command succeeded. It
carries no hashes and no verdict, because no standard facet for them was verified; use the in-toto
export or the manifest for that.

An export to RO-Crate (Process Run Crate) is not implemented; see the
[design note](DESIGN_RO_CRATE.md) for the open questions.

## `--format dsse`: in-toto Statement in DSSE envelope

Wrap the manifest in a Dead Simple Signing Envelope (DSSE v1.0) with payload type `application/vnd.in-toto+json`:

```bash
sciproof export baseline.sciproof.json --format dsse -o baseline.dsse.json
```

This output is ready for direct keyless signing using Sigstore / Cosign (`cosign sign-blob-attestation`) or GitHub Attestations.

## `--format typst`: numbers for a Typst paper

Typst is the modern alternative to LaTeX for scientific papers. With `--format typst`, verified experiment outputs are exported as native `#let` variables:

```bash
sciproof export baseline.sciproof.json --format typst -o evidence.typ \
  --value accuracy out/metrics.json '$.accuracy' \
  --value loss     out/metrics.json '$.loss'
```

Generated `evidence.typ`:
```typst
// SciProof 0.6.0: values of baseline.sciproof.json (integrity verification: PASS)
#let accuracy = 0.93
#let loss = 0.0025
```

In the Typst paper: `#import "evidence.typ": accuracy, loss`. Special float values (`calc.nan`, `calc.inf`, `-calc.inf`) are formatted correctly for Typst's math library.

## `--format latex`: numbers for a paper

A number typed into a paper by hand can drift away from the experiment that produced it. With
`--format latex` the numbers come out of the recorded outputs as LaTeX macros:

```bash
sciproof export baseline.sciproof.json --format latex -o numbers.tex \
  --value accuracy out/metrics.json '$.accuracy' \
  --value loss     out/metrics.json '$.loss'
```

```latex
% SciProof 0.4.1: values of baseline.sciproof.json (integrity verification: PASS)
% Generated by 'sciproof export --format latex'; do not edit by hand.
\newcommand{\accuracy}{0.93}
\newcommand{\loss}{$2.5\times 10^{-3}$}
```

In the paper: `\input{numbers.tex}` and then `accuracy \accuracy{}`.

* `--value NAME OUTPUT LOCATION`: `NAME` is the macro name (1 to 40 letters; `\newcommand` stops
  with an error if it is already defined), `OUTPUT` a declared `.json`, `.csv` or `.tsv` output,
  `LOCATION` the same form as in [claims](CLAIMS.md) (`$.key[0].name`, or `r2c1` for a table).
  If `--value` is omitted, value locations across declared numeric outputs are automatically exported
  (up to 200 values), using location keys for macro names, qualifying collisions with output file stems,
  and disambiguating further collisions with alphabetical suffixes.
* **Nothing is written unless the integrity verification of the manifest is `PASS`**: if an output
  was modified since the capture, the export stops with an error and says why. Nothing is
  executed.
* Text is escaped for LaTeX. Floats use at most ten significant digits, and very large or small
  ones are written as `$m\times 10^{e}$`.
* A passing verification shows that the file is what was recorded, not that the number is right,
  and the macro does not carry the manifest with it: commit `numbers.tex` and the manifest
  together, and run `sciproof verify` in the same CI job that builds the paper.

## Python

```python
import sciproof

for entry in sciproof.list_manifests("results", recursive=True, check=True):
    print(entry.file, entry.verdict)
statement = sciproof.export_in_toto("baseline.sciproof.json")  # a dict
dsse_env = sciproof.export_dsse("baseline.sciproof.json")  # a dict
ro_crate = sciproof.export_ro_crate("baseline.sciproof.json")  # a dict
events = sciproof.export_openlineage("baseline.sciproof.json")  # a list of two dicts
typst_vars = sciproof.export_typst("baseline.sciproof.json")  # typst text
```
