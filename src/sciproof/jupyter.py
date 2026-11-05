"""SciProof Jupyter / IPython integration.

Enables reproducible execution directly inside computational Jupyter notebooks::

    %load_ext sciproof.jupyter

    %%sciproof --name model_training --output metrics.json
    import json
    metrics = {"accuracy": 0.942, "loss": 0.038}
    with open("metrics.json", "w") as f:
        json.dump(metrics, f)

The cell is executed within the user namespace and verified outputs are
recorded into a standard SciProof manifest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


def register_sciproof_magics(ipython: Any) -> None:
    """Register %%sciproof cell magic with the active IPython instance."""
    from IPython.core.magic import register_cell_magic

    from sciproof.capture import capture

    @register_cell_magic
    def sciproof(line: str, cell: str) -> Any:
        parser = argparse.ArgumentParser(prog="%%sciproof", add_help=False)
        parser.add_argument("--name", default="cell_experiment")
        parser.add_argument("--output", action="append", default=[])
        parser.add_argument("--input", action="append", default=[])
        parser.add_argument("--manifest-dir", default=".")
        args, _ = parser.parse_known_args(line.split())

        # Execute cell within the user namespace
        exec(cell, ipython.user_ns)  # noqa: S102

        base = Path(args.manifest_dir).resolve()
        res = capture(
            name=args.name,
            argv=[sys.executable, "-c", "# cell executed via Jupyter %%sciproof magic"],
            base=base,
            inputs=args.input,
            outputs=args.output,
        )
        print(f"[sciproof] Captured {res.manifest_path.name}")
        return None


def load_ipython_extension(ipython: Any) -> None:
    """Called by IPython when '%load_ext sciproof.jupyter' is run."""
    register_sciproof_magics(ipython)
