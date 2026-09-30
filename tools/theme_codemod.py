"""
Replace hard-coded palette colours in Python source with Gate tokens.

Almost every colour typed into Slate's screens is one of the Slate theme's own
values (#16161A is Gate.PANEL, #3EA8BF is Gate.ACCENT ...). Typed as text they
are dark in every theme, which is why the Light theme could not reach them.
This rewrites them to the token they came from, so a screen follows the theme
and looks exactly as it did before in the default one:

    "background: #16161A; color: white;"   ->  f"background: {Gate.PANEL}; color: {Gate.TEXT};"
    QColor("#3EA8BF")                       ->  QColor(Gate.ACCENT)
    "rgba(255,255,255,0.05)"                ->  f"{Gate.overlay(0.05)}"
    "rgba(62, 168, 191, 0.18)"              ->  f"{Gate.tint(Gate.ACCENT, 0.18)}"

Plain strings become f-strings with their braces doubled, so a stylesheet (or
a string later passed to .format) evaluates to exactly the same text. Docstrings
are left alone, and so is anything inside an f-string's {expression}.

    python tools/theme_codemod.py slate/gui/tabs/my_tab.py [more files]   # rewrite
    python tools/theme_codemod.py --check slate/gui                       # report only

What it cannot decide is reported, not guessed: "white" or "black" used as a
background, a colour that is not in the palette, a colour inside an f-string
expression. Those need a person (and usually a Gate token by meaning).
"""

import argparse
import ast
import io
import os
import re
import sys
import tokenize

# Slate-theme value -> Gate attribute.
HEX = {
    "#0D0D0F": "GROUND", "#16161A": "PANEL", "#1D1D22": "RAISED", "#26262D": "RAISED_HI",
    "#2C2C34": "LINE", "#212128": "LINE_SOFT", "#121214": "INPUT", "#121212": "INPUT",
    "#E8E6E1": "TEXT", "#B4B1AA": "TEXT_2", "#87857F": "TEXT_DIM", "#07171B": "TEXT_ON_ACCENT",
    "#3EA8BF": "ACCENT", "#5FC6DA": "ACCENT_HI", "#2A7A8C": "ACCENT_DIM", "#16323A": "ACCENT_SURFACE",
    "#5FBF8F": "OK", "#D9A441": "WARN", "#D9635F": "BAD", "#6BA4C9": "INFO", "#6E6C67": "IDLE",
    "#1B3A2C": "OK_SURFACE", "#3A2F19": "WARN_SURFACE", "#3A1F1E": "BAD_SURFACE",
    "#E4817E": "BAD_HI", "#74CEA0": "OK_HI", "#EFC0BE": "BAD_TEXT_SOFT",
}

# rgb triple -> expression building the translucent colour for alpha {a}.
RGBA = {
    (255, 255, 255): "Gate.overlay({a})",
    (62, 168, 191): "Gate.tint(Gate.ACCENT, {a})",
    (217, 164, 65): "Gate.tint(Gate.WARN, {a})",
    (95, 191, 143): "Gate.tint(Gate.OK, {a})",
    (217, 99, 95): "Gate.tint(Gate.BAD, {a})",
    (107, 164, 201): "Gate.tint(Gate.INFO, {a})",
    (22, 22, 26): "Gate.tint(Gate.PANEL, {a})",
    (13, 13, 15): "Gate.tint(Gate.GROUND, {a})",
    (29, 29, 34): "Gate.tint(Gate.RAISED, {a})",
}

HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")
RGBA_RE = re.compile(r"rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([0-9.]+)\s*\)")
# "color: white" / "color:#fff" as a text colour (not background-color).
TEXT_WHITE_RE = re.compile(r"(?<![-\w])(color\s*:\s*)(white|#fff\b|#ffffff\b)", re.I)
TEXT_BLACK_RE = re.compile(r"(?<![-\w])(color\s*:\s*)(black|#000\b|#000000\b)", re.I)

IMPORT_LINE = "from slate.core.infra.gate import Gate\n"


