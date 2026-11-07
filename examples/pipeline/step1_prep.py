"""Step 1 (prep): turn raw measurements into a feature table.

The raw records are embedded below so the example is fully self-contained:
in a real workflow they would be read from an instrument export or a lab
notebook CSV. One record (S09) has a missing width; preparation drops it,
which is the kind of silent data change this example later proves did or
did not happen, via the recorded hashes.

Run directly:

    python step1_prep.py

or under SciProof, as shown in README.md. Writes ``processed_features.csv``
to the current directory. Standard library only, fully deterministic:
the same input always produces byte-identical output.
"""

from __future__ import annotations

import csv
from pathlib import Path

OUTPUT = Path("processed_features.csv")

# Raw input: (sample_id, length_cm, width_cm, species). ``None`` = not measured.
RAW_RECORDS: list[tuple[str, float | None, float | None, str]] = [
    ("S01", 2.1, 1.0, "alpha"),
    ("S02", 2.4, 1.2, "alpha"),
    ("S03", 1.9, 0.9, "alpha"),
    ("S04", 2.2, 1.1, "alpha"),
    ("S05", 5.8, 2.9, "beta"),
    ("S06", 6.2, 3.1, "beta"),
    ("S07", 5.5, 2.7, "beta"),
    ("S08", 6.0, 3.3, "beta"),
    ("S09", 3.1, None, "alpha"),
]

HEADER = ["sample_id", "length_cm", "width_cm", "area_cm2", "aspect_ratio", "species"]


def main() -> int:
    rows: list[list[str]] = []
    dropped = 0
    for sample_id, length, width, species in sorted(RAW_RECORDS):
        if length is None or width is None:
            dropped += 1
            continue
        rows.append(
            [
                sample_id,
                f"{length:.4f}",
                f"{width:.4f}",
                f"{length * width:.4f}",
                f"{length / width:.4f}",
                species,
            ]
        )

    # lineterminator="\n" keeps the file byte-identical on every platform,
    # which matters because SciProof compares SHA-256 hashes of this file.
    with OUTPUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)

    print(f"wrote {OUTPUT} ({len(rows)} rows, {dropped} incomplete record dropped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
