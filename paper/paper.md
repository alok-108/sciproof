---
title: 'SciProof: Machine-Verifiable Computational Evidence and Reproducibility for Scientific Experiments'
tags:
  - Python
  - reproducibility
  - open science
  - research software
  - provenance
  - artifact evaluation
authors:
  - name: Alok Pandey
    orcid: 0009-0007-7867-5939
    affiliation: 1
affiliations:
 - name: HayagrivAI Labs
   index: 1
date: 27 October 2026
bibliography: paper.bib
---

# Summary

Computational experiments across scientific domains frequently fail independent replication attempts [@baker2016reproducibility; @peng2011reproducible]. Even when research code is made publicly available, subtle differences in operating systems, minor software dependency updates, or hardware-specific floating-point arithmetic frequently cause re-execution to produce numbers that diverge from published manuscripts [@stodden2018empirical].

`SciProof` is a lightweight, zero-dependency Python tool designed to capture computational experiments and produce tamper-evident, verifiable evidence manifests. It computes cryptographic SHA-256 digests of all declared input datasets and generated outputs, records granular runtime execution metadata, and provides automated sandbox re-execution. To accommodate the practical reality of floating-point drift across heterogeneous hardware architectures, `SciProof` includes an automated calibration routine that empirically measures output variance across repeated runs, allowing reviewers and continuous integration (CI) workflows to distinguish acceptable numerical variation from computational errors.

# Statement of Need

Academic venues and funding agencies increasingly mandate code and data sharing to combat irreproducibility. However, verifying that research code reproduces published claims remains operationally challenging in practice:

1. **Containers vs. Verification:** Container systems (such as Docker or Singularity) encapsulate software environments, but they treat executed code as a black box. They do not inspect, parse, or verify whether numerical metrics produced in the container match the specific values reported in a paper.
2. **Infrastructure Overhead:** Production workflow orchestrators and data versioning tools (e.g., MLflow, DVC) require external tracking servers, object storage backends, or intrusive codebase refactoring. This operational burden often deters researchers from adopting rigorous provenance tracking.
3. **Paper Number Drift:** A ubiquitous failure mode in computational science occurs when experimental code is updated during paper revisions, but authors forget to manually synchronize updated metrics across LaTeX tables and figures, leading to silent discrepancies in published work.

`SciProof` resolves these challenges by operating non-invasively on standard scripts without requiring modifications to user code. It executes entirely locally, writes transparent JSON manifests, and exports verified values directly into LaTeX macros to eliminate paper number drift.

# State of the Field

Prior tools have addressed aspects of computational reproducibility from different perspectives:

* **System Call Interception:** Tools like ReproZip [@chirigati2016reprozip] intercept Linux system calls via `ptrace` to bundle dependencies into a self-contained archive. While effective for Linux userland packaging, this approach is tightly coupled to Linux kernels and does not support multi-run numerical tolerance assertions.
* **Large-Scale Workflow Engines:** Systems such as Nextflow and Snakemake manage complex computational pipelines across high-performance computing clusters. However, their primary objective is task scheduling and distributed execution rather than lightweight verification of single-run experimental claims.
* **Cryptographic Attestation Frameworks:** Frameworks such as in-toto [@kupferschmidt2019intoto] provide software supply-chain integrity. `SciProof` shares these cryptographic integrity principles and supports both in-toto attestations and RO-Crate [@soylu2022ro-crate] export, but tailors its command-line interface specifically for scientific artifact evaluation.

### Build vs. Contribute Justification
Rather than contributing extensions to existing workflow engines or MLOps frameworks, `SciProof` was designed from first principles as an independent, standalone tool. Existing platforms impose heavy infrastructure dependencies (such as container runtimes, remote storage buckets, or daemon services) that are incompatible with lightweight artifact evaluation workflows. Building `SciProof` as an unopinionated CLI tool ensures it can run without elevated permissions in any standard scientific environment, ranging from local laptops to air-gapped compute nodes.

# Software Design

The architecture of `SciProof` is guided by three explicit engineering trade-offs:

