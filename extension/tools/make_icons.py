"""Render the web app's logo into the extension's PNG icons (16, 32, 48, 128 px).

Uses headless Microsoft Edge (already on Windows) with a transparent background:
    python extension/tools/make_icons.py
Run by hand when the logo changes; the PNGs are committed.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGO = ROOT / "src" / "jobhunter" / "web" / "static" / "logo.svg"
OUT = ROOT / "extension" / "icons"
SIZES = (16, 32, 48, 128)
EDGE_PATHS = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


def find_edge() -> Path:
    for path in EDGE_PATHS:
        if path.exists():
            return path
    found = shutil.which("msedge")
    if found:
        return Path(found)
    raise SystemExit("Microsoft Edge not found")


def render(edge: Path, size: int, workdir: Path) -> Path:
    page = workdir / f"icon{size}.html"
    page.write_text(
        "<!doctype html><html><body style='margin:0;background:transparent'>"
        f"<img src='{LOGO.as_uri()}' width='{size}' height='{size}' "
        "style='display:block'></body></html>", encoding="utf-8")
    target = OUT / f"icon{size}.png"
    subprocess.run([
        str(edge), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--default-background-color=00000000",
        "--allow-file-access-from-files", f"--window-size={size},{size}",
        f"--user-data-dir={workdir / 'profile'}", f"--screenshot={target}", page.as_uri(),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    # Edge's helper processes keep the output pipes open; DEVNULL avoids waiting on them.
    if not target.exists():
        raise SystemExit(f"Edge did not write {target}")
    return target


def main() -> int:
    edge = find_edge()
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for size in SIZES:
            print(render(edge, size, Path(tmp)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
