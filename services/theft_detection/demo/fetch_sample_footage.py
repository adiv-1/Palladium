"""Download the openly available footage used for local evaluation into the ignored data/ folder.

Nothing downloaded here is committed. Check each source's licence yourself before any use
beyond local testing.

    python demo/fetch_sample_footage.py
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

INTEL = "https://github.com/intel-iot-devkit/sample-videos/raw/master"   # CC-BY-4.0
MIXKIT = "https://assets.mixkit.co/videos"                                # Mixkit Stock Video Free License

FILES = {
    "data/samples/people-detection.mp4": f"{INTEL}/people-detection.mp4",
    "data/samples/one-by-one-person-detection.mp4": f"{INTEL}/one-by-one-person-detection.mp4",
    "data/samples/store-aisle-detection.mp4": f"{INTEL}/store-aisle-detection.mp4",
    "data/samples/worker-zone-detection.mp4": f"{INTEL}/worker-zone-detection.mp4",
    "data/samples/classroom.mp4": f"{INTEL}/classroom.mp4",
    "data/samples/face-demographics-walking-and-pause.mp4": f"{INTEL}/face-demographics-walking-and-pause.mp4",
    "data/stock/mixkit_31359.mp4": f"{MIXKIT}/31359/31359-720.mp4",
    "data/stock/mixkit_31395.mp4": f"{MIXKIT}/31395/31395-720.mp4",
    "data/stock/mixkit_31354.mp4": f"{MIXKIT}/31354/31354-720.mp4",
}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for rel, url in FILES.items():
        dest = root / rel
        if dest.exists():
            print(f"have  {rel}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)  # noqa: S310 - fixed https URLs above
        tmp.replace(dest)
        print(f"got   {rel}")


if __name__ == "__main__":
    main()
