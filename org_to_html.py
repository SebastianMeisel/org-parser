#!/usr/bin/env python3
"""
org_to_html.py

Very small Org → HTML renderer built on the existing pipeline:

- config_loader.load_config() for regex + settings
- org_reader.read_with_includes() for depth-first include expansion
  (and "no include expansion inside blocks")
- org_parser.parse_org_line() for headings + block begin/end + src metadata

Scope (intentionally minimal):
- Headings -> <h1>.. <h6>
- Normal text -> <p>...</p> (blank lines separate paragraphs)
- Any #+begin_... / #+end_... blocks -> <pre>...</pre>
- src blocks get language + options as data attributes on <pre>

Everything else is kept simple and only uses information established so far.
"""
from __future__ import annotations
from config_loader import load_config, OrgReaderConfig
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
import argparse
import hashlib
import html
import ast
import math
import re

from org_parser import OrgEvent, OrgState, parse_org_line, tokenize_inline_org_markup
from org_reader import read_with_includes

MATH_CACHE = Path(".math-cache")  # or BASE_DIR / ".math-cache"
MATH_CACHE.mkdir(exist_ok=True)


def escape_html(text: str) -> str:
    """Escape text for HTML output."""
    return html.escape(text, quote=True)


def heading_tag_for_level(level: int) -> str:
    """Map Org heading level to an HTML heading tag."""
    safe_level = max(1, min(6, level))
    return f"h{safe_level}"


def is_org_directive_line(line: str) -> bool:
    """
    Decide whether a line is an Org keyword/directive line like '#+TITLE:'.

    We keep headings and block markers via events, but otherwise ignore directives
    in the HTML body to avoid noise.
    """
    stripped = line.lstrip()
    return stripped.startswith("#+")

def normalize_image_src(url: str) -> str:
    """
    Turn an Org image URL into a web URL.

    - 'file:img/foo.png'  -> '/assets/img/foo.png'
    - '/static/foo.png'   -> '/static/foo.png'  (left as-is)
    - 'https://…'         -> 'https://…'       (left as-is)
    """
    # Handle typical Org-style file: links (relative paths)
    if url.startswith("file:"):
        url = url[5:]  # strip "file:"
        # strip leading slashes so "file:./img/…" and "file:/img/…" both work
        while url.startswith("/"):
            url = url[1:]

    parsed = urlparse(url)
    # Preserve absolute URLs (http, https, etc.) and already-absolute paths
    if parsed.scheme or url.startswith("/"):
        return url

    # Everything else: treat as project-relative and expose under /assets/
    return f"/assets/{url}"

def render_hidden_comment_block(
    *,
    comment_id: str,
    anchor: Optional[str],
    lines: list[str],
    preamble_macros: str,
    button_label: str = "Show comment",  # <-- NEW
) -> str:
    wrapper_attrs = ' class="comment-wrapper"'
    if anchor:
        wrapper_attrs = f' class="comment-wrapper" id="{escape_html(anchor)}"'

    inner = render_comment_html_lines(lines, preamble_macros=preamble_macros)

    return (
        f"<div{wrapper_attrs}>"
        f'<button type="button" class="comment-toggle" data-target="{escape_html(comment_id)}">'
        f"{escape_html(button_label)}"
        "</button>"
        f'<div class="comment-box" id="{escape_html(comment_id)}" hidden>'
        f"{inner}"
        "</div>"
        "</div>\n"
    )



def build_verse_attributes(event: OrgEvent | None) -> str:
    """
    Build HTML attributes for a verse container.

    - Adds class="verse block block-verse"
    - Adds id="..." if the verse has an anchor (#+NAME:)
    """
    classes = ["verse", "block", "block-verse"]
    attrs: list[str] = [f'class="{" ".join(classes)}"']

    if event is not None:
        anchor = event.data.get("anchor")
        if isinstance(anchor, str) and anchor.strip():
            attrs.append(f'id="{escape_html(anchor.strip())}"')

    return " " + " ".join(attrs)


def render_verse_lines(lines: list[str], preamble_macros: str) -> str:
    """
    Tokenize + render verse lines while preserving line breaks.

    Each input line becomes one rendered line. Line breaks are emitted as <br />.
    """
    rendered_lines: list[str] = []
    for ln in lines:
        tokens = tokenize_inline_org_markup(ln or "")
        rendered_lines.append(render_inline_tokens(tokens, preamble_macros=preamble_macros))

    # Preserve line boundaries explicitly
    return "<br />\n".join(rendered_lines)

def render_comment_html_lines(
    lines: list[str],
    *,
    preamble_macros: str = "",
) -> str:
    """
    Render the content of a hidden comment/noexport block.

    We keep it simple: treat blank lines as paragraph breaks, otherwise
    render like normal inline Org (bold/italic/links/math).
    """
    out: list[str] = []
    paragraph: list[list[tuple[str, str]]] = []

    def flush_para() -> None:
        nonlocal paragraph
        if not paragraph:
            return
        rendered = [
            render_inline_tokens(toks, preamble_macros=preamble_macros)
            for toks in paragraph
        ]
        paragraph = []
        text = " ".join(s for s in rendered if s.strip())
        if text.strip():
            out.append(f"<p>{text}</p>")

    for raw in lines:
        line = (raw or "").rstrip("\n")
        if line.strip() == "":
            flush_para()
            continue

        # ignore org directives inside hidden blocks (optional, but usually nice)
        if is_org_directive_line(line):
            continue

        paragraph.append(tokenize_inline_org_markup(line))

    flush_para()
    return "\n".join(out) + ("\n" if out else "")


def math_image_url(math_src: str, *, preamble_macros: str = "") -> str:
    """
    Turn (macros + math) into a cache key and URL.

    We hash BOTH the macro preamble and the math snippet, so changes in macros
    produce a different SVG.
    """
    macros = (preamble_macros or "").strip()
    payload_for_hash = macros + "\n%%MATH%%\n" + (math_src or "")
    digest = hashlib.sha1(payload_for_hash.encode("utf-8")).hexdigest()

    source_path = MATH_CACHE / f"{digest}.tex"
    if not source_path.exists():
        # Store a tiny container format:
        # - supports cached macro preamble
        # - backward compatible fallback is handled in webapp.py
        content = (
            "%% org-math-cache-v1\n"
            "%% macros\n"
            f"{macros}\n"
            "%% math\n"
            f"{math_src}\n"
        )
        source_path.write_text(content, encoding="utf-8")

    return f"/math/{digest}.svg"

