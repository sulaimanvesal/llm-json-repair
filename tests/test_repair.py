"""Tests for llm-json-repair."""

import json
import subprocess
import sys

import pytest

from jsonrepair import RepairReport, loads_repair, repair_json


def repaired(text):
    obj, report = loads_repair(text)
    assert report.success
    return obj, report


# ---------------------------------------------------------------- valid input
def test_valid_json_passes_through_unchanged():
    src = '{"a": 1, "b": [1, 2, {"c": null}]}'
    out, report = repair_json(src)
    assert json.loads(out) == json.loads(src)
    assert report.applied == []
    assert report.success


# ------------------------------------------------------------------- fences
def test_markdown_fence_stripped():
    obj, report = repaired('```json\n{"a": 1}\n```')
    assert obj == {"a": 1}
    assert "fences" in report.applied


def test_fence_without_language_tag():
    obj, _ = repaired('```\n[1, 2]\n```')
    assert obj == [1, 2]


# ------------------------------------------------------------------ comments
def test_line_comments_removed():
    obj, report = repaired('{\n// a comment\n"a": 1 // trailing\n}')
    assert obj == {"a": 1}
    assert "comments" in report.applied


def test_block_comments_removed():
    obj, _ = repaired('{"a": /* inline */ 1}')
    assert obj == {"a": 1}


def test_hash_comments_removed():
    obj, _ = repaired('# header\n{"a": 1} # trailing')
    assert obj == {"a": 1}


def test_urls_in_strings_not_treated_as_comments():
    obj, _ = repaired('{"url": "https://example.com/a//b", "tag": "C# rocks"}')
    assert obj["url"] == "https://example.com/a//b"
    assert obj["tag"] == "C# rocks"


# ------------------------------------------------------------------ literals
def test_nan_infinity_undefined_become_null():
    obj, report = repaired('{"a": NaN, "b": Infinity, "c": -Infinity, "d": undefined}')
    assert obj == {"a": None, "b": None, "c": None, "d": None}
    assert "literals" in report.applied


def test_word_nan_inside_string_untouched():
    obj, _ = repaired('{"a": "NaN is not a number"}')
    assert obj == {"a": "NaN is not a number"}


# ----------------------------------------------------------- trailing commas
def test_trailing_commas_removed():
    obj, report = repaired('{"a": 1, "b": [1, 2,],}')
    assert obj == {"a": 1, "b": [1, 2]}
    assert "trailing-commas" in report.applied


# ------------------------------------------------------------- unquoted keys
def test_unquoted_keys_quoted():
    obj, report = repaired('{name: "x", count: 3}')
    assert obj == {"name": "x", "count": 3}
    assert "unquoted-keys" in report.applied


def test_dashed_unquoted_key():
    obj, _ = repaired('{content-type: "json"}')
    assert obj == {"content-type": "json"}


def test_word_with_colon_in_value_position_not_treated_as_key():
    # "http" follows a ":" (value position), so it must NOT be quoted as a key.
    obj, report = repaired('{"u": "http://x", b: 2}')
    assert obj == {"u": "http://x", "b": 2}
    assert "unquoted-keys" in report.applied


def test_bare_url_value_is_out_of_scope():
    # A completely unquoted URL value cannot be salvaged reliably.
    with pytest.raises(ValueError):
        repair_json('{"url": https://example.com}')


# ------------------------------------------------------------ single quotes
def test_single_quotes_converted():
    obj, report = repaired("{'a': 'hello'}")
    assert obj == {"a": "hello"}
    assert "single-quotes" in report.applied


def test_single_quotes_with_embedded_double_quotes():
    obj, _ = repaired("{'msg': 'she said \"hi\"'}")
    assert obj == {"msg": 'she said "hi"'}


def test_single_quotes_with_escaped_apostrophe():
    obj, _ = repaired("{'msg': 'it\\'s fine'}")
    assert obj == {"msg": "it's fine"}


def test_apostrophe_in_double_quoted_string_untouched():
    obj, _ = repaired('{"msg": "it\'s fine"}')
    assert obj == {"msg": "it's fine"}


# --------------------------------------------------------------- truncation
def test_missing_closing_braces():
    obj, report = repaired('{"a": {"b": [1, 2')
    assert obj == {"a": {"b": [1, 2]}}
    assert "truncated-braces" in report.applied


def test_unterminated_string_closed():
    obj, report = repaired('{"a": "hello')
    assert obj == {"a": "hello"}
    assert "truncated-string" in report.applied


def test_truncated_literal_completed():
    obj, report = repaired('{"ok": tru')
    assert obj == {"ok": True}
    assert "truncated-literal" in report.applied


# ---------------------------------------------------------- trailing garbage
def test_trailing_prose_trimmed():
    obj, report = repaired('{"a": 1}\nHope this helps!')
    assert obj == {"a": 1}
    assert "trailing-garbage" in report.applied


def test_leading_prose_not_silently_accepted():
    with pytest.raises(ValueError):
        repair_json('Here is your JSON: {"a": 1}')


# ------------------------------------------------------------------ combined
def test_kitchen_sink_llm_output():
    src = (
        "```json\n"
        "{\n"
        "  // model config\n"
        "  name: 'demo',\n"
        "  temp: 0.7,\n"
        "  tags: ['a', 'b',],\n"
        "  ratio: NaN, // unknown\n"
        "}\n"
        "```\n"
        "Let me know if you need more."
    )
    obj, report = repaired(src)
    assert obj == {"name": "demo", "temp": 0.7, "tags": ["a", "b"], "ratio": None}
    for fix in ("fences", "comments", "single-quotes", "trailing-commas",
                "unquoted-keys", "literals", "trailing-garbage"):
        assert fix in report.applied


def test_repair_is_idempotent():
    src = "```json\n{a: 'x',}\n``` trailing"
    once, _ = repair_json(src)
    twice, report = repair_json(once)
    assert twice == once
    assert report.applied == []


def test_garbage_raises_value_error():
    with pytest.raises(ValueError):
        repair_json("this is not json at all {{{")


def test_empty_input_raises():
    with pytest.raises(ValueError):
        repair_json("")


# ---------------------------------------------------------------------- CLI
def run_cli(*args, input_text=None):
    return subprocess.run(
        [sys.executable, "-m", "jsonrepair", *args],
        input=input_text,
        capture_output=True,
        text=True,
        cwd="/home/hatch/workspace/github-builds/llm-json-repair",
    )


def test_cli_repairs_stdin_to_stdout():
    p = run_cli(input_text="{a: 1,}")
    assert p.returncode == 0
    assert json.loads(p.stdout) == {"a": 1}
    assert "repaired" in p.stderr


def test_cli_check_mode():
    assert run_cli("--check", input_text='{"a": 1}').returncode == 0
    assert run_cli("--check", input_text="{a: 1,}").returncode == 0
    assert run_cli("--check", input_text="nope").returncode == 1


def test_cli_compact_flag():
    p = run_cli("--compact", input_text='{"a": 1}')
    assert p.returncode == 0
    assert p.stdout.strip() == '{"a": 1}'


def test_cli_quiet_suppresses_stderr():
    p = run_cli("--quiet", input_text="{a: 1,}")
    assert p.returncode == 0
    assert p.stderr == ""
