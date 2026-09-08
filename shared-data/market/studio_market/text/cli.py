"""Local import and read-only inspection of the Studio text corpus."""

from argparse import ArgumentParser
from datetime import datetime
import json

from .store import TextStore


def main(argv=None) -> int:
    parser = ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    importer = subparsers.add_parser("import-bundle")
    importer.add_argument("path")
    importer.add_argument("--expected-sha256")
    pull = subparsers.add_parser("pull-bundle")
    pull.add_argument("--host", required=True)
    pull.add_argument("--remote-path", required=True)
    search = subparsers.add_parser("search")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--offset", type=int, default=0)
    search.add_argument("--as-of", type=datetime.fromisoformat)
    read = subparsers.add_parser("read")
    read.add_argument("source_id")
    subparsers.add_parser("coverage")
    args = parser.parse_args(argv)
    store = TextStore()
    try:
        if args.command == "import-bundle":
            result = store.import_bundle(args.path, expected_sha256=args.expected_sha256)
        elif args.command == "pull-bundle":
            from .delivery import pull_bundle
            result = pull_bundle(store, host=args.host, remote_path=args.remote_path)
        elif args.command == "search":
            result = store.search(args.query, limit=args.limit, offset=args.offset, as_of=args.as_of)
        elif args.command == "read":
            result = store.read(args.source_id)
        else:
            result = store.get_coverage()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
