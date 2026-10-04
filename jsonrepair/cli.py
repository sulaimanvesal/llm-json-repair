"""Command-line interface for llm-json-repair."""

from __future__ import annotations

import argparse
import json
import sys

from .repair import loads_repair


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="llm-json-repair",
        description="Repair malformed JSON from LLM outputs (fences, comments, "
        "trailing commas, unquoted keys, single quotes, truncation, trailing prose).",
    )
    p.add_argument(
        "input",
        nargs="?",
        help="Input file (default: stdin). Use - for stdin explicitly.",
    )
    p.add_argument("-o", "--output", help="Write repaired JSON here (default: stdout).")
    p.add_argument(
        "--indent",
        type=int,
        default=2,
        help="Indent for pretty-printed output (default: 2).",
    )
    p.add_argument(
        "--compact", action="store_true", help="Emit compact JSON (no pretty print)."
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Exit 0 if input is valid/repairable JSON, 1 otherwise; print nothing.",
    )
    p.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress the repair report on stderr."
    )
    return p


def read_input(path: str | None) -> str:
    if path is None or path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw = read_input(args.input)

    try:
        obj, report = loads_repair(raw)
    except ValueError as exc:
        if not args.quiet:
            print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.check:
        return 0

    text = json.dumps(obj, indent=None if args.compact else args.indent,
                      ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)

    if not args.quiet:
        if report.applied:
            print(f"repaired: {', '.join(report.applied)}", file=sys.stderr)
        else:
            print("ok: input was already valid JSON", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
