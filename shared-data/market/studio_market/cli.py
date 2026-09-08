"""Shared market data maintenance; numeric and text have separate data contracts."""
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print("studio-market <numeric|text|pipeline> <action> [arguments]")
        return 0
    group = args.pop(0)
    if group == "numeric":
        from .numeric.cli import main as command
    elif group == "text":
        from .text.cli import main as command
    elif group == "pipeline":
        from .pipeline import main as command
    else:
        raise SystemExit("Expected numeric, text or pipeline")
    return command(args)


if __name__ == "__main__":
    raise SystemExit(main())