1. **Zero External Runtime Dependencies:** To guarantee instant installation in restricted high-performance computing (HPC) environments without compilation toolchains or network access, `SciProof` is implemented exclusively using the Python standard library. Optional integration with libraries like NumPy is handled gracefully via dynamic runtime discovery.
2. **Deterministic Manifest Serialization:** Cryptographic digests must be reproducible across operating systems. `SciProof` uses strictly ordered JSON serialization (sorted dictionary keys, compact separators, UTF-8 encoding) and normalized relative path representations, preventing false-positive integrity failures caused by filesystem or formatting discrepancies.
3. **Cross-Platform Process Sandboxing:** Handling subprocess lifecycle reliably across platforms is challenging. `SciProof` implements platform-specific process management: on POSIX systems, commands run in dedicated process groups (`os.killpg`) to guarantee clean termination of process subtrees, whereas on Windows, process trees are managed via targeted task termination. Sandbox verification isolates newly generated files within temporary scratch directories, ensuring original workspace files are never inadvertently overwritten during review.

# Core Workflow

A standard verification lifecycle with `SciProof` consists of three stages:

```
[Experiment Script] ---> sciproof record ---> [Evidence Manifest (.json)]
                                                        |
                                                        v
                                               sciproof calibrate
                                                        |
                                                        v
[Peer Reviewer / CI] <--- sciproof verify <--- [Calibrated Tolerances]
```

### 1. Recording an Experiment
A researcher captures their experiment using the `record` subcommand:

```bash
sciproof record --name benchmark --input data.csv -- python train.py
```

`SciProof` executes the script in a supervised child process, computes SHA-256 digests of all declared inputs and outputs, and writes an evidence manifest (`benchmark.sciproof.json`) containing execution metadata (CPU architecture, OS platform, Python version, runtime duration, and exit status).

### 2. Calibrating Numerical Tolerances
Because identical scientific algorithms can produce minor numerical variations across different CPU vector instructions or BLAS backends, strict bitwise equality is often too brittle for scientific validation. The `calibrate` command re-executes the experiment across multiple runs to measure observed variance across numerical formats (JSON, CSV, and NumPy `.npy` arrays):

```bash
sciproof calibrate benchmark.sciproof.json --runs 5
```

It outputs observed maximum absolute and relative deviations, automatically suggesting realistic absolute (`atol`) and relative (`rtol`) tolerances.

### 3. Reviewer Verification
A conference reviewer or automated CI runner validates the experiment in one command:

```bash
sciproof verify benchmark.sciproof.json --rerun --yes
```

The command verifies cryptographic file digests, re-executes the experiment inside an isolated sandbox, compares newly produced outputs against recorded baselines within calibrated tolerances, and prints an unambiguous `PASS` or `FAIL` verdict.

### 4. Direct Typst & LaTeX Manuscript Exports
To prevent manuscript numbers from drifting when experiments are updated, `SciProof` exports verified manifest metrics directly into Typst (`#let accuracy = 94.2`) or LaTeX definitions:

```bash
sciproof export benchmark.sciproof.json --format typst > evidence.typ
sciproof export benchmark.sciproof.json --format latex > numbers.tex
```

This allows authors to directly `#import "evidence.typ": accuracy` or include `\newcommand{\accuracy}{94.2}` within publication sources, eliminating manual transcription errors.

### 5. Multi-Stage Workflow DAG Verification
Scientific workflows frequently involve multi-stage computational pipelines (e.g., data preprocessing, feature engineering, model training, evaluation). `SciProof` supports linking manifests into a Directed Acyclic Graph (`pipeline.toml` via `sciproof pipeline verify`), enforcing end-to-end cryptographic chain-of-custody to guarantee downstream stages consume authentic, unmodified upstream outputs.

# Research Impact Statement

`SciProof` is specifically designed to support conference and journal reproducibility initiatives:
* **Turnkey Artifact Evaluation:** Provides a standardized appendix template and machine-readable evidence manifest for conferences with Artifact Evaluation tracks (e.g., NeurIPS, ICML, ACM SIGMOD, IEEE S&P). Evaluators can reproduce experimental claims with a single deterministic command.
* **Continuous Scientific Integration:** Includes a pre-configured GitHub Actions workflow that executes automated verification on pull requests, ensuring ongoing codebase modifications do not silently invalidate previously published experimental claims.
* **Community Educational Materials:** Interactive Google Colab quickstart notebooks and end-to-end tutorial pipelines are provided to assist students and laboratories in adopting reproducible computational practices.

# AI Usage Disclosure

No generative artificial intelligence tools were used in writing this manuscript or developing the software algorithms.

# Availability

`SciProof` is distributed under the open-source MIT license. The source code, documentation, test suite, and educational tutorials are publicly accessible on GitHub (`https://github.com/hayagrivai-labs/sciproof`). Releases are published on PyPI (`pip install sciproof`) and permanently archived on Zenodo (DOI: [10.5281/zenodo.23676138](https://doi.org/10.5281/zenodo.23676138)).

# References
