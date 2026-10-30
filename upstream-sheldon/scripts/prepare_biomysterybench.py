"""Import the existing official preview download without model calls."""
import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sheldon.core.io import data_root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=ROOT.parent / "baseline")
    parser.add_argument("--output", type=Path, default=data_root() / "biomysterybench")
    args = parser.parse_args()
    source = args.baseline.resolve()
    output = args.output.resolve()
    if output.exists():
        parser.error(f"Refusing to overwrite {output}")
    csv_path = source / "source/problems.csv"
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 5:
        parser.error("Expected the five-question preview")
    directories = {}
    for row in rows:
        tid = row["id"]
        if Path(tid).name != tid or tid in (".", ".."):
            parser.error("Invalid task ID")
        matches = [p for p in (source / "extracted").rglob(tid) if p.is_dir()]
        if len(matches) != 1 or any(p.is_symlink() for p in matches[0].rglob("*")):
            parser.error(f"Missing, ambiguous, or unsafe data for {tid}")
        directories[tid] = matches[0]
    output.mkdir(parents=True)
    shutil.copy2(csv_path, output / "problems.csv")
    for tid, directory in directories.items():
        shutil.copytree(directory, output / "files" / tid)
    metadata = source / "source/.cache/huggingface/download/problems.csv.metadata"
    (output / "provenance.json").write_text(json.dumps({
        "dataset": "Anthropic/BioMysteryBench-preview",
        "revision": metadata.read_text().splitlines()[0] if metadata.exists() else None,
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "task_ids": list(directories),
    }, indent=2))
    print(f"Prepared {len(rows)} questions in {output}")


if __name__ == "__main__":
    main()
