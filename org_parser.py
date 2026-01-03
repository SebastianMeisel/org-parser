#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Any

from config_loader import OrgReaderConfig


@dataclass
class OrgEvent:
    """
    A structured event emitted by the Org parser.

    type:
      - "preamble_kv"
      - "preamble_end"
      - "heading"
      - "block_begin"
      - "block_end"
      - "src_begin"
      - "src_end"
      - "list_item"
      - "ordered_list_item"
      - "name"
      - "caption"
      - "attr_html"
      - "drawer"
      - "table_row"
      - "table_hline"
      - "line_tokens"
      - "tblfm"
      - "comment"
      - "comment_block"
    """
    type: str
    data: dict[str, Any]

@dataclass
class OrgPreamble:
    """
    Parsed ORG preamble (document header keywords).

    Keys are stored lowercased in `headers`. Convenience accessors exist
    for common fields.
    """
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def title(self) -> Optional[str]:
        return self.headers.get("title")

    @property
    def author(self) -> Optional[str]:
        return self.headers.get("author")

    @property
    def date(self) -> Optional[str]:
        return self.headers.get("date")

    @property
    def options(self) -> Optional[str]:
        return self.headers.get("options")


@dataclass
class OrgState:
    """
    Mutable parser state for a streaming Org reader.

    Tracks:
    - preamble (#+KEY: value) until first real content
    - verbatim-block state
    - current section heading level + tags
    - src block options (language + header arguments)
    """
    # Preamble
    preamble: OrgPreamble = field(default_factory=OrgPreamble)
    is_in_preamble: bool = True

    # Block state
    is_inside_block: bool = False
    current_block_name: Optional[str] = None
    last_block_event: str = ""  # "begin" | "end" | ""

    # Block stack (supports nesting)
    block_stack: list[str] = field(default_factory=list)
    block_verbatim_stack: list[bool] = field(default_factory=list)
    current_block_is_verbatim: bool = False

    # True if we are inside *any* verbatim block (as per cfg.verbatim_blocks)
    is_inside_verbatim_block: bool = False

    # SRC options stack (so src can be nested inside non-verbatim containers)
    src_options_stack: list[Optional[dict[str, str]]] = field(default_factory=list)

    # Heading state
    current_heading_level: Optional[int] = None
    current_heading_tags: Optional[list[str]] = None

    # SRC options
    src_block_options: Optional[dict[str, str]] = None

    # Anchors / captions / HTML attributes for “next element”
    pending_anchor: Optional[str] = None
    pending_caption_tokens: Optional[list[tuple[str, str]]] = None
    pending_html_attr: Optional[dict[str, str]] = None

    # Drawer state  --------------------------------------------------- NEW
    is_inside_drawer: bool = False
    current_drawer_name: Optional[str] = None
    current_drawer_lines: Optional[list[str]] = None

    # Comment state --------------------------------------------------- NEW
    is_inside_comment: bool = False
    current_comment_anchor: Optional[str] = None
    current_comment_lines: Optional[list[str]] = None

    # LaTeX
    latex_macro_lines: list[str] = field(default_factory=list)
    latex_macro_set: set[str] = field(default_factory=set)

def calculate_heading_level(asterisks: str) -> int:
    """Convert the heading marker string (e.g. '***') into a numeric level."""
    return len(asterisks)


def extract_heading_tags(trailing: str) -> Optional[list[str]]:
    """
    Extract Org heading tags from the trailing part of a heading line.

    Example: ' :foo:bar:' -> ['foo', 'bar']
    """
    if ":" not in trailing:
        return None

    stripped = trailing.strip()
    if not (stripped.startswith(":") and stripped.endswith(":")):
        return None

    tags = [t for t in stripped.split(":") if t]
    return tags or None

