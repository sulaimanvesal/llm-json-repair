"""Runnable demo for llm-json-repair — zero API keys, pure stdlib."""

import json
import sys

sys.path.insert(0, "/home/hatch/workspace/github-builds/llm-json-repair")

from jsonrepair import loads_repair

CASES = [
    ("Markdown fence", '```json\n{"model": "demo", "temp": 0.7}\n```'),
    ("Comments (JSONC)", '{\n  // model config\n  "name": "x", /* inline */ "n": 1 # trailing\n}'),
    ("Trailing commas", '{"a": 1, "b": [1, 2,],}'),
    ("Unquoted keys", "{name: 'demo', retries: 3}"),
    ("Single quotes", "{'msg': 'she said \"hi\"'}"),
    ("NaN / undefined", '{"score": NaN, "extra": undefined}'),
    ("Truncated output", '{"a": {"b": [1, 2'),
    ("Unterminated string", '{"status": "ok'),
    ("Trailing prose", '{"done": true}\nHope this helps!'),
]


def main() -> int:
    failures = 0
    for title, broken in CASES:
        print(f"--- {title} ---")
        print(f"in : {broken!r}")
        try:
            obj, report = loads_repair(broken)
        except ValueError as exc:
            print(f"FAIL: {exc}")
            failures += 1
            continue
        print(f"out: {json.dumps(obj)}")
        print(f"fix: {', '.join(report.applied) or '(none — already valid)'}")
        print()
    print(f"{len(CASES) - failures}/{len(CASES)} cases repaired successfully.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
