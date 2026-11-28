from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
EXPORT_DIR = PROJECT_ROOT / "exports"
STYLE_ALIASES = {
    "standard": "standard",
    "cover_letter": "cover_letter",
    "anschreiben": "cover_letter",
    "cv": "cv",
    "lebenslauf": "cv",
}


def _safe_filename(filename: str) -> str:
    from src.tools import sanitize_filename

    name = sanitize_filename(Path(filename).name)
    if not name.lower().endswith(".docx"):
        name += ".docx"
    return name


def _normalize_style(doc_style: str) -> str:
    try:
        return STYLE_ALIASES[doc_style.strip().casefold()]
    except (AttributeError, KeyError) as exc:
        raise ValueError(
            "doc_style muss standard, cover_letter/anschreiben oder cv/lebenslauf sein."
        ) from exc


def _add_inline_markdown(paragraph: object, text: str) -> None:
    from docx.shared import Pt

    pattern = re.compile(
        r"(\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|`[^`]+`|\[[^\]]+\]\([^)]+\))"
    )
    for part in pattern.split(text):
        if not part:
            continue
        if (part.startswith("**") and part.endswith("**")) or (
            part.startswith("__") and part.endswith("__")
        ):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        elif (part.startswith("*") and part.endswith("*")) or (
            part.startswith("_") and part.endswith("_")
        ):
            run = paragraph.add_run(part[1:-1])
            run.italic = True
        elif part.startswith("`") and part.endswith("`"):
            run = paragraph.add_run(part[1:-1])
            run.font.name = "Courier New"
            run.font.size = Pt(9)
        elif part.startswith("[") and "](" in part and part.endswith(")"):
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part).groups()
            paragraph.add_run(f"{label} ({url})")
        else:
            paragraph.add_run(part)


def _set_shading(element: object, fill: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    properties = element._tc.get_or_add_tcPr() if hasattr(element, "_tc") else element._p.get_or_add_pPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def _set_table_borders(table: object, enabled: bool) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    properties = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement(f"w:{edge}")
        border.set(qn("w:val"), "single" if enabled else "nil")
        if enabled:
            border.set(qn("w:sz"), "4")
            border.set(qn("w:color"), "D6DAE1")
        borders.append(border)
    properties.append(borders)


def _set_paragraph_border(
    paragraph: object,
    edge: str = "bottom",
    color: str = "AAB4C3",
    size: str = "6",
) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    properties = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    border = OxmlElement(f"w:{edge}")
    border.set(qn("w:val"), "single")
    border.set(qn("w:sz"), size)
    border.set(qn("w:space"), "4")
    border.set(qn("w:color"), color)
    borders.append(border)
    properties.append(borders)


def _set_cell_margins(
    cell: object,
    top: int = 80,
    bottom: int = 80,
    left: int = 120,
    right: int = 120,
) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    properties = cell._tc.get_or_add_tcPr()
    margins = OxmlElement("w:tcMar")
    for side, value in (
        ("top", top),
        ("bottom", bottom),
        ("left", left),
        ("right", right),
    ):
        node = OxmlElement(f"w:{side}")
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")
        margins.append(node)
    properties.append(margins)


def _configure_document(document: object, style_name: str) -> None:
    from docx.shared import Cm, Pt, RGBColor

    section = document.sections[0]
    if style_name == "cover_letter":
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.0)
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
    elif style_name == "cv":
        section.left_margin = Cm(1.9)
        section.right_margin = Cm(1.9)
        section.top_margin = Cm(1.8)
        section.bottom_margin = Cm(1.8)
    else:
        section.left_margin = section.right_margin = Cm(2.5)
        section.top_margin = section.bottom_margin = Cm(2.5)

    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.color.rgb = RGBColor(34, 40, 49)
    if style_name == "cv":
        normal.font.size = Pt(10)
        normal.paragraph_format.line_spacing = 1.12
        normal.paragraph_format.space_after = Pt(2)
    elif style_name == "cover_letter":
        normal.font.size = Pt(10.5)
        normal.paragraph_format.line_spacing = 1.18
        normal.paragraph_format.space_after = Pt(8)
    else:
        normal.font.size = Pt(10.5)
        normal.paragraph_format.line_spacing = 1.15
        normal.paragraph_format.space_after = Pt(6)

    for bullet_style in ("List Bullet", "List Number"):
        if bullet_style in document.styles:
            list_style = document.styles[bullet_style]
            list_style.font.name = "Arial"
            list_style.font.size = Pt(10 if style_name == "cv" else 10.5)
            list_style.font.color.rgb = RGBColor(34, 40, 49)
            list_style.paragraph_format.space_after = Pt(2 if style_name == "cv" else 4)
            list_style.paragraph_format.line_spacing = 1.12 if style_name == "cv" else 1.15

    heading_sizes = (
        ((1, 20), (2, 12), (3, 10.5), (4, 10))
        if style_name == "cv"
        else ((1, 18), (2, 13.5), (3, 11.5), (4, 10.5))
    )
    for level, size in heading_sizes:
        heading = document.styles[f"Heading {level}"]
        heading.font.name = "Arial"
        heading.font.size = Pt(size)
        heading.font.bold = True
        heading.font.color.rgb = RGBColor(27, 54, 93)
        heading.paragraph_format.keep_with_next = True
        if style_name == "cv":
            if level == 1:
                heading.paragraph_format.space_before = Pt(0)
                heading.paragraph_format.space_after = Pt(4)
            elif level == 2:
                heading.paragraph_format.space_before = Pt(10)
                heading.paragraph_format.space_after = Pt(4)
            else:
                heading.paragraph_format.space_before = Pt(5)
                heading.paragraph_format.space_after = Pt(2)


