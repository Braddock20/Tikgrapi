#!/usr/bin/env python3
"""Fetch the OFL-licensed fonts the composition engine uses.

The fonts are grouped into the families the design engine pairs them from:
handwritten, serif, sans, typewriter and decorative. Everything downloaded
here is SIL Open Font License 1.1, so it can be published.

    python tools/fetch_fonts.py --dest assets/fonts

Existing files are skipped, so the script is safe to re-run.
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

BASE = "https://github.com/google/fonts/raw/main"

# family folder -> (category, [(file name on GitHub, local file name)])
FAMILIES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "patrickyhand": ("handwritten", [("ofl/patrickhand/PatrickHand-Regular.ttf", "PatrickHand-Regular.ttf")]),
    "indieflower": ("handwritten", [("ofl/indieflower/IndieFlower-Regular.ttf", "IndieFlower-Regular.ttf")]),
    "kalam": ("handwritten", [("ofl/kalam/Kalam-Regular.ttf", "Kalam-Regular.ttf"),
                              ("ofl/kalam/Kalam-Bold.ttf", "Kalam-Bold.ttf")]),
    "shadowsintolight": ("handwritten", [("ofl/shadowsintolight/ShadowsIntoLight-Regular.ttf", "ShadowsIntoLight-Regular.ttf")]),
    "caveat": ("handwritten", [("ofl/caveat/Caveat%5Bwght%5D.ttf", "Caveat-Variable.ttf")]),
    "dmserifdisplay": ("serif", [("ofl/dmserifdisplay/DMSerifDisplay-Regular.ttf", "DMSerifDisplay-Regular.ttf"),
                                 ("ofl/dmserifdisplay/DMSerifDisplay-Italic.ttf", "DMSerifDisplay-Italic.ttf")]),
    "librebaskerville": ("serif", [("ofl/librebaskerville/LibreBaskerville-Regular.ttf", "LibreBaskerville-Regular.ttf"),
                                   ("ofl/librebaskerville/LibreBaskerville-Italic.ttf", "LibreBaskerville-Italic.ttf")]),
    "playfairdisplay": ("serif", [("ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf", "PlayfairDisplay-Variable.ttf"),
                                  ("ofl/playfairdisplay/PlayfairDisplay-Italic%5Bwght%5D.ttf", "PlayfairDisplay-Italic-Variable.ttf")]),
    "cormorantgaramond": ("serif", [("ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf", "CormorantGaramond-Variable.ttf")]),
    "inter": ("sans", [("ofl/inter/Inter%5Bopsz,wght%5D.ttf", "Inter-Variable.ttf")]),
    "montserrat": ("sans", [("ofl/montserrat/Montserrat%5Bwght%5D.ttf", "Montserrat-Variable.ttf")]),
    "jost": ("sans", [("ofl/jost/Jost%5Bwght%5D.ttf", "Jost-Variable.ttf")]),
    "spacegrotesk": ("sans", [("ofl/spacegrotesk/SpaceGrotesk%5Bwght%5D.ttf", "SpaceGrotesk-Variable.ttf")]),
    "oswald": ("sans", [("ofl/oswald/Oswald%5Bwght%5D.ttf", "Oswald-Variable.ttf")]),
    "bebasneue": ("sans", [("ofl/bebasneue/BebasNeue-Regular.ttf", "BebasNeue-Regular.ttf")]),
    "cousine": ("typewriter", [("ofl/cousine/Cousine-Regular.ttf", "Cousine-Regular.ttf"),
                               ("ofl/cousine/Cousine-Bold.ttf", "Cousine-Bold.ttf")]),
    "specialelite": ("typewriter", [("ofl/specialelite/SpecialElite-Regular.ttf", "SpecialElite-Regular.ttf")]),
    "courierprime": ("typewriter", [("ofl/courierprime/CourierPrime-Regular.ttf", "CourierPrime-Regular.ttf"),
                                    ("ofl/courierprime/CourierPrime-Bold.ttf", "CourierPrime-Bold.ttf")]),
    "jetbrainsmono": ("typewriter", [("ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf", "JetBrainsMono-Variable.ttf")]),
    "lobster": ("decorative", [("ofl/lobster/Lobster-Regular.ttf", "Lobster-Regular.ttf")]),
    "pacifico": ("decorative", [("ofl/pacifico/Pacifico-Regular.ttf", "Pacifico-Regular.ttf")]),
    "greatvibes": ("decorative", [("ofl/greatvibes/GreatVibes-Regular.ttf", "GreatVibes-Regular.ttf")]),
    "satisfy": ("decorative", [("ofl/satisfy/Satisfy-Regular.ttf", "Satisfy-Regular.ttf")]),
}


def fetch(url: str, dest: Path) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=45) as response:
            data = response.read()
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {dest.name}: {type(exc).__name__}: {exc}")
        return False
    if len(data) < 2000:
        print(f"  ! {dest.name}: suspiciously small ({len(data)} bytes)")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", default="assets/fonts")
    args = ap.parse_args()
    dest = Path(args.dest)
    ok = 0
    failed = 0
    for family, (category, files) in FAMILIES.items():
        for remote, local in files:
            target = dest / category / local
            if target.exists() and target.stat().st_size > 2000:
                ok += 1
                continue
            url = f"{BASE}/{remote}"
            if fetch(url, target):
                print(f"  + {category}/{local}")
                ok += 1
            else:
                failed += 1
    print(f"fonts: {ok} available, {failed} failed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
