"""Read text from pixels (terminals, games, anything AT-SPI/CDP can't see) with Tesseract.

The region is captured at 2x scale first: measured 2026-10-14 on an Alacritty window,
Tesseract with `por` at 1x mangled accents ("ação" -> "acdo") while 2x read Portuguese
text correctly. Strings of loose symbols (`@#*~^´`) are still unreliable — this is for
reading words/labels, not exact symbol transcription.

Word boxes are mapped back to global screen coordinates so a match's `center` can go
straight into the `click` tool.
"""

from __future__ import annotations

import subprocess

from . import screenshot

SCALE = 2


class OcrError(RuntimeError):
    pass


def read(x: int, y: int, w: int, h: int, lang: str = "por+eng", min_conf: float = 40) -> dict:
    png = screenshot.capture_region_scaled(x, y, w, h, SCALE)
    try:
        tsv = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", lang, "tsv"],
            input=png, capture_output=True, timeout=60, check=True,
        ).stdout.decode(errors="replace")
    except FileNotFoundError as e:
        raise OcrError("tesseract not found (pacman -S tesseract tesseract-data-por tesseract-data-eng)") from e
    except subprocess.CalledProcessError as e:
        raise OcrError(f"tesseract failed: {e.stderr.decode(errors='replace').strip()}") from e

    words = []
    lines: dict[tuple[str, str, str], list[str]] = {}
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) != 12 or cols[0] != "5":  # level 5 = word
            continue
        text, conf = cols[11].strip(), float(cols[10])
        if not text or conf < min_conf:
            continue
        left, top, width, height = (int(v) / SCALE for v in cols[6:10])
        words.append({
            "text": text,
            "conf": round(conf),
            "center": [round(x + left + width / 2), round(y + top + height / 2)],
        })
        lines.setdefault((cols[2], cols[3], cols[4]), []).append(text)
    return {"text": "\n".join(" ".join(ws) for ws in lines.values()), "words": words}
