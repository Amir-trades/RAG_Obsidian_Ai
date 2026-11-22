from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
EXPORT_DIR = PROJECT_ROOT / "exports"


def _safe_filename(filename: str) -> str:
    from src.tools import sanitize_filename

    name = sanitize_filename(Path(filename).name)
    if not name.lower().endswith(".docx"):
        name += ".docx"
    return name


def _add_bold_text(paragraph: object, text: str) -> None:
    pattern = re.compile(r"(\*\*[^*]+\*\*|__[^_]+__)")
    for part in pattern.split(text):
        if not part:
            continue
        if (part.startswith("**") and part.endswith("**")) or (
            part.startswith("__") and part.endswith("__")
        ):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        else:
            paragraph.add_run(part)


def _ensure_code_style(document: Document) -> None:
    from docx.enum.style import WD_STYLE_TYPE
    from docx.shared import Pt

    if "Code Block" in document.styles:
        return
    style = document.styles.add_style("Code Block", WD_STYLE_TYPE.PARAGRAPH)
    style.font.name = "Courier New"
    style.font.size = Pt(9)


def markdown_to_docx(markdown_text: str, filename: str) -> str:
    from docx import Document

    if not isinstance(markdown_text, str):
        raise TypeError("markdown_text muss ein String sein.")

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(filename)
    output_path = EXPORT_DIR / safe_name

    document = Document()
    _ensure_code_style(document)
    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    in_code_block = False
    code_lines: list[str] = []

    for line in lines:
        if line.strip().startswith("```"):
            if in_code_block:
                for code_line in code_lines:
                    paragraph = document.add_paragraph(style="Code Block")
                    paragraph.add_run(code_line)
                code_lines = []
            in_code_block = not in_code_block
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        stripped = line.strip()
        if not stripped:
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            paragraph = document.add_heading(level=min(level, 3))
            _add_bold_text(paragraph, heading.group(2))
            continue

        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        if bullet:
            paragraph = document.add_paragraph(style="List Bullet")
            _add_bold_text(paragraph, bullet.group(1))
            continue

        numbered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if numbered:
            paragraph = document.add_paragraph(style="List Number")
            _add_bold_text(paragraph, numbered.group(1))
            continue

        paragraph = document.add_paragraph()
        _add_bold_text(paragraph, stripped)

    document.save(output_path)
    return str(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Markdown als Word-Datei exportieren")
    parser.add_argument("--mock", action="store_true", help="Demo-Dokument erstellen")
    args = parser.parse_args()

    if not args.mock:
        print("Hinweis: Verwende --mock für einen lokalen Export ohne API.")
        return 0

    markdown = """# Mock-Projektplan

## Ziel

- Lokaler **RAG-Assistent**
- Sicheres Schreiben ins Obsidian-Vault

```python
print("Hello from RAG")
```
"""
    path = markdown_to_docx(markdown, "Mock Projektplan")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
