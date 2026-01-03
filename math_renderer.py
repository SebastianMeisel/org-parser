# math_renderer.py
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile


def render_math_to_svg(
    math_src: str,
    out_path: Path,
    *,
    preamble_macros: str = "",
) -> None:
    """
    Render inline math to an SVG using LaTeX + dvisvgm.

    - math_src: raw LaTeX snippet, e.g. r"\\sum^{superscript}"
    - out_path: final SVG path, e.g. BASE_DIR / ".math-cache" / "<digest>.svg"
    - preamble_macros: LaTeX macro definitions to inject before \\begin{document}

    Requires `latex` and `dvisvgm` in PATH.
    """
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    macros = (preamble_macros or "").strip()

    # 1) Write a minimal standalone LaTeX document to a *temporary* .tex
    with tempfile.NamedTemporaryFile("w", suffix=".tex", delete=False) as f:
        tex_path = Path(f.name)
        f.write(r"\documentclass{standalone}" "\n")

        # Inject macro definitions into the preamble (recommended place).
        if macros:
            f.write("% --- org-viewer macros (cached from #+LATEX:) ---\n")
            f.write(macros)
            f.write("\n% --- end macros ---\n")

        f.write(r"\begin{document}" "\n")
        f.write("$")
        f.write(math_src)
        f.write("$\n")
        f.write(r"\end{document}" "\n")

    workdir = tex_path.parent

    # 2) latex -> dvi
    subprocess.run(
        ["latex", "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
        cwd=workdir,
        check=True,
    )

    dvi_path = tex_path.with_suffix(".dvi")

    # 3) dvi -> svg, written *directly* to out_path
    subprocess.run(
        [
            "dvisvgm",
            "-n",
            "-a",
            "-o",
            str(out_path),
            str(dvi_path),
        ],
        check=True,
    )
    
