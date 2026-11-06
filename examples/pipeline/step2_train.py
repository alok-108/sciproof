"""Step 2 (train): fit a tiny nearest-centroid classifier and score it.

Reads ``processed_features.csv`` (the output of step 1), computes the mean
feature vector ("centroid") of each species, classifies every row by its
nearest centroid, and writes the result to ``metrics.json``.

This is deliberately the smallest honest "training" step: no randomness, no
external dependencies, and the same input always gives the same metrics --
which is exactly what makes the pipeline's chain-of-custody check meaningful.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

INPUT = Path("processed_features.csv")
OUTPUT = Path("metrics.json")
FEATURES = ["length_cm", "width_cm", "area_cm2", "aspect_ratio"]


def load_rows() -> list[dict[str, float | str]]:
    with INPUT.open(encoding="utf-8", newline="") as fh:
        return [
            {
                "species": raw["species"],
                **{name: float(raw[name]) for name in FEATURES},
            }
            for raw in csv.DictReader(fh)
        ]


def centroid(rows: list[dict[str, float | str]]) -> dict[str, float]:
    return {name: sum(float(row[name]) for row in rows) / len(rows) for name in FEATURES}


def distance(row: dict[str, float | str], centre: dict[str, float]) -> float:
    return math.sqrt(sum((float(row[name]) - centre[name]) ** 2 for name in FEATURES))


def main() -> int:
    rows = load_rows()
    species = sorted({str(row["species"]) for row in rows})
    centroids = {
        name: centroid([row for row in rows if row["species"] == name]) for name in species
    }

    correct = sum(
        min(centroids, key=lambda name: distance(row, centroids[name])) == row["species"]
        for row in rows
    )

    metrics = {
        "model": "nearest-centroid",
        "features": FEATURES,
        "samples": len(rows),
        "correct": correct,
        "accuracy": round(correct / len(rows), 4),
        "centroids": {
            name: {feature: round(value, 6) for feature, value in centre.items()}
            for name, centre in centroids.items()
        },
    }
    OUTPUT.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} (accuracy {metrics['accuracy']} on {len(rows)} samples)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