_TBLFM_LHS_RE = re.compile(r"^\s*(\$\>|\$\d+)\s*$")
_COLREF_RE = re.compile(r"\$(\d+)")
_PREVROW_COL_RE = re.compile(r"@-1\$(\d+)")
_PREVROW_SAMECOL_RE = re.compile(r"(?<![\w$])@-1(?![\w$])")

def _coerce_number(value: str) -> float:
    s = (value or "").strip()
    if s == "":
        return 0.0
    # very permissive numeric parsing
    try:
        return float(s)
    except ValueError:
        # allow "1,23" german decimal (basic)
        try:
            return float(s.replace(",", "."))
        except ValueError:
            return 0.0

def _safe_eval(expr: str, names: dict[str, float]) -> float:
    """
    Safe-ish eval for basic arithmetic expressions.

    Allowed:
      - numbers, + - * / **, parentheses
      - names in `names`
      - calls: abs, round, min, max, int, float, sqrt
    """
    allowed_funcs = {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "int": int,
        "float": float,
        "sqrt": math.sqrt,
    }

    node = ast.parse(expr, mode="eval")

    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Constant,
        ast.Name,
        ast.Load,
        ast.Call,
    )

    for sub in ast.walk(node):
        if not isinstance(sub, allowed_nodes):
            raise ValueError(f"Disallowed expression node: {type(sub).__name__}")

        if isinstance(sub, ast.Call):
            if not isinstance(sub.func, ast.Name):
                raise ValueError("Only simple function calls allowed")
            if sub.func.id not in allowed_funcs:
                raise ValueError(f"Function not allowed: {sub.func.id}")

    compiled = compile(node, "<tblfm>", "eval")
    env = dict(allowed_funcs)
    env.update(names)
    return float(eval(compiled, {"__builtins__": {}}, env))

def _format_result(x: float) -> str:
    if abs(x - round(x)) < 1e-12:
        return str(int(round(x)))
    # keep it readable
    return str(x)

def _parse_tblfm_assignments(formulas: list[str], max_cols: int) -> list[tuple[int, str]]:
    """
    Return list of (dest_col_1based, rhs_expr) for column formulas.

    Supports LHS:
      - $N
      - $>  (last column)
    Ignores any non-column formulas.
    Also strips trailing calc options after ';' (basic).
    """
    out: list[tuple[int, str]] = []

    for raw in formulas:
        # strip trailing options like ;T ;N etc (basic)
        core = raw.split(";", 1)[0].strip()
        if "=" not in core:
            continue

        lhs, rhs = core.split("=", 1)
        lhs = lhs.strip()
        rhs = rhs.strip()
        if not rhs:
            continue

        m = _TBLFM_LHS_RE.match(lhs)
        if not m:
            continue

        if lhs == "$>":
            dest = max_cols
        else:
            try:
                dest = int(lhs[1:])
            except ValueError:
                continue

        if dest <= 0:
            continue

        out.append((dest, rhs))
    return out

def _apply_column_formulas_to_table(
    table_rows: list[dict],
    tblfm_formulas: list[str],
) -> None:
    """
    Mutate table_rows in-place (row cells updated).

    table_rows items:
      {"type": "row", "cells": [...]}
      {"type": "hline"}
    """
    # determine max cols
    max_cols = 0
    for r in table_rows:
        if r.get("type") == "row":
            max_cols = max(max_cols, len(r.get("cells", [])))
    if max_cols == 0:
        return

    # pad all rows to max_cols
    for r in table_rows:
        if r.get("type") == "row":
            cells = r.get("cells", [])
            if len(cells) < max_cols:
                r["cells"] = cells + [""] * (max_cols - len(cells))

    assignments = _parse_tblfm_assignments(tblfm_formulas, max_cols)
    if not assignments:
        return

    # header detection: everything before first hline that has rows below is header
    first_hline = None
    for i, r in enumerate(table_rows):
        if r.get("type") == "hline":
            # ensure there is at least one data row below
            if any(rr.get("type") == "row" for rr in table_rows[i+1:]):
                first_hline = i
                break

    def is_header_row(idx: int) -> bool:
        return first_hline is not None and idx < first_hline

    # track previous data row values (after computations) for @-1 support
    prev_data_row_cells: list[str] | None = None

    for idx, r in enumerate(table_rows):
        if r.get("type") != "row":
            continue
        if is_header_row(idx):
            continue

        cells: list[str] = r["cells"]

        # build base numeric context c1..cN from *current* row
        names: dict[str, float] = {}
        for col in range(1, max_cols + 1):
            names[f"c{col}"] = _coerce_number(cells[col - 1])

        # add previous-row context for @-1 / @-1$K
        if prev_data_row_cells is None:
            for col in range(1, max_cols + 1):
                names[f"p{col}"] = 0.0
        else:
            for col in range(1, max_cols + 1):
                names[f"p{col}"] = _coerce_number(prev_data_row_cells[col - 1])

        # apply assignments in order
        for dest_col, rhs in assignments:
            # translate a minimal subset of Org/Calc-ish syntax into our eval names:
            # - $N -> cN
            # - @-1$K -> pK
            # - bare @-1 -> p<dest_col>
            expr = rhs.strip()

            # power: Org/Calc commonly uses '^' for exponent
            expr = expr.replace("^", "**")

            expr = _PREVROW_COL_RE.sub(lambda m: f"p{m.group(1)}", expr)
            expr = _PREVROW_SAMECOL_RE.sub(f"p{dest_col}", expr)
            expr = _COLREF_RE.sub(lambda m: f"c{m.group(1)}", expr)

            try:
                result = _safe_eval(expr, names)
            except Exception:
                # fail soft: do not change the cell
                continue

            # write result into destination col (1-based)
            if 1 <= dest_col <= max_cols:
                cells[dest_col - 1] = _format_result(result)

            # update names for potential later formulas in same row
            names[f"c{dest_col}"] = _coerce_number(cells[dest_col - 1])

        prev_data_row_cells = list(cells)

