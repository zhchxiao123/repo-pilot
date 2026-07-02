"""A tiny real CLI: transform text. `python cli.py --upper hello` -> HELLO."""
import argparse


def main() -> int:
    parser = argparse.ArgumentParser(prog="texttool")
    parser.add_argument("text")
    parser.add_argument("--upper", action="store_true")
    args = parser.parse_args()
    out = args.text.upper() if args.upper else args.text.lower()
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
