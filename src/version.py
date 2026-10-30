from pathlib import Path

VERSION = (
    (Path(__file__).resolve().parents[1] / "version.txt")
    .read_text(encoding="utf-8")
    .strip()
)
