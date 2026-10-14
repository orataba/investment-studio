"""Split the Watchlist test files into two deterministic, disjoint CI jobs."""
import argparse
from pathlib import Path


def select_files(test_root: Path, shard: int) -> list[Path]:
    if shard not in (1, 2):
        raise ValueError("Watchlist CI shard must be 1 or 2")
    files = sorted(test_root.rglob("test_*.py"))
    selected = files[shard - 1::2]
    if not selected:
        raise ValueError("Watchlist CI shard contains no test files")
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shard", type=int, choices=(1, 2))
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[2] / "apps/watchlist/backend"
    for path in select_files(backend / "tests", args.shard):
        print(path.relative_to(backend).as_posix())


if __name__ == "__main__":
    main()
