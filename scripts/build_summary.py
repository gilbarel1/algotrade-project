"""Render docs/project_summary.html -> docs/project_summary.pdf (WeasyPrint).

The summary document is a graded deliverable, and it was previously exported by
hand — so the committed PDF could silently drift from the committed HTML. This
makes the build one command and reproducible.

WeasyPrint (not a browser) is the right renderer here: the stylesheet uses CSS
Paged Media — `@page` with an `@bottom-center` running footer and
`counter(page)/counter(pages)` — which browsers' print-to-PDF largely ignore.
It is already a dependency of the report pipeline (§8), so nothing new is added.

Usage:
    npm run summary                # docs/project_summary.html -> .pdf
    python scripts/build_summary.py [src.html] [out.pdf]
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = REPO_ROOT / "docs" / "project_summary.html"
DEFAULT_OUT = REPO_ROOT / "docs" / "project_summary.pdf"


def main() -> int:
    # Resolve so a relative argument still prints (and renders) sanely.
    src = (Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC).resolve()
    out = (Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT).resolve()

    if not src.is_file():
        print(f"error: {src} not found", file=sys.stderr)
        return 1

    try:
        from weasyprint import HTML
    except OSError as exc:  # GTK/Pango missing (the documented Windows prerequisite)
        print(
            f"error: WeasyPrint could not load its native libraries ({exc}).\n"
            "On Windows install the GTK3 runtime (see README prerequisites), then retry.",
            file=sys.stderr,
        )
        return 2

    # base_url lets any relative asset in the HTML resolve against docs/.
    HTML(filename=str(src), base_url=str(src.parent)).write_pdf(str(out))
    size_kb = out.stat().st_size / 1024

    def show(p: Path) -> str:
        try:
            return str(p.relative_to(REPO_ROOT))
        except ValueError:  # an out-of-tree path (e.g. a temp file) is fine
            return str(p)

    print(f"{show(src)} -> {show(out)}  ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