def _parse_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _parse_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _add_markdown_table(document: object, lines: list[str], style_name: str) -> int:
    from docx.enum.table import WD_ALIGN_VERTICAL
    from docx.shared import Pt, RGBColor

    header = _parse_table_row(lines[0])
    has_separator = len(lines) > 1 and _is_table_separator(lines[1])
    data_start = 2 if has_separator else 1
    rows = [header] + [_parse_table_row(line) for line in lines[data_start:]]
    column_count = max(len(row) for row in rows)
    table = document.add_table(rows=0, cols=column_count)
    table.autofit = False
    if style_name == "standard":
        table.style = "Table Grid"
        _set_table_borders(table, True)
    else:
        _set_table_borders(table, False)

    for row_index, values in enumerate(rows):
        cells = table.add_row().cells
        for column_index, cell in enumerate(cells):
            cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
            _set_cell_margins(
                cell,
                top=60 if style_name == "cv" else 80,
                bottom=60 if style_name == "cv" else 80,
                left=40 if style_name == "cv" and column_index == 0 else 110,
                right=110,
            )
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(2)
            paragraph.paragraph_format.line_spacing = 1.12
            value = values[column_index] if column_index < len(values) else ""
            _add_inline_markdown(paragraph, value)
            if style_name == "standard" and row_index == 0:
                _set_shading(cell, "E8ECF2")
                for run in paragraph.runs:
                    run.bold = True
            elif style_name == "cv":
                if row_index == 0:
                    for run in paragraph.runs:
                        run.bold = True
                        run.font.size = Pt(9)
                        run.font.color.rgb = RGBColor(95, 107, 122)
                elif column_index == 0:
                    for run in paragraph.runs:
                        run.bold = True
                        run.font.size = Pt(9.5)
                        run.font.color.rgb = RGBColor(74, 85, 104)
                else:
                    for run in paragraph.runs:
                        run.font.size = Pt(10)
        if style_name == "cv" and column_count == 2:
            section = document.sections[0]
            content_width = (
                section.page_width - section.left_margin - section.right_margin
            )
            cells[0].width = content_width * 28 // 100
            cells[1].width = content_width * 72 // 100
    return len(lines)


def _is_horizontal_rule(line: str) -> bool:
    return bool(re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,})", line.strip()))


def _is_date_line(line: str) -> bool:
    stripped = line.strip()
    return bool(
        re.match(
            r"^(?:Datum:\s*|[A-Za-zÄÖÜäöüß.\- ]+,\s*(?:den\s+)?)?"
            r"(?:\d{1,2}\.\s*[A-Za-zÄÖÜäöüß]+\s+\d{4}|\d{1,2}\.\d{1,2}\.\d{4})$",
            stripped,
        )
    )


def _is_closing_line(line: str) -> bool:
    return bool(
        re.match(
            r"^(?:Mit freundlichen Grüßen|Freundliche Grüße|Viele Grüße|"
            r"Beste Grüße|Best regards|Kind regards|Sincerely)\b",
            line.strip(),
            re.IGNORECASE,
        )
    )


