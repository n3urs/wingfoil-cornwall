"""Build a static snapshot of the dashboard for GitHub Pages.

    .venv/bin/python tools/build_static.py

Computes the dashboard exactly like the live server does and writes it, plus
a copy of static/, into _site/ — the folder GitHub Actions publishes to
Pages. Runs on a schedule there (see .github/workflows/pages.yml); locally
it's mostly useful for checking the published output looks right before it
goes live:

    .venv/bin/python tools/build_static.py
    cd _site && python3 -m http.server 8788

Demo mode (?demo=...) doesn't work on the published site — it needs live
computation, which a static file can't offer — so it isn't built here.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from wingfoil.engine import build_dashboard  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"


def main() -> None:
    start = time.monotonic()
    print("Building dashboard…")
    data = build_dashboard(days=7)
    elapsed = time.monotonic() - start

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    # Mirror the local server's own URL layout — index.html at the root
    # (FastAPI serves it at "/"), everything else under static/ (FastAPI
    # mounts static/ there) — so index.html's relative hrefs resolve the
    # same way locally and on a Pages project site served from a subpath.
    shutil.copy(ROOT / "static" / "index.html", OUT / "index.html")
    shutil.copytree(
        ROOT / "static", OUT / "static", ignore=shutil.ignore_patterns("index.html")
    )
    (OUT / "dashboard.json").write_text(json.dumps(data))
    (OUT / ".nojekyll").write_text("")  # stop Pages mangling underscore-prefixed paths

    sources = {s["id"]: s["status"] for s in data["sources"]}
    print(f"Done in {elapsed:.1f}s — {len(data['spots'])} spots, sources: {sources}")
    if data["warnings"]:
        print("Warnings:")
        for w in data["warnings"]:
            print(f"  - {w}")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