_FILLS = {
    # a solid fill -> the text colour that reads on it in every theme
    "ACCENT": "Gate.TEXT_ON_ACCENT", "ACCENT_HI": "Gate.TEXT_ON_ACCENT", "ACCENT_DIM": "Gate.TEXT_ON_ACCENT",
    "BAD": "Gate.TEXT_ON_BAD", "OK": "Gate.TEXT_ON_BAD", "WARN": "Gate.TEXT_ON_BAD",
    "INFO": "Gate.TEXT_ON_BAD", "BAD_HI": "Gate.TEXT_ON_BAD", "OK_HI": "Gate.TEXT_ON_BAD",
}
_BACKGROUND_RE = re.compile(r"background(?:-color)?\s*:\s*(#[0-9A-Fa-f]{6})\b", re.I)


def _text_on(text, position, default):
    """
    "color: white" means "the text colour" on a panel, but "white on the
    accent" on an accent button - which is dark text on the Light theme's
    accent. Look at the background set in the same rule block to tell.
    """
    start = text.rfind("{", 0, position)
    end = text.find("}", position)
    block = text[start + 1 if start >= 0 else 0: end if end >= 0 else len(text)]
    for match in _BACKGROUND_RE.finditer(block):
        name = HEX.get(match.group(1).upper())
        if name in _FILLS:
            return _FILLS[name]
    return default


def _replacement_expressions(text, report, where):
    """
    Find what in `text` can become a Gate expression.
    Returns a list of (start, end, expression).
    """
    found = []

    for match in TEXT_WHITE_RE.finditer(text):
        start = match.start(2)
        found.append((start, match.end(2), _text_on(text, start, "Gate.TEXT")))
    for match in TEXT_BLACK_RE.finditer(text):
        found.append((match.start(2), match.end(2), _text_on(text, match.start(2), "Gate.TEXT_ON_ACCENT")))
    for match in RGBA_RE.finditer(text):
        rgb = tuple(int(match.group(i)) for i in (1, 2, 3))
        template = RGBA.get(rgb)
        if template is None:
            if rgb != (0, 0, 0):
                report.append(f"{where}: rgba{rgb} is not a palette colour - left as is")
            continue
        found.append((match.start(), match.end(), template.format(a=match.group(4))))
    for match in HEX_RE.finditer(text):
        name = HEX.get(match.group(0).upper())
        if name is None:
            report.append(f"{where}: {match.group(0)} is not a palette colour - left as is")
            continue
        found.append((match.start(), match.end(), f"Gate.{name}"))

    # Overlaps (e.g. "color: #ffffff" found by two rules): keep the first.
    found.sort()
    result, last_end = [], -1
    for start, end, expression in found:
        if start >= last_end:
            result.append((start, end, expression))
            last_end = end
    return result


def _split_prefix(token_text):
    match = re.match(r"^([A-Za-z]*)(['\"]{3}|['\"])", token_text)
    prefix, quote = match.group(1), match.group(2)
    body = token_text[len(prefix) + len(quote):-len(quote)]
    return prefix, quote, body


def _depth_map(body):
    """For an f-string body: True at each index that is literal text (outside {...})."""
    literal = [True] * len(body)
    depth, i = 0, 0
    while i < len(body):
        ch = body[i]
        if depth == 0 and body.startswith("{{", i):
            i += 2
            continue
        if depth == 0 and body.startswith("}}", i):
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            literal[i] = False
            i += 1
            continue
        if depth > 0:
            literal[i] = False
        i += 1
    return literal


def _rewrite_string(token_text, report, where, standalone):
    prefix, quote, body = _split_prefix(token_text)
    lower = prefix.lower()
    if "b" in lower:
        return None
    is_f = "f" in lower

    edits = _replacement_expressions(body, report, where)
    if not edits:
        return None

    if is_f:
        literal = _depth_map(body)
        kept = []
        for start, end, expression in edits:
            if all(literal[start:end]):
                kept.append((start, end, expression))
            else:
                report.append(f"{where}: colour inside an f-string expression - left as is")
        edits = kept
        if not edits:
            return None

    # The whole string is one colour: use the expression itself.
    if (standalone and not is_f and len(edits) == 1 and edits[0][0] == 0
            and edits[0][1] == len(body) and "r" not in lower):
        return edits[0][2]

    out, cursor = [], 0
    for start, end, expression in edits:
        chunk = body[cursor:start]
        if not is_f:
            chunk = chunk.replace("{", "{{").replace("}", "}}")
        out.append(chunk)
        out.append("{" + expression + "}")
        cursor = end
    tail = body[cursor:]
    if not is_f:
        tail = tail.replace("{", "{{").replace("}", "}}")
    out.append(tail)

    new_prefix = prefix.replace("u", "").replace("U", "")
    if not is_f:
        new_prefix = "f" + new_prefix
    return new_prefix + quote + "".join(out) + quote


