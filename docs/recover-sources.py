#!/usr/bin/env python3
"""Recover the original TypeScript sources of the KALO Smart app.

The app is an Ionic/Angular app wrapped in Capacitor and ships source maps with
`sourcesContent`, so the original sources can be read straight out of the APK.

    pip install nothing  # stdlib only
    python3 docs/recover-sources.py path/to/de.kalo.smart.apk -o ./kalo-src

An .xapk is a zip of split APKs; extract `de.kalo.smart.apk` from it first.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

WEB_ROOT = "assets/public/"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path, help="base APK (de.kalo.smart.apk)")
    parser.add_argument("-o", "--out", type=Path, default=Path("kalo-src"))
    args = parser.parse_args()

    written = 0
    with zipfile.ZipFile(args.apk) as zf:
        maps = [n for n in zf.namelist() if n.startswith(WEB_ROOT) and n.endswith(".js.map")]
        print(f"{len(maps)} source maps in {args.apk.name}")

        for name in maps:
            try:
                sourcemap = json.loads(zf.read(name))
            except (json.JSONDecodeError, KeyError):
                continue

            sources = sourcemap.get("sources") or []
            contents = sourcemap.get("sourcesContent") or []

            for source, content in zip(sources, contents, strict=False):
                if not content:
                    continue

                relative = source.replace("../", "").replace("./", "").lstrip("/")
                if relative.startswith("node_modules"):
                    continue  # third-party; only the app's own code is interesting

                dest = args.out / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Several chunks embed the same file; keep the longest copy.
                if dest.exists() and dest.stat().st_size >= len(content):
                    continue
                dest.write_text(content)
                written += 1

    print(f"wrote {written} files to {args.out}")


if __name__ == "__main__":
    main()