def render_inline_tokens(tokens: list[tuple[str, str]],
    *,
    preamble_macros: str = "",
    ) -> str:
    """
    Render inline token spans to HTML.

    token types:
      - plaintext    -> escaped text
      - bold_text    -> <strong>
      - italic_text  -> <em>
      - code         -> <code>
      - math_inline  -> <img class="math-inline" src="/math/<hash>.svg"> (cached)
      - link         -> <a href="...">...</a> or <img ... /> for image links
    """
    NULL_SEP = "\u0000"

    out: list[str] = []
    for token_type, token_text in tokens:
        if token_type == "plaintext":
            out.append(escape_html(token_text))

        elif token_type == "bold_text":
            out.append(f"<strong>{escape_html(token_text)}</strong>")

        elif token_type == "italic_text":
            out.append(f"<em>{escape_html(token_text)}</em>")

        elif token_type == "code":
            out.append(f"<code>{escape_html(token_text)}</code>")

        elif token_type == "math_inline":
            src = math_image_url(token_text, preamble_macros=preamble_macros)
            alt = token_text.strip() or "math"
            out.append(
                f'<img src="{escape_html(src)}" '
                f'alt="{escape_html(alt)}" '
                f'class="math-inline" />'
            )

        elif token_type == "link":
            # token_text is "url<NULL>desc" as produced by tokenize_inline_org_markup
            url = token_text
            label = token_text
            if NULL_SEP in token_text:
                url, label = token_text.split(NULL_SEP, 1)

            url = url.strip()
            label = (label or "").strip() or url

            if is_image_target(url):
                # Render as image; label becomes alt (and title)
                alt_text = label or url
                web_url = normalize_image_src(url)
                out.append(
                    f'<img src="{escape_html(web_url)}" '
                    f'alt="{escape_html(alt_text)}" '
                    f'title="{escape_html(alt_text)}" '
                    f'class="inline-image" />'
                )
            else:
                # Normal hyperlink
                out.append(
                    f'<a href="{escape_html(url)}">{escape_html(label)}</a>'
                )

        else:
            # fallback
            out.append(escape_html(token_text))

    return "".join(out)

def open_html_document(preamble: object) -> str:
    """
    Return the HTML prolog + minimal CSS, enriched with Org preamble metadata.

    Expects an object with:
      - .title (Optional[str])
      - .author (Optional[str])
      - .date (Optional[str])
      - .options (Optional[str])
      - .headers (dict[str, str])  (optional but nice)
    """
    title_value = getattr(preamble, "title", None) or "Org Export"
    safe_title = escape_html(title_value)

    author_value = getattr(preamble, "author", None)
    date_value = getattr(preamble, "date", None)
    options_value = getattr(preamble, "options", None)

    meta_lines: list[str] = []
    if isinstance(author_value, str) and author_value.strip():
        meta_lines.append(f'  <meta name="author" content="{escape_html(author_value.strip())}" />\n')
    if isinstance(date_value, str) and date_value.strip():
        # There is no universally standard "date" meta, but it's fine to include one.
        meta_lines.append(f'  <meta name="date" content="{escape_html(date_value.strip())}" />\n')
    if isinstance(options_value, str) and options_value.strip():
        meta_lines.append(f"  <!-- org-options: {escape_html(options_value.strip())} -->\n")

    # Emit any additional preamble headers as x-org-* meta tags (simple + safe).
    headers = getattr(preamble, "headers", None)
    if isinstance(headers, dict):
        for key, value in headers.items():
            if key in {"title", "author", "date", "options"}:
                continue
            if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
                meta_lines.append(
                    f'  <meta name="x-org-{escape_html(key.strip())}" content="{escape_html(value.strip())}" />\n'
                )

    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\" />\n"
        f"  <title>{safe_title}</title>\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
        + "".join(meta_lines) +
        '  <link rel="stylesheet" href="/static/org.css" />\n'
        '  <script src="/static/viewer.js" defer></script>\n'
        "</head>\n"
        "<body>\n"
    )


def close_html_document() -> str:
    """Return the HTML epilog."""
    return "</body>\n</html>\n"


def render_tags(tags: Optional[list[str]]) -> str:
    """Render heading tags as a small suffix."""
    if not tags:
        return ""
    safe = ", ".join(escape_html(t) for t in tags)
    return f"<span class=\"tags\">[{safe}]</span>"


def build_pre_attributes(event: OrgEvent) -> str:
    """
    Build HTML attributes for <pre> from block events.

    For src blocks we add:
      class="block block-src lang-python"   (example)
      data-language="python"
      data-session="foo" etc.
      id="anchor"                           (from #+NAME:)

    For RESULT example blocks (#+RESULTS: followed by #+begin_example)
    we additionally add a 'result' class:
      class="block block-example result"
    """
    attrs: list[str] = []
    classes: list[str] = []

    # Block name -> generic block class + name-specific class
    name = event.data.get("name")
    if isinstance(name, str) and name:
        classes.append("block")
        classes.append(f"block-{escape_html(name)}")

    # Mark “result” blocks from #+RESULTS:
    if event.data.get("is_result"):
        classes.append("result")

    # SRC options (language + header args)
    src_opts = event.data.get("src")
    if isinstance(src_opts, dict):
        lang = src_opts.get("language")
        if isinstance(lang, str) and lang:
            # use language as an additional CSS class
            classes.append(f"lang-{escape_html(lang)}")

            # keep the data-language attribute for CSS ::before + tooling
            attrs.append(f'data-language="{escape_html(lang)}"')

        for key, value in src_opts.items():
            if key == "language":
                continue
            if isinstance(key, str) and isinstance(value, str):
                attrs.append(f'data-{escape_html(key)}="{escape_html(value)}"')

    # Anchor (from #+NAME:)
    anchor = event.data.get("anchor")
    if isinstance(anchor, str) and anchor:
        attrs.append(f'id="{escape_html(anchor)}"')

    # If we collected any classes, serialize them as a single class="..." attribute
    if classes:
        class_value = " ".join(classes)
        attrs.insert(0, f'class="{class_value}"')

    return (" " + " ".join(attrs)) if attrs else ""