def _docstring_starts(tree):
    starts = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                starts.add((body[0].value.lineno, body[0].value.col_offset))
    return starts


def _has_gate_import(source):
    return re.search(r"^\s*from\s+[\w.]*gate\s+import\s+[^\n]*\bGate\b", source, re.M) is not None


def _add_import(source, tree):
    lines = source.splitlines(keepends=True)
    insert_at = 0
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) \
                and isinstance(node.value.value, str) and insert_at == 0:
            insert_at = node.end_lineno
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            insert_at = node.end_lineno
            continue
        break
    # Put it after the first block of top-level imports, if there is one.
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node.lineno > insert_at:
            last = node.end_lineno
            index = tree.body.index(node)
            for follower in tree.body[index + 1:]:
                if isinstance(follower, (ast.Import, ast.ImportFrom)):
                    last = follower.end_lineno
                else:
                    break
            insert_at = last
            break
    lines.insert(insert_at, IMPORT_LINE)
    return "".join(lines)


def rewrite_source(source, where="<source>"):
    """Return (new_source, changes, report)."""
    report = []
    tree = ast.parse(source)
    docstrings = _docstring_starts(tree)

    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    significant = [t for t in tokens if t.type not in (
        tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)]
    neighbours = {}
    for i, tok in enumerate(significant):
        before = significant[i - 1] if i else None
        after = significant[i + 1] if i + 1 < len(significant) else None
        neighbours[tok.start] = (before, after)

    line_starts = [0]
    for line in source.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))

    def offset(position):
        return line_starts[position[0] - 1] + position[1]

    edits = []
    for tok in tokens:
        if tok.type != tokenize.STRING:
            continue
        if (tok.start[0], tok.start[1]) in docstrings:
            continue
        before, after = neighbours.get(tok.start, (None, None))
        standalone = not ((before is not None and before.type == tokenize.STRING)
                          or (after is not None and after.type == tokenize.STRING))
        new = _rewrite_string(tok.string, report, f"{where}:{tok.start[0]}", standalone)
        if new is not None and new != tok.string:
            edits.append((offset(tok.start), offset(tok.end), new))

    if not edits:
        return source, 0, report

    out, cursor = [], 0
    for start, end, text in edits:
        out.append(source[cursor:start])
        out.append(text)
        cursor = end
    out.append(source[cursor:])
    new_source = "".join(out)

    if not _has_gate_import(new_source):
        new_source = _add_import(new_source, ast.parse(new_source))
    ast.parse(new_source)       # never write a file that does not parse
    return new_source, len(edits), report


def _files(paths):
    for path in paths:
        if os.path.isdir(path):
            for root, _dirs, names in os.walk(path):
                for name in sorted(names):
                    if name.endswith(".py"):
                        yield os.path.join(root, name)
        elif path.endswith(".py"):
            yield path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--check", action="store_true", help="report only, change nothing")
    args = parser.parse_args(argv)

    total = 0
    for path in _files(args.paths):
        with open(path, "r", encoding="utf-8", newline="") as handle:
            source = handle.read()
        try:
            new_source, changes, report = rewrite_source(source, path)
        except SyntaxError as exc:
            print(f"{path}: skipped, does not parse ({exc})")
            continue
        for line in report:
            print("  note", line)
        if changes:
            total += changes
            print(f"{path}: {changes} string(s) {'would change' if args.check else 'rewritten'}")
            if not args.check:
                with open(path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(new_source)
    print(f"{total} string(s) {'to change' if args.check else 'changed'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