def parse_html_attr_args(arg_string: str) -> dict[str, str]:
    """
    Parse an Org #+ATTR_HTML: argument string into a dict.

    Example:
        ':width 50% :class big img-rounded'
    ->  {'width': '50%', 'class': 'big img-rounded'}

    Very permissive: values may contain spaces until the next ':key'.
    """
    tokens = arg_string.strip().split()
    if not tokens:
        return {}

    attrs: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith(":"):
            key = token[1:]
            i += 1
            values: list[str] = []
            while i < len(tokens) and not tokens[i].startswith(":"):
                values.append(tokens[i])
                i += 1
            attrs[key] = " ".join(values) if values else "true"
        else:
            # stray token: ignore
            i += 1
    return attrs

def parse_src_block_options(arg_string: str) -> dict[str, str]:
    """
    Parse Org src block header arguments.

    Example:
        'python :results output :session foo :tangle out.py'
    ->  {'language': 'python', 'results': 'output', 'session': 'foo', 'tangle': 'out.py'}
    """
    tokens = arg_string.strip().split()
    if not tokens:
        return {}

    options: dict[str, str] = {}
    idx = 0

    # First token is language unless it starts with ':'
    if not tokens[0].startswith(":"):
        options["language"] = tokens[0]
        idx = 1

    # Parse pairs: :key value  (or :flag)
    while idx < len(tokens):
        token = tokens[idx]
        if not token.startswith(":"):
            idx += 1
            continue

        key = token[1:]
        idx += 1

        if idx >= len(tokens) or tokens[idx].startswith(":"):
            options[key] = "true"
            continue

        options[key] = tokens[idx]
        idx += 1

    return options