def markdown_to_docx(
    markdown_text: str,
    filename: str,
    doc_style: str = "standard",
    export_dir: str | Path | None = None,
) -> str:
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    if not isinstance(markdown_text, str):
        raise TypeError("markdown_text muss ein String sein.")
    style_name = _normalize_style(doc_style)
    target_dir = Path(export_dir).expanduser().resolve() if export_dir else EXPORT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / _safe_filename(filename)
    document = Document()
    _configure_document(document, style_name)

    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    in_code_block = False
    code_lines: list[str] = []
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code_block:
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.left_indent = Cm(0.3)
                run = paragraph.add_run("\n".join(code_lines))
                run.font.name = "Courier New"
                run.font.size = Pt(9)
                p_shading = OxmlElement("w:shd")
                p_shading.set(qn("w:fill"), "F1F3F6")
                paragraph._p.get_or_add_pPr().append(p_shading)
                code_lines = []
            in_code_block = not in_code_block
            index += 1
            continue
        if in_code_block:
            code_lines.append(line)
            index += 1
            continue
        if not stripped:
            index += 1
            continue

        if "|" in stripped and index + 1 < len(lines) and _is_table_separator(lines[index + 1]):
            table_lines = [stripped, lines[index + 1].strip()]
            index += 2
            while index < len(lines) and "|" in lines[index]:
                table_lines.append(lines[index].strip())
                index += 1
            _add_markdown_table(document, table_lines, style_name)
            continue

        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            heading_text = heading.group(2)
            if style_name == "cover_letter" and re.match(
                r"^(?:betreff|subject)\s*:",
                heading_text,
                re.IGNORECASE,
            ):
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.space_before = Pt(10)
                paragraph.paragraph_format.space_after = Pt(10)
                paragraph.paragraph_format.keep_with_next = True
                _add_inline_markdown(paragraph, heading_text)
                for run in paragraph.runs:
                    run.bold = True
                    run.font.color.rgb = RGBColor(27, 54, 93)
            else:
                paragraph = document.add_heading(level=level)
                _add_inline_markdown(paragraph, heading_text)
                if style_name == "cv" and level == 1:
                    _set_paragraph_border(paragraph, "bottom", color="1B365D", size="12")
                elif style_name == "cv" and level == 2:
                    _set_paragraph_border(paragraph, "bottom", color="C5CEDB", size="6")
            index += 1
            continue

        if _is_horizontal_rule(stripped):
            _set_paragraph_border(document.add_paragraph())
            index += 1
            continue

        if stripped.startswith(">"):
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.left_indent = Cm(0.6)
            paragraph.paragraph_format.right_indent = Cm(0.2)
            _set_paragraph_border(paragraph, "left")
            _add_inline_markdown(paragraph, stripped[1:].strip())
            index += 1
            continue

        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        if bullet:
            paragraph = document.add_paragraph(style="List Bullet")
            _add_inline_markdown(paragraph, bullet.group(1))
            index += 1
            continue

        numbered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        is_cover_letter_date = (
            style_name == "cover_letter" and _is_date_line(stripped)
        )
        if numbered and not is_cover_letter_date:
            paragraph = document.add_paragraph(style="List Number")
            _add_inline_markdown(paragraph, numbered.group(1))
            index += 1
            continue

        paragraph = document.add_paragraph()
        if style_name == "cover_letter" and _is_date_line(stripped):
            from docx.enum.text import WD_ALIGN_PARAGRAPH

            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            paragraph.paragraph_format.space_before = Pt(6)
            paragraph.paragraph_format.space_after = Pt(8)
        elif style_name == "cover_letter" and _is_closing_line(stripped):
            paragraph.paragraph_format.space_before = Pt(10)
            paragraph.paragraph_format.keep_with_next = True
        is_subject = style_name == "cover_letter" and re.match(
            r"^(?:\*\*)?(?:betreff|subject)\s*:",
            stripped,
            re.IGNORECASE,
        )
        _add_inline_markdown(paragraph, stripped)
        if is_subject:
            paragraph.paragraph_format.space_before = Pt(10)
            paragraph.paragraph_format.space_after = Pt(10)
            paragraph.paragraph_format.keep_with_next = True
            for run in paragraph.runs:
                run.bold = True
                run.font.color.rgb = RGBColor(27, 54, 93)
        index += 1

    if in_code_block and code_lines:
        paragraph = document.add_paragraph()
        run = paragraph.add_run("\n".join(code_lines))
        run.font.name = "Courier New"
        run.font.size = Pt(9)
        _set_shading(paragraph, "F1F3F6")

    document.save(output_path)
    return str(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Markdown als Word-Datei exportieren")
    parser.add_argument("--mock", action="store_true", help="Demo-Dokumente erstellen")
    args = parser.parse_args()

    if not args.mock:
        print("Hinweis: Verwende --mock für einen lokalen Export ohne API.")
        return 0

    samples = {
        "standard": "# Mock-Projektplan\n\n- **Lokaler RAG-Assistent**\n",
        "cover_letter": (
            "# Bewerbung\n\n"
            "**Betreff: Bewerbung als Projektarchitekt**\n\n"
            "Sehr geehrte Damen und Herren,\n\nMit freundlichen Grüßen"
        ),
        "cv": (
            "# Lebenslauf\n\n## Berufserfahrung\n\n"
            "| Zeitraum | Station |\n|---|---|\n| 2024–heute | RAG-Projekt |"
        ),
    }
    with TemporaryDirectory() as output_dir:
        for style_name, markdown in samples.items():
            path = markdown_to_docx(
                markdown,
                f"Mock {style_name}",
                style_name,
                export_dir=output_dir,
            )
            print(Path(path).name)
    print("DOCX Mock erfolgreich")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
