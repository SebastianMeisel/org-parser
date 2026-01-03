#!/usr/bin/env python3
from __future__ import annotations
from hashlib import sha1
from pathlib import Path
from typing import Iterable

from dataclasses import dataclass, field
from urllib.parse import quote
import html as _html

from flask import Flask, abort, render_template_string, send_from_directory

from math_renderer import render_math_to_svg
from config_loader import load_config
from org_to_html import render_org_to_html_body

BASE_DIR = Path.cwd()
CONFIG_PATH = BASE_DIR / "config.yml"
ORG_DIR = BASE_DIR / "org"
README_PATH = (BASE_DIR / "README.org").resolve()

MATH_CACHE = BASE_DIR / ".math-cache"
MATH_CACHE.mkdir(exist_ok=True)

app = Flask(__name__, static_folder="static", static_url_path="/static")
cfg = load_config(CONFIG_PATH)


LAYOUT_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ page_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="/static/org.css">
  <script src="/static/viewer.js" defer></script>
</head>
<body class="with-sidebar">
  <div class="layout">
    <aside class="sidebar">
      <div class="sidebar-title"><a href="/">Org Viewer</a></div>

      <div class="sidebar-section">
        <div class="sidebar-label">Root</div>
        <div class="fm-file {{ 'active' if current_file == 'README.org' else '' }}">
          <a href="/view/README.org">README.org</a>
        </div>
      </div>

      <div class="sidebar-section">
        <div class="sidebar-label">org/</div>
        {{ file_tree|safe }}
      </div>
    </aside>

    <main class="content">
    <div class="topbar">
    <button id="sidebar-toggle" class="icon-btn" type="button" aria-label="Toggle sidebar" title="Toggle sidebar">
    <img src="/static/sidebar.svg" alt="" width="22" height="22">
    </button>
    <div class="topbar-spacer"></div>
    </div>

    {{ content|safe }}
    </main>  </div>
