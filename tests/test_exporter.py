from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm

from src import exporter


def _xml_attribute(element: object, tag: str, attribute: str) -> list[str | None]:
    return [
        node.get(qn(attribute))
        for node in element.iter(qn(tag))
    ]


def test_standard_export_renders_inline_markdown_tables_and_blocks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(exporter, "EXPORT_DIR", tmp_path)
    markdown = (
        "#### Details\n\n"
        "**bold** *italic* `code` [Docs](https://example.test)\n\n"
        "| Name | Value |\n|---|---|\n| Aqua | ready |\n\n"
        "> A useful quote.\n\n---\n\n"
        "```python\nprint('ok')\n```"
    )

    path = exporter.markdown_to_docx(markdown, "nested/report", "standard")
    document = Document(path)

    assert Path(path).parent == tmp_path
    assert document.tables[0].cell(1, 0).text == "Aqua"
    assert document.tables[0].style.name == "Table Grid"
    assert any("bold" in run.text and run.bold for p in document.paragraphs for run in p.runs)
    assert any("italic" in run.text and run.italic for p in document.paragraphs for run in p.runs)
    assert any("code" in run.text and run.font.name == "Courier New" for p in document.paragraphs for run in p.runs)
    assert "Docs (https://example.test)" in "\n".join(p.text for p in document.paragraphs)
    assert any(p.style.name == "Heading 4" for p in document.paragraphs)
    assert any("A useful quote." in p.text for p in document.paragraphs)
    assert abs(document.sections[0].left_margin - Cm(2.5)) <= 500
    assert _xml_attribute(
        document.tables[0].cell(0, 0)._tc,
        "w:shd",
        "w:fill",
    ) == ["E8ECF2"]


def test_cover_letter_alias_uses_letter_margins_and_formats_subject_and_date(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(exporter, "EXPORT_DIR", tmp_path)
    path = exporter.markdown_to_docx(
        "Absender\n\nEmpfänger\n\n2. Januar 2026\n\n"
        "Betreff: Bewerbung als Architekt\n\nBrieftext.",
        "cover",
        "anschreiben",
    )
    document = Document(path)

    assert abs(document.sections[0].left_margin - Cm(2.5)) <= 500
    assert abs(document.sections[0].right_margin - Cm(2.0)) <= 500
    assert document.paragraphs[2].alignment == WD_ALIGN_PARAGRAPH.RIGHT
    subject = next(p for p in document.paragraphs if p.text.startswith("Betreff:"))
    assert all(run.bold for run in subject.runs if run.text.strip())


def test_cv_alias_uses_compact_margins_and_borderless_two_column_table(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(exporter, "EXPORT_DIR", tmp_path)
    path = exporter.markdown_to_docx(
        "# Lebenslauf\n\n## Berufserfahrung\n\n"
        "| Zeitraum | Station |\n|---|---|\n| 2024–heute | Aqua |\n",
        "cv",
        "lebenslauf",
    )
    document = Document(path)

    assert abs(document.sections[0].top_margin - Cm(1.8)) <= 500
    assert document.tables[0].cell(1, 1).text == "Aqua"
    borders = document.tables[0]._tbl.tblPr.find(qn("w:tblBorders"))
    assert borders is not None
    assert [
        borders.find(qn(f"w:{edge}")).get(qn("w:val"))
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
    ] == ["nil"] * 6


def test_exporter_rejects_unknown_style(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(exporter, "EXPORT_DIR", tmp_path)

    try:
        exporter.markdown_to_docx("Text", "bad", "unknown")
    except ValueError as exc:
        assert "doc_style" in str(exc)
    else:
        raise AssertionError("Unknown document style should fail clearly.")


def test_cover_letter_city_date_and_code_block_font_size(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from docx.shared import Pt

    monkeypatch.setattr(exporter, "EXPORT_DIR", tmp_path)
    path = exporter.markdown_to_docx(
        "Absender\n\nEmpfänger\n\nKetsch, 28. November 2026\n\n"
        "Betreff: Test\n\n```python\nprint('hi')\n```",
        "letter_city",
        "cover_letter",
    )
    document = Document(path)

    assert document.paragraphs[2].alignment == WD_ALIGN_PARAGRAPH.RIGHT
    code_runs = [
        run
        for paragraph in document.paragraphs
        for run in paragraph.runs
        if run.font.name == "Courier New"
    ]
    assert code_runs and code_runs[0].font.size == Pt(9)