def _handle_preamble_if_applicable(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    """
    Parse #+KEY: value lines at the beginning of a document.

    Preamble ends when:
      - the first heading is encountered (handled elsewhere), OR
      - we see the first non-empty line that is NOT a #+KEY: directive.
    """
    if not state.is_in_preamble:
        return None

    stripped = line.strip()
    if stripped == "":
        # blank lines are allowed inside preamble
        return None

    # If this looks like a #+KEY: directive, capture it
    match = cfg.header_kv_re.match(line)
    if match:
        key = match.group(1).strip().lower()
        # Everything after the first ":" is treated as the value
        value = line.split(":", 1)[1].strip() if ":" in line else ""
        state.preamble.headers[key] = value
        return OrgEvent(type="preamble_kv", data={"key": key, "value": value})

    # Non-empty line, not a header keyword => preamble ends here
    state.is_in_preamble = False
    return OrgEvent(type="preamble_end", data={"reason": "first_non_header_content"})


def _handle_section_heading_if_present(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    match = cfg.section_heading_re.match(line)
    if not match:
        return None

    # Heading ends the preamble too
    if state.is_in_preamble:
        state.is_in_preamble = False

    heading_level = calculate_heading_level(match.group(1))
    trailing = match.group(3)
    heading_tags = extract_heading_tags(trailing)

    state.current_heading_level = heading_level
    state.current_heading_tags = heading_tags

    anchor = state.pending_anchor
    state.pending_anchor = None

    return OrgEvent(
        type="heading",
        data={
            "level": heading_level,
            "tags": heading_tags,
            "anchor": anchor,
        },
    )

def _handle_latex_macro_keyword_if_present(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    """
    Detect "#+LATEX:" lines that contain macro definitions and cache them.

    We only cache lines that contain one of:
      \\def, \\newcommand, \\renewcommand, \\providecommand,
      \\newenvironment, \\renewenvironment
    """
    match = cfg.header_kv_re.match(line)
    if not match:
        return None

    key = match.group(1).strip().lower()
    if key != "latex":
        return None

    value = line.split(":", 1)[1].strip() if ":" in line else ""
    if not value:
        return OrgEvent(type="latex_macro", data={"raw": "", "added": False, "ignored": True})

    # Only cache macro-definition lines
    if not cfg.latex_macro_re.search(value):
        return OrgEvent(type="latex_macro", data={"raw": value, "added": False, "ignored": True})

    # Deduplicate (keep stable order)
    if value not in state.latex_macro_set:
        state.latex_macro_set.add(value)
        state.latex_macro_lines.append(value)
        return OrgEvent(type="latex_macro", data={"raw": value, "added": True, "ignored": False})

    return OrgEvent(type="latex_macro", data={"raw": value, "added": False, "ignored": False})

def _handle_block_marker_if_present(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    """
    Detect Org block begin/end markers and update state.

    With your current config.yml, block_re has a 3rd capture group for remainder:
      ^\\s*#\\+(begin|end)_(\\w+)\\b\\s*(.*)$
    """
    match = cfg.block_re.match(line)
    if not match:
        return None

    side = match.group(1).lower()  # "begin" | "end"
    block_name = match.group(2).lower()

    remainder = ""
    if match.lastindex and match.lastindex >= 3:
        remainder = (match.group(3) or "").strip()

    is_verbatim = block_name in cfg.verbatim_blocks

    # ----------------- BEGIN -----------------
    if side == "begin":
        state.block_stack.append(block_name)
        state.block_verbatim_stack.append(is_verbatim)
        state.is_inside_verbatim_block = any(state.block_verbatim_stack)

        state.is_inside_block = True
        state.current_block_name = state.block_stack[-1]
        state.current_block_is_verbatim = state.block_verbatim_stack[-1]
        state.last_block_event = "begin"

        anchor = state.pending_anchor
        state.pending_anchor = None

        if block_name == "src":
            opts = parse_src_block_options(remainder)
            state.src_options_stack.append(opts or None)
            state.src_block_options = opts or None
            return OrgEvent(
                type="src_begin",
                data={
                    "name": "src",
                    "src": opts or None,
                    "verbatim": True,  # src is always treated as verbatim content
                    "heading_level": state.current_heading_level,
                    "heading_tags": state.current_heading_tags,
                    "anchor": anchor,
                },
            )

        state.src_options_stack.append(None)
        state.src_block_options = None

        return OrgEvent(
            type="block_begin",
            data={
                "name": block_name,
                "verbatim": is_verbatim,
                "anchor": anchor,
            },
        )

    # ----------------- END -----------------
    if not state.block_stack:
        return OrgEvent(type="block_end", data={"name": block_name, "orphan_ignored": True})

    expected = state.block_stack[-1]
    if expected != block_name:
        return OrgEvent(
            type="block_end",
            data={"name": block_name, "mismatch_ignored": True, "expected": expected},
        )

    # Pop stacks
    popped_name = state.block_stack.pop()
    popped_verbatim = state.block_verbatim_stack.pop()
    popped_src_opts = state.src_options_stack.pop() if state.src_options_stack else None

    state.is_inside_verbatim_block = any(state.block_verbatim_stack)

    state.is_inside_block = bool(state.block_stack)
    state.current_block_name = state.block_stack[-1] if state.block_stack else None
    state.current_block_is_verbatim = state.block_verbatim_stack[-1] if state.block_verbatim_stack else False
    state.last_block_event = "end"

    if popped_name == "src":
        state.src_block_options = state.src_options_stack[-1] if state.src_options_stack else None
        return OrgEvent(
            type="src_end",
            data={
                "name": "src",
                "src": popped_src_opts,
                "heading_level": state.current_heading_level,
                "heading_tags": state.current_heading_tags,
            },
        )

    state.src_block_options = state.src_options_stack[-1] if state.src_options_stack else None
    return OrgEvent(type="block_end", data={"name": block_name, "verbatim": popped_verbatim})

def _handle_drawer_if_present(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    """
    Track simple Org drawers of the form

      :NAME:
      ...
      :END:

    We collect all content lines and emit a single 'drawer' event when
    the closing :END: is seen.

    The event data dict is constructed with the drawer name as the
    first key, e.g.:

      {
          "name":  "PROPERTIES",
          "lines": ["FOO: bar", "BAZ: qux"],
      }
    """
    # Already inside a drawer?
    if state.is_inside_drawer:
        # Closing line?
        if cfg.drawer_end_re.match(line):
            data: dict[str, Any] = {}
            data["name"] = state.current_drawer_name or ""
            data["lines"] = list(state.current_drawer_lines or [])

            event = OrgEvent(type="drawer", data=data)

            # Reset state
            state.is_inside_drawer = False
            state.current_drawer_name = None
            state.current_drawer_lines = None
            return event

        # Content line inside drawer
        if state.current_drawer_lines is None:
            state.current_drawer_lines = []
        state.current_drawer_lines.append(line)
        return None

    # Not inside a drawer: check for :NAME:
    m = cfg.drawer_begin_re.match(line)
    if not m:
        return None

    name = m.group(1)
    state.is_inside_drawer = True
    state.current_drawer_name = name
    state.current_drawer_lines = []
    # No event yet; will be emitted on :END:
    return None


    # side == "end"
    if not state.is_inside_block:
        return OrgEvent(type="block_end", data={"name": block_name, "orphan_ignored": True})

    if state.current_block_name != block_name:
        return OrgEvent(
            type="block_end",
            data={"name": block_name, "mismatch_ignored": True, "expected": state.current_block_name},
        )

    state.is_inside_block = False
    state.current_block_name = None
    state.last_block_event = "end"

    if block_name == "src":
        src_payload = state.src_block_options
        state.src_block_options = None
        return OrgEvent(
            type="src_end",
            data={
                "name": "src",
                "src": src_payload,
                "heading_level": state.current_heading_level,
                "heading_tags": state.current_heading_tags,
            },
        )

    state.src_block_options = None
    return OrgEvent(type="block_end", data={"name": block_name})

def _handle_table_if_present(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    """
    Detect simple Org tables and turn each row into an event.

    Supported forms (minimal):

      | a | b | c |
      | 1 | 2 | 3 |
      |----+----|
      |------+--+---|

    We emit:

      - "table_hline" for horizontal separator lines
      - "table_row"   for data rows, with cells as a list of strings
    """
    stripped = line.lstrip()
    if not stripped.startswith("|"):
        return None

    core = stripped.strip()

    # Identify horizontal rule lines like |-----+----|
    inner = core.strip("|").strip()
    if inner and all(ch in "-+ " for ch in inner):
        return OrgEvent(
            type="table_hline",
            data={"raw": line},
        )

    # Data row: split into cells
    row_inner = core
    if row_inner.startswith("|"):
        row_inner = row_inner[1:]
    if row_inner.endswith("|"):
        row_inner = row_inner[:-1]

    raw_cells = row_inner.split("|")
    cells = [c.strip() for c in raw_cells]

    return OrgEvent(
        type="table_row",
        data={
            "cells": cells,   # <-- this is your “array” of cell contents
            "raw": line,
        },
    )

def _handle_tblfm_keyword_if_present(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    """
    Detect a TBLFM keyword line directly below an Org table:

      #+TBLFM: $4=$1+$2:: $5=$3*2

    We emit:
      OrgEvent(type="tblfm", data={"raw": "<full rhs>", "formulas": ["...", "..."]})
    """
    match = cfg.header_kv_re.match(line)
    if not match:
        return None

    key = match.group(1).strip().lower()
    if key != "tblfm":
        return None

    value = line.split(":", 1)[1].strip() if ":" in line else ""
    parts = [p.strip() for p in value.split("::") if p.strip()]
    return OrgEvent(
        type="tblfm",
        data={"raw": value, "formulas": parts},
    )

def _handle_comment_if_present(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    """
    Handle Org comments:

    1) Comment blocks:
         #+begin_comment
         ...
         #+end_comment

       We collect inner lines and emit ONE 'comment_block' event when
       the closing marker is seen.

    2) Single-line comments:
         # this is a comment

       Lines starting with '#' (after whitespace) but NOT '#+' are emitted
       as 'comment' events and are not treated as normal content.
    """
    # --- inside comment block: collect until end marker
    if state.is_inside_comment:
        if cfg.comment_end_re.match(line):
            event = OrgEvent(
                type="comment_block",
                data={
                    "anchor": state.current_comment_anchor,
                    "lines": list(state.current_comment_lines or []),
                },
            )
            state.is_inside_comment = False
            state.current_comment_anchor = None
            state.current_comment_lines = None
            return event

        if state.current_comment_lines is None:
            state.current_comment_lines = []
        state.current_comment_lines.append(line)
        return None

    # --- not inside: begin marker?
    if cfg.comment_begin_re.match(line):
        state.is_inside_comment = True
        state.current_comment_lines = []
        # attach #+NAME: (if present) to this comment block
        state.current_comment_anchor = state.pending_anchor
        state.pending_anchor = None
        return None

    # --- single-line comment: '#' but not '#+'
    stripped = line.lstrip()
    if stripped.startswith("#") and not stripped.lower().startswith("#+"):
        anchor = state.pending_anchor
        state.pending_anchor = None

        text = stripped[1:]
        if text.startswith(" "):
            text = text[1:]

        return OrgEvent(
            type="comment",
            data={
                "anchor": anchor,
                "text": text,
            },
        )

    return None


def _handle_unordered_list_if_present(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    """
    Detect unordered list items like:
      - item
      + item

    We do NOT treat heading lines as list items; parse_org_line() ensures
    this is only called when no heading was detected.
    """
    match = cfg.unordered_list_re.match(line)
    if not match:
        return None

    # Indentation depth (spaces) – useful later if you want nested <ul>
    indent = len(line) - len(line.lstrip(" "))

    # Text after the bullet
    item_text = match.group(1) if match.lastindex else ""

    return OrgEvent(
        type="list_item",
        data={
            "indent": indent,
            "text": item_text,
        },
    )

def _handle_ordered_list_if_present(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    """
    Detect ordered list items like:
      1. first
      2) second
    """
    match = cfg.ordered_list_re.match(line)
    if not match:
        return None

    indent = len(line) - len(line.lstrip(" "))
    number_str = match.group(1)
    text = match.group(2) if match.lastindex and match.lastindex >= 2 else ""

    try:
        index = int(number_str)
    except ValueError:
        index = None

    return OrgEvent(
        type="ordered_list_item",
        data={
            "indent": indent,
            "raw_index": number_str,
            "index": index,
            "text": text,
        },
    )

def _handle_name_keyword_if_present(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    """
    Detect and store anchors from lines like:

      #+NAME: fig:my-figure

    The name is stored in state.pending_anchor and emitted as a 'name' event.
    The actual attachment to heading/block/image happens later.
    """
    match = cfg.header_kv_re.match(line)
    if not match:
        return None

    key = match.group(1).strip().lower()
    if key != "name":
        return None

    value = line.split(":", 1)[1].strip() if ":" in line else ""
    if value:
        state.pending_anchor = value
    else:
        state.pending_anchor = None

    return OrgEvent(type="name", data={"name": value})

def _handle_caption_keyword_if_present(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    """
    Detect and store captions from lines like:

      #+CAPTION: This is *bold* and [[https://example.com][linked]]

    Caption text is parsed as inline Org markup and stored in
    state.pending_caption_tokens for the next image (or other consumer).
    """
    match = cfg.header_kv_re.match(line)
    if not match:
        return None

    key = match.group(1).strip().lower()
    if key != "caption":
        return None

    value = line.split(":", 1)[1].strip() if ":" in line else ""
    tokens = tokenize_inline_org_markup(value) if value else []

    state.pending_caption_tokens = tokens or None

    return OrgEvent(
        type="caption",
        data={
            "caption": value,
            "tokens": tokens,
        },
    )

def _handle_attr_html_keyword_if_present(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> Optional[OrgEvent]:
    """
    Detect and store HTML attributes from lines like:

      #+ATTR_HTML: :width 50% :class big

    Attributes are parsed into a dict and stored in state.pending_html_attr
    to be applied to the next suitable element (e.g. an image).
    """
    match = cfg.header_kv_re.match(line)
    if not match:
        return None

    key = match.group(1).strip().lower()
    if key != "attr_html":
        return None

    value = line.split(":", 1)[1].strip() if ":" in line else ""
    attrs = parse_html_attr_args(value) if value else {}

    state.pending_html_attr = attrs or None

    return OrgEvent(
        type="attr_html",
        data={
            "raw": value,
            "attrs": attrs,
        },
    )


def tokenize_inline_org_markup(text: str) -> list[tuple[str, str]]:
    """
    Tokenize a line into (type, text) spans.

    Supported (minimal):
      *bold*        -> ("bold_text", "bold")
      /italic/      -> ("italic_text", "italic")
      =code= or ~code~ -> ("code", "code")
      [[url]] or [[url][desc]] -> ("link", packed_url_desc)
      \\(...\\) and $...$        -> ("math_inline", "raw latex source")

    Everything else -> ("plaintext", "...")

    Notes:
    - Non-nested: we do not parse markup inside markup.
    - Very permissive: intended as a simple teaching/utility tokenizer.
    """
    delimiter_to_type: dict[str, str] = {
        "*": "bold_text",
        "/": "italic_text",
        "=": "code",
        "~": "code",
    }

    tokens: list[tuple[str, str]] = []
    buffer: list[str] = []

    def flush_plaintext() -> None:
        if buffer:
            tokens.append(("plaintext", "".join(buffer)))
            buffer.clear()

    def is_valid_emphasis_open(pos: int, delim: str) -> bool:
        # Avoid treating leading heading stars "* " as emphasis
        if delim == "*" and pos == 0:
            if text.startswith("* "):
                return False
        # Require some content after delimiter and not immediately whitespace
        if pos + 1 >= len(text):
            return False
        if text[pos + 1].isspace():
            return False
        # Avoid delimiter being part of a word like foo*bar (very rough)
        if pos > 0 and not text[pos - 1].isspace() and text[pos - 1] not in "([{\"'":
            return False
        return True

    def is_valid_emphasis_close(pos: int, delim: str) -> bool:
        # Require some content before delimiter and not immediately whitespace
        if pos - 1 < 0:
            return False
        if text[pos - 1].isspace():
            return False
        # Avoid closing inside a word like foo*bar (very rough)
        if pos + 1 < len(text) and not text[pos + 1].isspace() and text[pos + 1] not in ".,;:!?)]}\"'":
            return False
        return True

    NULL_SEP = "\u0000"  # used to pack url + desc for link tokens

    i = 0
    while i < len(text):
        # --- inline math \( ... \) -----------------------------
        if text.startswith(r"\(", i):
            end = text.find(r"\)", i + 2)
            if end != -1:
                inner = text[i + 2 : end]   # without delimiters
                flush_plaintext()
                tokens.append(("math_inline", inner))
                i = end + 2
                continue

        ch = text[i]

        # --- display math $$ ... $$ (for now: treat as plaintext) ----------
        if ch == "$" and i + 1 < len(text) and text[i + 1] == "$":
            end = text.find("$$", i + 2)
            if end != -1:
                # keep the whole $$...$$ sequence as plain text
                buffer.append(text[i : end + 2])
                i = end + 2
                continue
            # no closing $$ found -> fall through to single-$ handling

        # --- inline math $ ... $ -------------------------------------------
        if ch == "$":
            j = i + 1
            closing = -1
            while j < len(text):
                if text[j] == "$":
                    closing = j
                    break
                j += 1

            if closing != -1:
                inner = text[i + 1 : closing]
                if inner.strip():
                    flush_plaintext()
                    tokens.append(("math_inline", inner))
                    i = closing + 1
                    continue

            # no closing or only whitespace -> treat literal '$'
            buffer.append("$")
            i += 1
            continue

        # --- Org-style links: [[url]] or [[url][desc]] ---------------------
        if i + 1 < len(text) and text[i] == "[" and text[i + 1] == "[":
            end = text.find("]]", i + 2)
            if end != -1:
                inner = text[i + 2 : end]
                if "][" in inner:
                    url, desc = inner.split("][", 1)
                else:
                    url = inner
                    desc = inner

                url = url.strip()
                desc = desc.strip()

                flush_plaintext()
                tokens.append(("link", f"{url}{NULL_SEP}{desc}"))
                i = end + 2
                continue
            # no closing "]]" found -> fall through as plaintext

        # --- emphasis / code delimiters ------------------------------------
        if ch in delimiter_to_type and is_valid_emphasis_open(i, ch):
            # find closing delimiter
            j = i + 1
            while j < len(text):
                if text[j] == ch and is_valid_emphasis_close(j, ch):
                    # emit
                    flush_plaintext()
                    inner = text[i + 1 : j]
                    tokens.append((delimiter_to_type[ch], inner))
                    i = j + 1
                    break
                j += 1
            else:
                # no close found -> treat as plaintext
                buffer.append(ch)
                i += 1
        else:
            buffer.append(ch)
            i += 1


    flush_plaintext()
    return tokens


def make_line_token_event(line: str) -> OrgEvent:
    """
    Public wrapper: tokenize the line into spans.

    If you later add more inline constructs, keep this as the stable API.
    """
    return OrgEvent(
            type="line_tokens",
            data={"tokens": tokenize_inline_org_markup(line)},
    )


def parse_org_line(
    line: str,
    cfg: OrgReaderConfig,
    state: OrgState,
) -> tuple[OrgState, list[OrgEvent]]:
    events: list[OrgEvent] = []

    preamble_event = _handle_preamble_if_applicable(line, cfg, state)
    if preamble_event:
        events.append(preamble_event)
        if preamble_event.type == "preamble_kv":
            line_event = make_line_token_event(line)
            events.append(line_event)
            return state, events

    # ----- Comments ------------------------------------------------
    comment_event = _handle_comment_if_present(line, cfg, state)
    if comment_event is not None:
        events.append(comment_event)
        return state, events
    if state.is_inside_comment:
        # swallow lines while collecting comment block
        return state, events
    # ---------------------------------------------------------------

    # ----- Drawers -------------------------------------------------
    drawer_event = _handle_drawer_if_present(line, cfg, state)
    if drawer_event is not None:
        events.append(drawer_event)
        return state, events
    if state.is_inside_drawer:
        return state, events
    # ---------------------------------------------------------------

    # ----- Tables -------------------------------------------------- 
    table_event = _handle_table_if_present(line, cfg, state)
    if table_event is not None:
        events.append(table_event)
        # No line_tokens for tables (for now), and we don't treat them
        # as paragraphs or lists.
        return state, events

    # #+TBLFM:
    tblfm_event = _handle_tblfm_keyword_if_present(line, cfg, state)
    if tblfm_event:
        events.append(tblfm_event)
        # Optional: tokens for debug
        line_event = make_line_token_event(line)
        events.append(line_event)
        return state, events
    # ---------------------------------------------------------------
    # #+NAME:
    name_event = _handle_name_keyword_if_present(line, cfg, state)
    if name_event:
        events.append(name_event)
        line_event = make_line_token_event(line)
        events.append(line_event)
        return state, events

    # #+CAPTION:
    caption_event = _handle_caption_keyword_if_present(line, cfg, state)
    if caption_event:
        events.append(caption_event)
        line_event = make_line_token_event(line)
        events.append(line_event)
        return state, events

    # #+ATTR_HTML:
    attr_html_event = _handle_attr_html_keyword_if_present(line, cfg, state)
    if attr_html_event:
        events.append(attr_html_event)
        line_event = make_line_token_event(line)
        events.append(line_event)
        return state, events

    # #+LATEX: (macro caching)
    latex_macro_event = _handle_latex_macro_keyword_if_present(line, cfg, state)
    if latex_macro_event:
        events.append(latex_macro_event)
        line_event = make_line_token_event(line)
        events.append(line_event)
        return state, events

    heading_event = _handle_section_heading_if_present(line, cfg, state)
    if heading_event:
        events.append(heading_event)

    block_event = _handle_block_marker_if_present(line, cfg, state)
    if block_event:
        events.append(block_event)

    # Lists: only if we did NOT detect a heading on this line
    if not heading_event:
        ul_event = _handle_unordered_list_if_present(line, cfg, state)
        if ul_event:
            events.append(ul_event)
        else:
            ol_event = _handle_ordered_list_if_present(line, cfg, state)
            if ol_event:
                events.append(ol_event)

    line_event = make_line_token_event(line)
    if line_event:
        events.append(line_event)

    return state, events