</body>
</html>
"""

@dataclass
class FileTreeNode:
    dirs: dict[str, "FileTreeNode"] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)

def _insert_path(root: FileTreeNode, rel_parts: tuple[str, ...]) -> None:
    node = root
    for part in rel_parts[:-1]:
        node = node.dirs.setdefault(part, FileTreeNode())
    node.files.append(rel_parts[-1])

def build_org_tree(org_dir: Path) -> FileTreeNode:
    root = FileTreeNode()
    if not org_dir.exists():
        return root

    for p in sorted(org_dir.glob("**/*.org")):
        # skip hidden dirs
        if any(seg.startswith(".") for seg in p.relative_to(org_dir).parts):
            continue
        rel = p.relative_to(org_dir)
        _insert_path(root, rel.parts)
    return root

def _open_dir_set_for_current(current_rel: str) -> set[str]:
    """
    current_rel is like 'org/2025/foo.org'. We want open dirs within ORG_DIR:
      '2025' for example.
    """
    if not current_rel.startswith("org/"):
        return set()
    inner = current_rel[len("org/"):]
    parts = [p for p in inner.split("/") if p]
    # ancestors excluding filename
    open_dirs: set[str] = set()
    acc: list[str] = []
    for seg in parts[:-1]:
        acc.append(seg)
        open_dirs.add("/".join(acc))
    return open_dirs

def render_tree_html(node: FileTreeNode, *, prefix: str, open_dirs: set[str], current_file: str) -> str:
    """
    prefix: path inside org/ (e.g. '' or '2025')
    current_file: full rel path from BASE_DIR (e.g. 'org/2025/foo.org')
    """
    out: list[str] = []

    # directories
    for dirname in sorted(node.dirs.keys()):
        child = node.dirs[dirname]
        child_prefix = f"{prefix}/{dirname}".strip("/")
        open_attr = " open" if child_prefix in open_dirs else ""
        out.append(f'<details class="fm-dir"{open_attr}>')
        out.append(f"<summary>{_html.escape(dirname)}/</summary>")
        out.append('<div class="fm-children">')
        out.append(render_tree_html(child, prefix=child_prefix, open_dirs=open_dirs, current_file=current_file))
        out.append("</div></details>")

    # files
    for fname in sorted(node.files):
        rel_inside_org = f"{prefix}/{fname}".strip("/")
        rel_from_base = f"org/{rel_inside_org}"
        href = "/view/" + quote(rel_from_base)
        active = " active" if rel_from_base == current_file else ""
        out.append(f'<div class="fm-file{active}"><a href="{href}">{_html.escape(fname)}</a></div>')

    return "".join(out)


def _load_math_cache_payload(tex_path: Path) -> tuple[str, str]:
    """
    Load cached macros + math from .math-cache/<digest>.tex

    Supports:
      - new container format (v1)
      - legacy format: file contains only raw math snippet
    """
    raw = tex_path.read_text(encoding="utf-8")

    if raw.startswith("%% org-math-cache-v1"):
        lines = raw.splitlines()
        try:
            i_macros = lines.index("%% macros")
            i_math = lines.index("%% math")
        except ValueError:
            # malformed -> fallback to legacy behavior
            return "", raw.strip()

        macros = "\n".join(lines[i_macros + 1 : i_math]).strip()
        math_src = "\n".join(lines[i_math + 1 :]).strip()
        return macros, math_src

    # legacy: whole file is the math snippet
    return "", raw.strip()

@app.route("/")
def index():
    tree = build_org_tree(ORG_DIR)
    file_tree_html = render_tree_html(
        tree,
        prefix="",
        open_dirs=set(),
        current_file="",
    )

    content = """
      <h1>Org Viewer</h1>
      <p>Wähle links eine Datei aus.</p>
    """

    return render_template_string(
        LAYOUT_TEMPLATE,
        page_title="Org Viewer",
        file_tree=file_tree_html,
        content=content,
        current_file="",
    )


@app.route("/view/<path:filename>")
def view_file(filename: str):
    org_path = (BASE_DIR / filename).resolve()
    try:
        org_path.relative_to(BASE_DIR)
    except ValueError:
        abort(404)

    if not org_path.is_file() or org_path.suffix.lower() != ".org":
        abort(404)

    if org_path != README_PATH:
        try:
            org_path.relative_to(ORG_DIR.resolve())
        except ValueError:
            abort(404)

    # body-only rendering
    state, body_html = render_org_to_html_body(org_path, cfg)

    current_rel = str(org_path.relative_to(BASE_DIR))
    tree = build_org_tree(ORG_DIR)
    open_dirs = _open_dir_set_for_current(current_rel)
    file_tree_html = render_tree_html(tree, prefix="", open_dirs=open_dirs, current_file=current_rel)

    title = state.preamble.title or current_rel

    return render_template_string(
        LAYOUT_TEMPLATE,
        page_title=title,
        file_tree=file_tree_html,
        content=body_html,
        current_file=current_rel,
    )

@app.route("/assets/<path:subpath>")
def assets(subpath: str):
    # Prevent directory traversal
    asset_path = (BASE_DIR / subpath).resolve()
    try:
        asset_path.relative_to(BASE_DIR)
    except ValueError:
        abort(404)

    if not asset_path.exists():
        abort(404)

    return send_from_directory(BASE_DIR, subpath)

if __name__ == "__main__":
    # Run in dev mode
    app.run(debug=False)


@app.route("/math/<digest>.svg")
def math_image(digest: str):
    # Basic safety: only hex digests allowed
    if not all(c in "0123456789abcdef" for c in digest) or len(digest) != 40:
        abort(404)

    svg_path = MATH_CACHE / f"{digest}.svg"

    if not svg_path.exists():
        source_path = MATH_CACHE / f"{digest}.tex"
        if not source_path.exists():
            abort(404)

        macros, math_src = _load_math_cache_payload(source_path)

        try:
            render_math_to_svg(math_src, svg_path, preamble_macros=macros)
        except Exception:
            abort(500)

    return send_from_directory(MATH_CACHE, svg_path.name)
