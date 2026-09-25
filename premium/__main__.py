"""
python -m premium run                     scrape every source, then build the map
python -m premium scrape --sources boe    scrape only some sources
python -m premium scrape --only gava castelldefels
python -m premium build                   re-score + rebuild the map from saved data (no network)
"""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser

from . import pipeline


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="premium", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "scrape", "build"])
    ap.add_argument("--sources", nargs="+", default=list(pipeline.SOURCES), choices=pipeline.SOURCES)
    ap.add_argument("--only", nargs="+", metavar="MUNICIPALITY",
                    help="limit scraping to these municipalities (name or slug)")
    ap.add_argument("--open", action="store_true", help="open the map in the browser when done")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    pipeline.load_dotenv()
    cfg = pipeline.load_config()
    if a.command in ("run", "scrape"):
        pipeline.scrape(cfg, a.sources, a.only)
    if a.command in ("run", "build"):
        pipeline.build(cfg)
        if a.open:
            webbrowser.open((pipeline.OUT / "map.html").as_uri())


if __name__ == "__main__":
    main()
