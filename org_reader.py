from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterator, Tuple

from config_loader import OrgReaderConfig, load_config
from org_parser import parse_org_line, OrgState
from helper import print_event_gray

def safe_input_path(raw: str, *, root: Path | None = None) -> Path:
    """
    Parse and validate a user-supplied path.

    Goals:
    - reject obvious malicious / malformed inputs (NUL, empty)
    - avoid directory traversal surprises when a root is given
    - resolve symlinks safely (best effort) and return an absolute path
    """
    if not raw or raw.strip() == "":
        raise ValueError("Empty input path.")

    if "\x00" in raw:
        raise ValueError("NUL byte in path is not allowed.")

    p = Path(raw).expanduser()

    # Disallow path traversal patterns (conservative).
    # NOTE: This is stricter than necessary, but good for "input filename safety".
    parts = list(p.parts)
    if any(part == ".." for part in parts):
        raise ValueError("Path traversal ('..') is not allowed.")

    # Resolve to an absolute path
    # strict=False so it can still be resolved even if it doesn't exist (we check after)
    resolved = p.resolve(strict=False)

    if root is not None:
        root_resolved = root.resolve(strict=True)
        try:
            resolved.relative_to(root_resolved)
        except ValueError as e:
            raise ValueError(f"Input path must be within root: {root_resolved}") from e

    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")
    if not resolved.is_file():
        raise IsADirectoryError(f"Not a file: {resolved}")

    return resolved


def is_include_line(line: str, cfg: OrgReaderConfig) -> bool:
    """
    Return True if line looks like an Org include directive.
    Uses cfg.include_keyword_re when available.
    """
    # Prefer regex from config if present
    if hasattr(cfg, "include_keyword_re") and cfg.include_keyword_re is not None:
        return bool(cfg.include_keyword_re.search(line))
    return line.lstrip().lower().startswith("#+include")


def parse_include_target(line: str) -> str | None:
    """
    Extract the include target from a line like:
      #+INCLUDE: "file.org"
      #+INCLUDE: file.org
    Returns the raw target (still maybe quoted) or None if not parseable.
    """
    stripped = line.strip()
    # allow different cases/spaces
    if not stripped.lower().startswith("#+include"):
        return None

    # Split at the first colon
    idx = stripped.find(":")
    if idx == -1:
        return None

    rest = stripped[idx + 1 :].strip()
    if not rest:
        return None

    # strip optional quotes
    if (rest.startswith('"') and rest.endswith('"')) or (rest.startswith("'") and rest.endswith("'")):
        rest = rest[1:-1].strip()

    return rest if rest else None

def un_quote_string(string: str, cfg: OrgReaderConfig) -> str:
    """
    Remove a single matching pair of surrounding quotation marks from a string.
    """
    for open_quote, close_quote in cfg.quotes.items():
        if string.startswith(open_quote) and string.endswith(close_quote):
            return string[len(open_quote):-len(close_quote)].strip()
    return string


def resolve_include(line: str, path: Path, cfg: OrgReaderConfig) -> Path:
    """
    Resolve an Org-style #+INCLUDE directive to an absolute file path.
    """
    include_path = line.split(maxsplit=1)[1]
    include_path = include_path.lstrip(":").strip()
    include_path = un_quote_string(include_path, cfg)
    return (path.parent / include_path).resolve()


def is_include(line: str, cfg: OrgReaderConfig) -> bool:
    """
    Determine whether a line starts an Org-style #+INCLUDE directive.
    """
    return bool(cfg.include_keyword_re.match(line))


def should_skip_header_line(line: str, cfg: OrgReaderConfig) -> bool:
    """
    Determine whether a line is a skippable Org header keyword line.
    """
    match = cfg.header_kv_re.match(line)
    return bool(match and match.group(1).lower() in cfg.skip_header_keys)


def preamble_decision(line: str, cfg: OrgReaderConfig) -> Tuple[bool, bool]:
    """
    Decide whether a line belongs to the preamble of an included Org file.
    """
    stripped = line.strip()

    if stripped == "":
        return True, True

    if should_skip_header_line(line, cfg):
        return True, True

    return False, False


def read_with_includes(
    path: Path,
    cfg: OrgReaderConfig,
    *,
    is_root: bool = True,
) -> Iterator[str]:
    """
    Iterate over an Org file line-by-line, expanding #+INCLUDE directives.

    Does NOT expand includes inside:
      - verbatim blocks (OrgState.is_inside_block)
      - drawers         (OrgState.is_inside_drawer)
      - comment blocks  (OrgState.is_inside_comment)
    """
    path = Path(path)
    state = OrgState()
    in_preamble: bool = not is_root

    with path.open(encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")

            if in_preamble:
                skip, in_preamble = preamble_decision(line, cfg)
                if skip:
                    continue

            # Update state for this line
            state, _ = parse_org_line(line, cfg, state)

            # Expand includes only when NOT inside containers
            if (
                is_include(line, cfg)
                and not state.is_inside_block
                and not state.is_inside_drawer
                and not getattr(state, "is_inside_comment", False)
            ):
                yield from read_with_includes(
                    resolve_include(line, path, cfg),
                    cfg,
                    is_root=False,
                )
                continue

            yield line

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="org_reader.py",
        description="Stream an Org file and expand #+INCLUDE directives (depth-first).",
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="main.org",
        help="Org file to read (default: main.org)",
    )
    parser.add_argument(
        "--config",
        default="config.yml",
        help="Path to config.yml (default: config.yml)",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Optional root directory: input file must be within this directory.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        cfg = load_config(Path(args.config))
    except Exception as e:
        print(f"[org_reader] Failed to load config: {e}", file=sys.stderr)
        return 2

    root_dir: Path | None = None
    if args.root:
        try:
            root_dir = Path(args.root).expanduser().resolve(strict=True)
        except Exception as e:
            print(f"[org_reader] Invalid --root: {e}", file=sys.stderr)
            return 2

    try:
        input_path = safe_input_path(args.input, root=root_dir)
    except Exception as e:
        print(f"[org_reader] Invalid input path: {e}", file=sys.stderr)
        return 2

    try:
        for line in read_with_includes(input_path, cfg):
            print(line)
    except Exception as e:
        print(f"[org_reader] Error while reading: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
