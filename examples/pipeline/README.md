# Multi-step pipeline example

A complete, runnable two-step pipeline with an end-to-end chain of custody:

```
step1_prep.py  --(processed_features.csv)-->  step2_train.py  --(metrics.json)
```

- **Step 1 (`prep`)** turns embedded raw measurements into a feature table.
  One raw record is incomplete and is dropped — the kind of quiet data change
  that is hard to notice later without recorded evidence.
- **Step 2 (`train`)** fits a nearest-centroid classifier on the features and
  writes `metrics.json`.

Both steps are recorded with SciProof, and `pipeline.toml` links the two
manifests into a DAG. Verifying the pipeline re-checks every recorded hash
*and* that the file step 2 consumed is byte-identical to the file step 1
produced. If the intermediate file changes in between, verification fails.

Everything runs on the standard library alone and is deterministic: the same
inputs always produce byte-identical outputs.

## Files

| File | What it is |
| --- | --- |
| `step1_prep.py` | Preparation script; writes `processed_features.csv` |
| `step2_train.py` | Training script; reads the CSV, writes `metrics.json` |
| `pipeline.toml` | Pipeline definition linking the two manifests |
| `README.md` | This file |

Generated while you run the example (not committed): `processed_features.csv`,
`metrics.json`, `prep.sciproof.json`, `train.sciproof.json`.

## Run it

You need Python 3.11+ and SciProof installed (`pip install sciproof`, or
`pip install -e .` from a checkout of this repository). Work from this
directory:

```bash
cd examples/pipeline
```

### 1. Record step 1 (prep)

`record` runs the script and detects what it read and wrote, so there is no
`--output` flag to declare:

```bash
sciproof record --name prep -- python step1_prep.py
```

```text
Evidence written to prep.sciproof.json
  inputs found:   1
  outputs, numeric: 1
```

### 2. Record step 2 (train)

```bash
sciproof record --name train --input processed_features.csv -- python step2_train.py
```

```text
Evidence written to train.sciproof.json
  inputs found:   2
  outputs, numeric: 1
```

(`record` prints the equivalent `capture` command with every file declared
explicitly; either form produces the manifests this example uses.)

### 3. Verify the pipeline

```bash
sciproof pipeline verify pipeline.toml
```

```text
SciProof Pipeline: pipeline_example
Overall Verdict: PASS

Steps:
  [PASS] prep (prep.sciproof.json)
  [PASS] train (train.sciproof.json)
```

### 4. Break it on purpose (simulated tampering)

Change a single digit in the intermediate file:

```bash
sed -i 's/S01,2.1000/S01,9.1000/' processed_features.csv
sciproof pipeline verify pipeline.toml
```

Verification now fails (exit status 1): the file on disk no longer matches
the hash recorded by step 1, nor the input hash recorded by step 2, so the
chain of custody between the steps is broken:

```text
SciProof Pipeline: pipeline_example
Overall Verdict: FAIL

Steps:
  [FAIL] prep (prep.sciproof.json)
  [FAIL] train (train.sciproof.json)
```

To restore the example, re-record both steps with `--force` and verify again:

```bash
sciproof record --force --name prep -- python step1_prep.py
sciproof record --force --name train --input processed_features.csv -- python step2_train.py
sciproof pipeline verify pipeline.toml   # PASS again
```

## What this does and does not prove

Verification proves that the files on disk are the files the recorded runs
produced and consumed, and that the steps are linked in the declared order.
It does not prove the classifier is a good model — the nearest-centroid
"training" here is a teaching stand-in, kept trivial on purpose so the
verification story is the interesting part.
