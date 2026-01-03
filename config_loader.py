# config_loader.py
from __future__ import annotations

from pathlib import Path
from typing import Any
import re

try:
    import yaml  # PyYAML
except ImportError as e:
    raise SystemExit(
        "Missing dependency: PyYAML\n"
        "Install with: python -m pip install pyyaml"
    ) from e


class OrgReaderConfig:
    """
    Immutable-ish container for Org reader configuration.
    """

    def __init__(
        self,
        *,
        verbatim_blocks: set[str],
        skip_header_keys: set[str],
        quotes: dict[str, str],
        block_re: re.Pattern,
        header_kv_re: re.Pattern,
        include_keyword_re: re.Pattern,
        section_heading_re: re.Pattern,
        unordered_list_re: re.Pattern,
        ordered_list_re: re.Pattern,
        drawer_begin_re: re.Pattern,
        drawer_end_re: re.Pattern,
        comment_begin_re: re.Pattern,
        comment_end_re: re.Pattern,
        latex_macro_re: re.Pattern,   # <-- NEW
    ):
        self.verbatim_blocks = verbatim_blocks
        self.skip_header_keys = skip_header_keys
        self.quotes = quotes
        self.block_re = block_re
        self.header_kv_re = header_kv_re
        self.include_keyword_re = include_keyword_re
        self.section_heading_re = section_heading_re
        self.unordered_list_re = unordered_list_re
        self.ordered_list_re = ordered_list_re
        self.drawer_begin_re = drawer_begin_re
        self.drawer_end_re = drawer_end_re
        self.comment_begin_re = comment_begin_re
        self.comment_end_re = comment_end_re
        self.latex_macro_re = latex_macro_re  # <-- NEW


# ---------------- Defaults ---------------------------------------------------

DEFAULT_CONFIG = OrgReaderConfig(
    verbatim_blocks={"example", "src", "verbatim", "export", "quote"},
    skip_header_keys={"title", "author", "date", "options"},
    quotes={'"': '"', "'": "'"},
    block_re=re.compile(r"^\s*#\+(begin|end)_(\w+)\b\s*(.*)$", re.IGNORECASE),
    header_kv_re=re.compile(r"^\s*#\+([A-Za-z0-9_-]+)\s*:", re.IGNORECASE),
    include_keyword_re=re.compile(r"^\s*#\+include\b", re.IGNORECASE),
    section_heading_re=re.compile(r"^([*]+)\s+([^:]*)(.*)$", re.IGNORECASE),
    unordered_list_re=re.compile(r"^\s*[-+]\s+(.*)$", re.IGNORECASE),
    ordered_list_re=re.compile(r"^\s*(\d+)[.)]\s+(.*)$", re.IGNORECASE),
    drawer_begin_re=re.compile(r"^\s*:([A-Za-z0-9_@#%]+):\s*$", re.IGNORECASE),
    drawer_end_re=re.compile(r"^\s*:END:\s*$", re.IGNORECASE),
    comment_begin_re=re.compile(r"^\s*#\+begin_comment\b", re.IGNORECASE),
    comment_end_re=re.compile(r"^\s*#\+end_comment\b", re.IGNORECASE),
    latex_macro_re=re.compile(
        r"\\(?:def|newcommand|renewcommand|providecommand|newenvironment|renewenvironment)\b"
    ),
)

# ---------------- Loader -----------------------------------------------------


def _as_lower_str_set(value: Any, name: str) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list of strings")
    return {str(v).lower() for v in value}


def load_config(path: Path) -> OrgReaderConfig:
    """
    Load YAML config and return an OrgReaderConfig instance.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise TypeError("Config root must be a mapping")

    regex = raw.get("regex", {})

    return OrgReaderConfig(
        verbatim_blocks=_as_lower_str_set(
            raw.get("verbatim_blocks", list(DEFAULT_CONFIG.verbatim_blocks)),
            "verbatim_blocks",
        ),
        skip_header_keys=_as_lower_str_set(
            raw.get("skip_header_keys", list(DEFAULT_CONFIG.skip_header_keys)),
            "skip_header_keys",
        ),
        quotes=dict(raw.get("quotes", DEFAULT_CONFIG.quotes)),
        block_re=re.compile(
            regex.get("block_re", DEFAULT_CONFIG.block_re.pattern),
            re.IGNORECASE,
        ),
        header_kv_re=re.compile(
            regex.get("header_kv_re", DEFAULT_CONFIG.header_kv_re.pattern),
            re.IGNORECASE,
        ),
        include_keyword_re=re.compile(
            regex.get("include_keyword", DEFAULT_CONFIG.include_keyword_re.pattern),
            re.IGNORECASE,
        ),
        section_heading_re=re.compile(
            regex.get("section_heading_re", DEFAULT_CONFIG.section_heading_re.pattern),
            re.IGNORECASE,
        ),
        unordered_list_re=re.compile(
            regex.get("unordered_list_re", DEFAULT_CONFIG.unordered_list_re.pattern),
            re.IGNORECASE,
        ),
        ordered_list_re=re.compile(
            regex.get("ordered_list_re", DEFAULT_CONFIG.ordered_list_re.pattern),
            re.IGNORECASE,
        ),
        drawer_begin_re=re.compile(
            regex.get("drawer_begin_re", DEFAULT_CONFIG.drawer_begin_re.pattern),
            re.IGNORECASE,
        ),
        drawer_end_re=re.compile(
            regex.get("drawer_end_re", DEFAULT_CONFIG.drawer_end_re.pattern),
            re.IGNORECASE,
        ),
        comment_begin_re=re.compile(
            regex.get("comment_begin_re", DEFAULT_CONFIG.comment_begin_re.pattern),
            re.IGNORECASE,
        ),
        comment_end_re=re.compile(
            regex.get("comment_end_re", DEFAULT_CONFIG.comment_end_re.pattern),
            re.IGNORECASE,
        ),
        latex_macro_re=re.compile(
            regex.get("latex_macro_re", DEFAULT_CONFIG.latex_macro_re.pattern)
        ),
    )
