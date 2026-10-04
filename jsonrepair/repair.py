"""Core repair engine for llm-json-repair.

Deterministic, zero-dependency, string-aware repair pipeline. Every structural
fix is applied while skipping over string contents, so URLs, code samples and
prose inside strings are never mangled.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class RepairReport:
    """What the repair pipeline did to its input."""

    applied: list = field(default_factory=list)  # fix names, in application order
    success: bool = False
    error: str | None = None

    def note(self, name: str) -> None:
        if name not in self.applied:
            self.applied.append(name)


# ---------------------------------------------------------------------------
# Pass 1: fences / whitespace / BOM
# ---------------------------------------------------------------------------

_OPEN_FENCE_RE = re.compile(r"^\s*```(?:json|JSON|jsonc)?[ \t]*\r?\n?")
_CLOSE_FENCE_RE = re.compile(r"\r?\n?[ \t]*```")


def strip_fences(text: str, report: RepairReport) -> str:
    cleaned = text.lstrip("\ufeff").strip()
    m = _OPEN_FENCE_RE.match(cleaned)
    if not m:
        if cleaned != text:
            report.note("whitespace")
        return cleaned
    report.note("fences")
    rest = cleaned[m.end():]
    c = _CLOSE_FENCE_RE.search(rest)
    if c:
        # Keep anything after the closing fence; the trailing-garbage
        # fallback deals with it later.
        inner, after = rest[: c.start()], rest[c.end():]
        combined = inner + ("\n" + after if after.strip() else "")
        return combined.strip()
    # Unclosed fence (truncated output): drop the opening line and let the
    # truncation pass close the structure.
    return rest.strip()


# ---------------------------------------------------------------------------
# Pass 2: string-aware structural normalisation
# ---------------------------------------------------------------------------
# Handles, in one scan that never touches string contents:
#   - // line comments, # line comments, /* block */ comments   (comments)
#   - NaN / Infinity / -Infinity / undefined -> null            (literals)
#   - trailing commas before } or ]                             (trailing-commas)
#   - unquoted object keys                                       (unquoted-keys)
#   - single-quoted strings -> double-quoted                    (single-quotes)


def _skip_ignored(text: str, k: int) -> int:
    """Skip whitespace and comments starting at index k; return new index."""
    n = len(text)
    while k < n:
        ch = text[k]
        if ch in " \t\r\n":
            k += 1
        elif ch == "/" and k + 1 < n and text[k + 1] == "/":
            j = text.find("\n", k + 2)
            k = n if j == -1 else j + 1
        elif ch == "/" and k + 1 < n and text[k + 1] == "*":
            j = text.find("*/", k + 2)
            k = n if j == -1 else j + 2
        elif ch == "#" and (k == 0 or text[k - 1] in " \t\r\n"):
            j = text.find("\n", k + 1)
            k = n if j == -1 else j + 1
        else:
            break
    return k


def _scan_normalize(text: str, report: RepairReport) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    prev_sig = ""  # last significant (non-space) char emitted, for key detection

    def emit(s: str) -> None:
        out.append(s)

    while i < n:
        ch = text[i]

        # ---- line comments ----
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i + 2)
            i = n if j == -1 else j
            report.note("comments")
            continue
        # ---- block comments ----
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            report.note("comments")
            continue
        # ---- # comments (full-line, or trailing after whitespace) ----
        if ch == "#" and (i == 0 or text[i - 1] in " \t\r\n"):
            j = text.find("\n", i + 1)
            i = n if j == -1 else j
            report.note("comments")
            continue

        # ---- double-quoted string: copy verbatim ----
        if ch == '"':
            j = i + 1
            buf = ['"']
            closed = False
            while j < n:
                c = text[j]
                buf.append(c)
                if c == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if c == '"':
                    closed = True
                    j += 1
                    break
                j += 1
            emit("".join(buf))
            prev_sig = '"'
            if not closed:
                # Unterminated string at EOF: leave the closer to the
                # truncation pass, but mark where we are.
                i = j
                continue
            i = j
            continue

        # ---- single-quoted string: convert to double-quoted ----
        if ch == "'":
            j = i + 1
            inner: list[str] = []
            closed = False
            while j < n:
                c = text[j]
                if c == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    inner.append("'" if nxt == "'" else "\\" + nxt)
                    j += 2
                    continue
                if c == "'":
                    closed = True
                    j += 1
                    break
                inner.append(c)
                j += 1
            content = "".join(inner).replace("\\", "\\\\").replace('"', '\\"')
            # A literal newline inside a JSON string is invalid; escape it.
            content = content.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
            emit('"' + content + ('"' if closed else ""))
            prev_sig = '"'
            report.note("single-quotes")
            i = j
            continue

        # ---- trailing comma: skip if } or ] follows (past comments) ----
        if ch == ",":
            k = _skip_ignored(text, i + 1)
            if k < n and text[k] in "}]":
                report.note("trailing-commas")
                i += 1
                continue
            emit(",")
            prev_sig = ","
            i += 1
            continue

        # ---- bare words: literals + unquoted keys ----
        if ch.isalpha() or ch == "_" or ch == "-":
            m = re.match(r"[A-Za-z_][A-Za-z0-9_.-]*|-?Infinity|NaN|undefined", text[i:])
            word = m.group(0) if m else ch
            low = word.lower()
            if word in ("NaN", "Infinity", "-Infinity") or low == "undefined":
                emit("null")
                report.note("literals")
                i += len(word)
                prev_sig = "l"
                continue
            if word in ("true", "false", "null"):
                emit(word)
                i += len(word)
                prev_sig = "l"
                continue
            # Unquoted key? Only when it directly follows { or , and is
            # followed by a colon (comments allowed in between).
            if prev_sig in "{,":
                k = _skip_ignored(text, i + len(word))
                if k < n and text[k] == ":":
                    emit('"' + word + '"')
                    report.note("unquoted-keys")
                    i += len(word)
                    prev_sig = "k"
                    continue
            emit(word)
            i += len(word)
            prev_sig = "w"
            continue

        emit(ch)
        if ch not in " \t\r\n":
            prev_sig = ch
        i += 1

    return "".join(out)


# ---------------------------------------------------------------------------
# Pass 3: truncation closing
# ---------------------------------------------------------------------------

_TRUNC_LITERAL = {"tru": "true", "fals": "false", "nul": "null"}


def close_truncated(text: str, report: RepairReport) -> str:
    """Balance braces/brackets and close an unterminated string.

    Only used when json.loads still fails after normalisation.
    """
    stack: list[str] = []
    in_str = False
    escape = False
    for ch in text:
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
    out = text
    stripped = out.rstrip()
    # Complete a cut-off literal: {"a": tru  ->  {"a": true
    for frag, full in _TRUNC_LITERAL.items():
        if re.search(r"(?<![A-Za-z0-9_])" + frag + r"$", stripped):
            out = stripped[: -len(frag)] + full
            report.note("truncated-literal")
            break
    else:
        out = stripped
    if in_str:
        out += '"'
        report.note("truncated-string")
    while stack:
        out += stack.pop()
        report.note("truncated-braces")
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_FIX_ORDER = [
    "fences",
    "whitespace",
    "comments",
    "literals",
    "trailing-commas",
    "unquoted-keys",
    "single-quotes",
]


def repair_json(text: str) -> tuple[str, RepairReport]:
    """Return (repaired_text, report). Raises ValueError if unrepairable."""
    report = RepairReport()
    candidate = strip_fences(text, report)
    candidate = _scan_normalize(candidate, report)

    try:
        json.loads(candidate)
        report.success = True
        return candidate, report
    except json.JSONDecodeError:
        pass

    # Fallback 1: close truncation, then retry.
    last_err = ""
    closed = close_truncated(candidate, report)
    if closed != candidate:
        try:
            json.loads(closed)
            report.success = True
            return closed, report
        except json.JSONDecodeError as exc:
            last_err = str(exc)

    # Fallback 2: valid JSON prefix followed by trailing prose.
    try:
        _obj, end = json.JSONDecoder().raw_decode(candidate)
        rest = candidate[end:].strip()
        if rest:
            prefix = candidate[:end]
            json.loads(prefix)  # must not raise
            report.note("trailing-garbage")
            report.success = True
            return prefix, report
    except json.JSONDecodeError:
        pass

    report.error = last_err or "could not repair input as JSON"
    raise ValueError(f"llm-json-repair: {report.error}")


def loads_repair(text: str) -> tuple[object, RepairReport]:
    """Parse possibly-broken JSON, returning (object, report)."""
    repaired, report = repair_json(text)
    return json.loads(repaired), report