def flush_paragraph(paragraph_buffer: list[list[tuple[str, str]]],
                    out: list[str],
                    *,
                    preamble_macros: str = "",
                    ) -> None:
    """Flush buffered tokenized lines as a <p>...</p>."""
    if not paragraph_buffer:
        return

    rendered_lines: list[str] = []
    for token_line in paragraph_buffer:
        rendered_lines.append(render_inline_tokens(token_line, preamble_macros=preamble_macros))

    paragraph_buffer.clear()

    text = " ".join(s for s in rendered_lines if s.strip() != "")
    if text.strip():
        out.append(f"<p>{text}</p>\n")

def is_image_target(url: str) -> bool:
    """
    Return True if the URL looks like an image file (by extension).
    Query string and fragment are ignored.
    """
    # strip query and fragment
    base = url.split("?", 1)[0].split("#", 1)[0]
    base = base.lower()
    return base.endswith((
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".bmp",
    ))


def render_org_to_html_body(
    input_path: Path,
    cfg: OrgReaderConfig,
) -> tuple[OrgState, str]:
    """
    Render an Org file (with includes expanded) into HTML *body content*.
    Returns (final_state, body_html).
    """
    state = OrgState()

    html_out: list[str] = []
    paragraph_buffer: list[list[tuple[str, str]]] = []
    inside_pre: bool = False
    pre_lines: list[str] = []
    pre_open_event: Optional[OrgEvent] = None

    inside_container: bool = False
    container_name: Optional[str] = None
    container_anchor: Optional[str] = None

    inside_ul: bool = False
    inside_ol: bool = False

    inside_table: bool = False

    table_buffer: list[dict] = []
    table_tblfm: list[str] = []
    table_anchor: Optional[str] = None
    table_caption: Optional[list[tuple[str, str]]] = None
    table_collecting: bool = False

    inside_verse: bool = False
    verse_lines: list[str] = []
    verse_open_event: Optional[OrgEvent] = None

    anchor_pending: Optional[str] = None
    caption_pending_tokens: Optional[list[tuple[str, str]]] = None
    html_attr_pending: Optional[dict[str, str]] = None   # <-- NEW

    comment_counter: int = 0

    latex_macros_preamble: str = ""


    # RESULTS handling
    results_mode: str = "none"  # "none" | "awaiting" | "collecting"
    results_lines: list[str] = []

    def flush_results_block() -> None:
        """
        Flush collected #+RESULTS: lines as a <pre class="block result">...</pre>.
        """
        nonlocal results_mode, results_lines
        if not results_lines:
            results_mode = "none"
            return

        # Results are independent of paragraphs/lists
        flush_paragraph(paragraph_buffer, html_out)
        close_ul_if_needed()
        close_ol_if_needed()

        content = "\n".join(results_lines)
        results_lines = []

        html_out.append('<pre class="block result"><code>')
        html_out.append(escape_html(content))
        html_out.append("</code></pre>\n")

        results_mode = "none"

    def flush_paragraph_here() -> None:
        flush_paragraph(paragraph_buffer, html_out, preamble_macros=latex_macros_preamble)

    def flush_pre_if_needed() -> None:
        nonlocal inside_pre, pre_lines, pre_open_event
        if not inside_pre:
            return
        close_ul_if_needed()
        close_ol_if_needed()
        content = "\n".join(pre_lines)
        pre_lines = []
        attrs = build_pre_attributes(pre_open_event) if pre_open_event else ""
        html_out.append(f"<pre{attrs}><code>{escape_html(content)}</code></pre>\n")
        inside_pre = False
        pre_open_event = None

    def flush_verse_if_needed() -> None:
        nonlocal inside_verse, verse_lines, verse_open_event

        if not inside_verse:
            return

        # verse blocks are standalone (like pre/table)
        flush_paragraph_here()
        close_ul_if_needed()
        close_ol_if_needed()
        flush_pre_if_needed()
        flush_table_if_needed()

        inner = render_verse_lines(verse_lines, preamble_macros=latex_macros_preamble)

        attrs = build_verse_attributes(verse_open_event)
        html_out.append(f"<div{attrs}>\n{inner}\n</div>\n")

        inside_verse = False
        verse_lines = []
        verse_open_event = None


    def flush_table_if_needed() -> None:
        nonlocal table_buffer, table_tblfm, table_anchor, table_caption, table_collecting

        if not table_collecting or not table_buffer:
            table_collecting = False
            table_buffer = []
            table_tblfm = []
            table_anchor = None
            table_caption = None
            return

        # apply formulas before rendering
        _apply_column_formulas_to_table(table_buffer, table_tblfm)

        # header detection (same rule as in manual)
        first_hline = None
        for i, r in enumerate(table_buffer):
            if r.get("type") == "hline":
                if any(rr.get("type") == "row" for rr in table_buffer[i+1:]):
                    first_hline = i
                    break

        # determine max cols for colspan
        max_cols = 0
        for r in table_buffer:
            if r.get("type") == "row":
                max_cols = max(max_cols, len(r.get("cells", [])))
        max_cols = max(max_cols, 1)

        def cell_html(s: str) -> str:
            # allow inline markup inside cells
            return render_inline_tokens(tokenize_inline_org_markup(s or ""),  preamble_macros=latex_macros_preamble)

        def render_row(cells: list[str], th: bool) -> str:
            tag = "th" if th else "td"
            inner = "".join(f"<{tag}>{cell_html(c)}</{tag}>" for c in cells)
            return f"<tr>{inner}</tr>\n"

        # optional figure wrapper for caption
        if table_caption:
            fig_id = f' id="{escape_html(table_anchor)}"' if table_anchor else ""
            html_out.append(f"<figure class=\"table\"{fig_id}>\n")
            html_out.append(f"<figcaption>{render_inline_tokens(table_caption)}</figcaption>\n")
            table_id_attr = ""
        else:
            table_id_attr = f' id="{escape_html(table_anchor)}"' if table_anchor else ""

        html_out.append(f"<table{table_id_attr}>\n")

        if first_hline is not None and first_hline > 0:
            html_out.append("<thead>\n")
            for r in table_buffer[:first_hline]:
                if r.get("type") == "row":
                    html_out.append(render_row(r.get("cells", []), th=True))
            html_out.append("</thead>\n")
            body_part = table_buffer[first_hline+1:]
        else:
            body_part = table_buffer

        html_out.append("<tbody>\n")
        for r in body_part:
            if r.get("type") == "hline":
                html_out.append(f'<tr class="hline"><td colspan="{max_cols}"></td></tr>\n')
            elif r.get("type") == "row":
                html_out.append(render_row(r.get("cells", []), th=False))
        html_out.append("</tbody>\n")
        html_out.append("</table>\n")

        if table_caption:
            html_out.append("</figure>\n")

        # reset
        table_collecting = False
        table_buffer = []
        table_tblfm = []
        table_anchor = None
        table_caption = None

    def add_paragraph_line(tokens: list[tuple[str, str]]) -> None:
        paragraph_buffer.append(tokens)

    def render_heading(line: str, ev: OrgEvent, anchor: Optional[str] = None) -> str:
        """
        Render a heading event + original line into an HTML <hN>…</hN>.
        """
        level = ev.data.get("level")
        tags = ev.data.get("tags")

        if not isinstance(level, int):
            level = 1
        tag = heading_tag_for_level(level)
    
        # Raw heading text: strip stars, then optional tag suffix
        heading_text = line.lstrip("*").strip()
        if isinstance(tags, list) and heading_text.endswith(":") and " :" in heading_text:
            heading_text = heading_text.rsplit(" :", 1)[0].rstrip()
    
        heading_tokens = tokenize_inline_org_markup(heading_text)
    
        opening = f"<{tag}"
        if anchor:
            opening += f' id="{escape_html(anchor)}"'
        opening += ">"
    
        return (
            f"{opening}{render_inline_tokens(heading_tokens,  preamble_macros=latex_macros_preamble)}"
            f"{render_tags(tags)}</{tag}>\n"
        )

    def handle_inside_pre(line: str, filtered_events: list[OrgEvent]) -> bool:
        """
        Handle a line while inside a <pre> block.

        Returns True if the main loop should 'continue' (i.e. line is fully handled).
        """
        nonlocal inside_pre
        has_end_event = any(
            ev.type in {"block_end", "src_end"} for ev in filtered_events
        )
        if has_end_event:
            flush_pre_if_needed()
            return True

        pre_lines.append(line)
        return True

    def open_ul_if_needed() -> None:
        nonlocal inside_ul
        if not inside_ul:
            html_out.append("<ul>\n")
            inside_ul = True

    def close_ul_if_needed() -> None:
        nonlocal inside_ul
        if inside_ul:
            html_out.append("</ul>\n")
            inside_ul = False

    def open_ol_if_needed() -> None:
        nonlocal inside_ol
        if not inside_ol:
            html_out.append("<ol>\n")
            inside_ol = True

    def close_ol_if_needed() -> None:
        nonlocal inside_ol
        if inside_ol:
            html_out.append("</ol>\n")
            inside_ol = False

    def open_container_block(name: str, anchor: Optional[str]) -> None:
        nonlocal inside_container, container_name, container_anchor
        flush_paragraph_here()
        close_ul_if_needed()
        close_ol_if_needed()
        flush_pre_if_needed()
        flush_table_if_needed()

        attrs: list[str] = [f'class="block block-{escape_html(name)}"']
        if anchor:
            attrs.append(f'id="{escape_html(anchor)}"')
        html_out.append(f"<div {' '.join(attrs)}>\n")
        inside_container = True
        container_name = name
        container_anchor = anchor

    def close_container_block() -> None:
        nonlocal inside_container, container_name, container_anchor
        flush_paragraph_here()
        close_ul_if_needed()
        close_ol_if_needed()
        flush_pre_if_needed()
        flush_table_if_needed()
        html_out.append("</div>\n")
        inside_container = False
        container_name = None
        container_anchor = None


    def open_table_if_needed() -> None:
        """
        Start a <table>… block if we are not already inside one.

        A table closes any open paragraph or list context.
        """
        nonlocal inside_table
        if not inside_table:
            flush_paragraph_here()
            close_ul_if_needed()
            close_ol_if_needed()
            flush_pre_if_needed()
            html_out.append("<table>\n")
            inside_table = True

    def close_table_if_needed() -> None:
        """
        Close an open <table>… block, if any.
        """
        nonlocal inside_table
        if inside_table:
            html_out.append("</table>\n")
            inside_table = False

    def render_list_item(ev: OrgEvent) -> str:
        """
        Render unordered list items (<ul>).
        """
        text = ev.data.get("text", "") or ""
        tokens = tokenize_inline_org_markup(text)
        return f"<li>{render_inline_tokens(tokens, preamble_macros=latex_macros_preamble)}</li>\n"

    def render_ordered_list_item(ev: OrgEvent) -> str:
        """
        Render ordered list items (<ol>).
        We ignore the numeric index for now and let HTML handle numbering.
        """
        text = ev.data.get("text", "") or ""
        tokens = tokenize_inline_org_markup(text)
        return f"<li>{render_inline_tokens(tokens)}</li>\n"

    def unpack_link_token_value(token_text: str) -> tuple[str, str]:
        """
        For a 'link' token_text that was packed as "url<NULL>desc", return (url, desc).
        """
        NULL_SEP = "\u0000"
        url = token_text
        label = token_text
        if NULL_SEP in token_text:
            url, label = token_text.split(NULL_SEP, 1)
        return url.strip(), (label or "").strip()

    def is_single_image_line(tokens: list[tuple[str, str]]) -> bool:
        """
        True if this token line represents exactly one image-style link.
        """
        if len(tokens) != 1:
            return False
        token_type, token_text = tokens[0]
        if token_type != "link":
            return False
        url, _ = unpack_link_token_value(token_text)
        return is_image_target(url)

    def render_image_figure(
        tokens: list[tuple[str, str]],
        anchor: Optional[str] = None,
        caption_tokens: Optional[list[tuple[str, str]]] = None,
        html_attrs: Optional[dict[str, str]] = None,
    ) -> str:
        """
        Render an image-only line as a <figure> with optional id and figcaption.

        ATTR_HTML is applied to the <img>, not the <figure>.
        """
        # Expect exactly one link token, but be defensive
        url = ""
        label = ""
        if len(tokens) == 1 and tokens[0][0] == "link":
            url, label = unpack_link_token_value(tokens[0][1])
        else:
            # Fallback: just render inline tokens (no special ATTR_HTML handling)
            inner = render_inline_tokens(tokens)
            if anchor:
                return f'<figure id="{escape_html(anchor)}">{inner}</figure>\n'
            return f"<figure>{inner}</figure>\n"

        img_attrs = build_img_attributes(url, label, html_attrs)
        img_html = f"<img{img_attrs} />"

        if anchor:
            opening = f'<figure id="{escape_html(anchor)}">'
        else:
            opening = "<figure>"

        if caption_tokens:
            caption_html = render_inline_tokens(caption_tokens)
            closing = f"<figcaption>{caption_html}</figcaption></figure>\n"
        else:
            closing = "</figure>\n"

        return f"{opening}{img_html}{closing}"

    def render_anchored_image_figure(anchor: str, tokens: list[tuple[str, str]]) -> str:
        """
        Wrap an image-only line into a <figure id="...">…</figure>.
        """
        return (
            f'<figure id="{escape_html(anchor)}">'
            f"{render_inline_tokens(tokens)}"
            f"</figure>\n"
        )

    # --- noexport section capture -----------------------------------------
    noexport_active: bool = False
    noexport_level: int = 0
    noexport_anchor: Optional[str] = None
    noexport_title: str = "noexport"
    noexport_lines: list[str] = []
    noexport_counter: int = 0

    def flush_noexport_block() -> None:
        nonlocal noexport_active, noexport_level, noexport_anchor, noexport_title, noexport_lines, noexport_counter

        if not noexport_active:
            return

        # close any open contexts before inserting the hidden block
        flush_results_block()
        flush_verse_if_needed()
        flush_paragraph(paragraph_buffer, html_out)
        close_ul_if_needed()
        close_ol_if_needed()
        flush_pre_if_needed()
        flush_table_if_needed()

        noexport_counter += 1
        block_id = f"noexport-{noexport_counter}"

        html_out.append(
            render_hidden_comment_block(
                comment_id=block_id,
                anchor=noexport_anchor,
                lines=list(noexport_lines),
                preamble_macros=latex_macros_preamble,
                button_label=f"Show section: {noexport_title}",
            )
        )

        # reset
        noexport_active = False
        noexport_level = 0
        noexport_anchor = None
        noexport_title = "noexport"
        noexport_lines = []


    def build_img_attributes(
        url: str,
        label: str,
        html_attrs: Optional[dict[str, str]],
    ) -> str:
        """
        Build attribute string for an <img> element.

        Default attributes:
          src, alt, title, class="inline-image"
        html_attrs keys (from #+ATTR_HTML:) override or extend them:
          - class: appended to default class
          - others (width, height, id, style, etc.) set directly
        """
        alt_text = label or url
        web_url = normalize_image_src(url)
        base: dict[str, str] = {
            "src": web_url,
            "alt": alt_text,
            "title": alt_text,
            "class": "inline-image",
        }

        if html_attrs:
            for key, value in html_attrs.items():
                if not isinstance(key, str):
                    continue
                key = key.strip()
                if not key:
                    continue
                if not isinstance(value, str):
                    value = str(value)
                value = value.strip()

                if key == "class":
                    # append to existing class
                    old = base.get("class", "")
                    base["class"] = (old + " " + value).strip()
                else:
                    base[key] = value

        # Serialize
        parts: list[str] = []
        for k, v in base.items():
            if v == "":
                continue
            parts.append(f'{k}="{escape_html(v)}"')
        return " " + " ".join(parts) if parts else ""
    #---------------------------------------
    # ---------------- MAIN LOOP -----------
    for line in read_with_includes(input_path, cfg):
        state, events = parse_org_line(line, cfg, state)
        tblfm_here: list[str] = []

        # --- Skip drawers completely in HTML output ----------------
        # Lines between :NAME: and :END: (including the markers) are not
        # rendered. The whole drawer is represented only by the 'drawer'
        # event for consumers that care.
        if getattr(state, "is_inside_drawer", False):
            # Currently inside a drawer (content line)
            continue
        if any(ev.type == "drawer" for ev in events):
            # This was the :END: line that just closed a drawer
            continue

        # ------------------------------------------------------------


        stripped_for_results = line.lstrip()
        if stripped_for_results.startswith("#+RESULTS"):
            # Starting a new RESULTS section.
            if results_mode == "collecting":
                flush_results_block()
            results_mode = "awaiting"
            results_lines = []
            # We ignore the #+RESULTS: directive itself in HTML (it will be
            # skipped later by is_org_directive_line, but we can just continue).
            continue

        current_line_tokens: list[tuple[str, str]] = [("plaintext", line)]
        filtered_events: list[OrgEvent] = []
        has_table_event: bool = False
        for ev in events:
            if ev.type == "line_tokens":
                maybe_tokens = ev.data.get("tokens")
                if isinstance(maybe_tokens, list):
                    current_line_tokens = maybe_tokens
            elif ev.type == "name":
                name_val = ev.data.get("name")
                if isinstance(name_val, str) and name_val.strip():
                    anchor_pending = name_val.strip()
            elif ev.type == "caption":
                tokens = ev.data.get("tokens")
                if isinstance(tokens, list):
                    caption_pending_tokens = tokens
            elif ev.type == "attr_html":
                attrs = ev.data.get("attrs")
                if isinstance(attrs, dict):
                    html_attr_pending = attrs
            elif ev.type in {"table_row", "table_hline"}:
                filtered_events.append(ev)
            elif ev.type == "tblfm":
                parts = ev.data.get("formulas")
                if isinstance(parts, list):
                    tblfm_here.extend([str(p) for p in parts if str(p).strip()])
            elif ev.type == "latex_macro":
                # state already updated; only rebuild string when something was added
                if ev.data.get("added"):
                    latex_macros_preamble = "\n".join(getattr(state, "latex_macro_lines", []))
                # directive line is not body content, so we just swallow it later
            else:
                filtered_events.append(ev)

        if tblfm_here and table_collecting:
            table_tblfm.extend(tblfm_here)
            # do not render this directive line
            continue

        table_events = [ev for ev in filtered_events if ev.type in {"table_row", "table_hline"}]

        if table_events and not inside_pre:
            # starting a new table?
            if not table_collecting:
                flush_paragraph_here()
                close_ul_if_needed()
                close_ol_if_needed()
                flush_pre_if_needed()

                table_collecting = True
                table_anchor = anchor_pending
                table_caption = caption_pending_tokens
                anchor_pending = None
                caption_pending_tokens = None
                html_attr_pending = None  # not used for table in this basic version

            for ev in table_events:
                if ev.type == "table_hline":
                    table_buffer.append({"type": "hline"})
                elif ev.type == "table_row":
                    cells = ev.data.get("cells") or []
                    table_buffer.append({"type": "row", "cells": [str(c) for c in cells]})

            # table lines are fully handled
            continue

        if table_collecting and not table_events:
            # table ended; flush now (unless this line was a tblfm, handled above)
            flush_table_if_needed()
            # continue processing current line normally


        heading_ev = next((ev for ev in filtered_events if ev.type == "heading"), None)

        # --- If we're currently capturing a noexport section ----------------
        if noexport_active:
            # End capture when we hit a heading at same or higher level
            if heading_ev is not None:
                lvl = heading_ev.data.get("level")
                if isinstance(lvl, int) and lvl <= noexport_level:
                    flush_noexport_block()
                    # fall through and process this heading normally
                else:
                    noexport_lines.append(line)
                    continue
            else:
                noexport_lines.append(line)
                continue

        # --- Start capture if this heading is tagged :noexport: -------------
        if heading_ev is not None:
            tags = heading_ev.data.get("tags")
            lvl = heading_ev.data.get("level")

            if isinstance(tags, list) and "noexport" in [t.lower() for t in tags] and isinstance(lvl, int):
                # build a readable title from the heading line
                heading_text = line.lstrip("*").strip()

                # strip trailing " :tag:tag:" portion (same logic as your render_heading)
                if isinstance(tags, list) and " :" in heading_text:
                    heading_text = heading_text.rsplit(" :", 1)[0].rstrip()

                # anchor: prefer event anchor, then pending name
                anchor_for_section = heading_ev.data.get("anchor") or anchor_pending
                if isinstance(anchor_for_section, str) and anchor_for_section.strip():
                    noexport_anchor = anchor_for_section.strip()
                    anchor_pending = None
                else:
                    noexport_anchor = None

                noexport_active = True
                noexport_level = lvl
                noexport_title = heading_text or "noexport"
                noexport_lines = []  # we DO NOT include the heading line itself

                # noexport behaves like comments: ignore other pending attachments
                caption_pending_tokens = None
                html_attr_pending = None

                # swallow this heading line
                continue

        # If we are currently collecting RESULT-lines, swallow non-empty lines
        # into the result buffer until a blank line ends the block.
        if results_mode == "collecting":
            if line.strip() == "":
                # Blank line ends the result block
                flush_results_block()
                # fall through to normal handling of this blank line
            else:
                results_lines.append(line)
                # Do NOT render this line as normal content
                continue

        # If we're inside a verse block, only watch for its end marker.
        if inside_verse:
            has_verse_end = any(
                ev.type == "block_end" and ev.data.get("name") == "verse"
                for ev in filtered_events
            )
            if has_verse_end:
                flush_verse_if_needed()
                continue

            # Collect raw line text; we'll tokenize on flush.
            verse_lines.append(line)
            continue


        # If we're inside a <pre> block, only watch for end events.
        if inside_pre:
            has_end_event = any(
                ev.type in {"block_end", "src_end"} for ev in filtered_events
            )
            if has_end_event:
                flush_pre_if_needed()
                continue

            pre_lines.append(line)
            continue

            # If this line had no table events but we are currently in a table,
            # close the table before handling normal content.
            if not line_consumed and not has_table_event and inside_table:
                close_table_if_needed()


        # Not in pre: handle events
        line_consumed: bool = False

        # If we are awaiting results and this line does NOT start a block,
        # we switch into "collecting" mode and treat the line as result text.
        if results_mode == "awaiting":
            has_block_begin = any(
                ev.type in {"block_begin", "src_begin"}
                for ev in filtered_events
            )
            if not has_block_begin:
                if line.strip() == "":
                    # RESULTS followed by blank -> no content, just reset
                    results_mode = "none"
                else:
                    results_mode = "collecting"
                    results_lines.append(line)
                    # swallow this line as part of results
                    continue

        for ev in filtered_events:
            if ev.type == "heading":
                flush_paragraph_here()
                close_ul_if_needed()
                close_ol_if_needed()
                flush_pre_if_needed()
                close_table_if_needed() 

                # ATTR_HTML does NOT attach to headings in this simple variant
                html_attr_pending = None

                anchor_for_heading = ev.data.get("anchor") or anchor_pending
                html_out.append(render_heading(line, ev, anchor_for_heading))
                if anchor_for_heading:
                    anchor_pending = None
                line_consumed = True

            elif ev.type == "src_begin":
                # src is always verbatim -> <pre>
                if results_mode == "awaiting":
                    results_mode = "none"

                flush_paragraph_here()
                close_ul_if_needed()
                close_ol_if_needed()
                flush_table_if_needed()

                html_attr_pending = None
                inside_pre = True
                pre_open_event = ev
                pre_lines = []
                line_consumed = True

            elif ev.type == "block_begin":
                name = ev.data.get("name") or ""
                verbatim = bool(ev.data.get("verbatim"))

                # results-mode tagging only makes sense for verbatim blocks
                if results_mode == "awaiting":
                    if verbatim and name == "example":
                        ev.data["is_result"] = True
                    results_mode = "none"

                # --- SPECIAL: verse ---------------------------------
                if name == "verse":
                    # verse is standalone, like pre/table
                    flush_paragraph_here()
                    close_ul_if_needed()
                    close_ol_if_needed()
                    flush_pre_if_needed()
                    flush_table_if_needed()

                    # attach anchor if it came via #+NAME (anchor_pending)
                    if not ev.data.get("anchor") and anchor_pending:
                        ev.data["anchor"] = anchor_pending
                        anchor_pending = None

                    html_attr_pending = None  # not used
                    caption_pending_tokens = None  # not used

                    inside_verse = True
                    verse_open_event = ev
                    verse_lines = []
                    line_consumed = True
                    continue
                # ------------------------------------------------------

                # Non-verbatim blocks become containers
                if not verbatim:
                    anchor = ev.data.get("anchor") or anchor_pending
                    open_container_block(str(name), anchor if isinstance(anchor, str) else None)
                    if anchor:
                        anchor_pending = None
                    html_attr_pending = None
                    line_consumed = True
                else:
                    # Verbatim block -> <pre>
                    flush_paragraph_here()
                    close_ul_if_needed()
                    close_ol_if_needed()
                    flush_table_if_needed()
                    html_attr_pending = None
                    inside_pre = True
                    pre_open_event = ev
                    pre_lines = []
                    if ev.data.get("anchor"):
                        anchor_pending = None
                    line_consumed = True

            elif ev.type == "src_end":
                flush_pre_if_needed()
                line_consumed = True

            elif ev.type == "block_end":
                # If we're closing a container, close it; otherwise it’s a verbatim <pre> end.
                name = ev.data.get("name")
                verbatim = bool(ev.data.get("verbatim"))

                if name == "verse":
                    flush_verse_if_needed()
                    line_consumed = True
                    continue

                if inside_container and container_name == name and not verbatim:
                    close_container_block()
                    line_consumed = True
                else:
                    flush_pre_if_needed()
                    line_consumed = True

            elif ev.type == "list_item":
                flush_paragraph_here()
                close_ol_if_needed()
                html_attr_pending = None   # lists don’t use it here
                open_ul_if_needed()
                html_out.append(render_list_item(ev))
                line_consumed = True

            elif ev.type == "ordered_list_item":
                flush_paragraph_here()
                close_ul_if_needed()
                html_attr_pending = None
                open_ol_if_needed()
                html_out.append(render_ordered_list_item(ev))
                line_consumed = True

            elif ev.type == "table_row":   # <-- NEW
                open_table_if_needed()
                cells = ev.data.get("cells") or []
                # Render simple <td> cells; no header detection for now.
                row_html = "".join(f"<td>{escape_html(str(c))}</td>" for c in cells)
                html_out.append(f"<tr>{row_html}</tr>\n")
                line_consumed = True

            elif ev.type == "table_hline":  # <-- NEW
                # For now we ignore horizontal separators in HTML rendering.
                # They still delimit tables structurally via events if needed later.
                open_table_if_needed()
                line_consumed = True

            elif ev.type in {"comment", "comment_block"}:
                flush_paragraph_here()
                close_ul_if_needed()
                close_ol_if_needed()
                flush_pre_if_needed()
                flush_table_if_needed()

                nonlocal_comment_lines: list[str] = []

                anchor = ev.data.get("anchor")
                if not isinstance(anchor, str):
                    anchor = None

                if ev.type == "comment":
                    text = ev.data.get("text", "")
                    nonlocal_comment_lines = [str(text)]
                else:
                    raw_lines = ev.data.get("lines", [])
                    nonlocal_comment_lines = [str(x) for x in raw_lines] if isinstance(raw_lines, list) else []
            
                comment_counter += 1
                comment_id = f"comment-{comment_counter}"
            
                html_out.append(
                    render_hidden_comment_block(
                        comment_id=comment_id,
                        anchor=anchor,
                        lines=nonlocal_comment_lines,
                        preamble_macros=latex_macros_preamble,
                        button_label="Show comment",
                    )
                )
                line_consumed = True


        if line_consumed:
            continue

        # Ignore Org directives (#+TITLE, #+INCLUDE, #+NAME, #+CAPTION, etc.)
        if is_org_directive_line(line):
            continue

        # Handle image-only lines with optional anchor/caption/ATTR_HTML
        if (anchor_pending or caption_pending_tokens or html_attr_pending) and is_single_image_line(current_line_tokens):
            flush_paragraph_here()
            close_ul_if_needed()
            close_ol_if_needed()

            html_out.append(
                render_image_figure(
                    tokens=current_line_tokens,
                    anchor=anchor_pending,
                    caption_tokens=caption_pending_tokens,
                    html_attrs=html_attr_pending,
                )
            )

            anchor_pending = None
            caption_pending_tokens = None
            html_attr_pending = None
            continue

        # Paragraph handling
        if line.strip() == "":
            close_table_if_needed() 
            flush_paragraph_here()
            continue

        add_paragraph_line(current_line_tokens)

    # Final flushes
    flush_results_block()
    flush_verse_if_needed()
    flush_paragraph_here()
    flush_pre_if_needed()
    close_ul_if_needed()
    close_ol_if_needed()
    flush_table_if_needed()
    flush_noexport_block()

    body_html = "".join(html_out)
    return state, body_html

def render_org_to_html_document(
    input_path: Path,
    cfg: OrgReaderConfig,
) -> str:
    """
    Render an Org file (with includes expanded) into a complete HTML document.
    """
    state, body_html = render_org_to_html_body(input_path, cfg)
    document = open_html_document(state.preamble) + body_html + close_html_document()
    return document

def org_to_html(
    input_path: Path,
    output_path: Path,
    cfg: OrgReaderConfig,
) -> None:
    """
    Convert an Org file (with includes expanded) to a minimal HTML document
    and write it to disk.
    """
    document = render_org_to_html_document(input_path, cfg)
    output_path.write_text(document, encoding="utf-8")

def main() -> None:
    parser = argparse.ArgumentParser(description="Convert Org (with includes) to minimal HTML.")
    parser.add_argument("input", nargs="?", default="org/main.org", help="Input Org file (default: org/main.org)")

    parser.add_argument("-o", "--output", default="out.html", help="Output HTML file (default: out.html)")
    parser.add_argument("-c", "--config", default="config.yml", help="Config YAML file (default: config.yml)")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    org_to_html(Path(args.input), Path(args.output), cfg)

if __name__ == "__main__":
    main()
