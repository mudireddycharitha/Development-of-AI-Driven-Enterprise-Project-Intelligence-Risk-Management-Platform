import json
import os
from pathlib import Path

import pytest

from app.services.chunking import TextSegment, approximate_token_count, chunk_segments
from app.services.document_parser import extract_segments
from app.services.pipeline import safe_error_message


def test_chunking_preserves_overlap_and_source_location() -> None:
    segments = [TextSegment("one two three four five six seven eight nine ten eleven", {"page_number": 3})]
    chunks = chunk_segments(segments, chunk_size=5, overlap=2)
    assert len(chunks) >= 3
    assert chunks[0]["location"] == {"page_number": 3}
    assert chunks[0]["text"].split()[-2:] == chunks[1]["text"].split()[:2]
    assert "eleven" in chunks[-1]["text"]
    assert all(approximate_token_count(chunk["text"]) == chunk["token_count"] for chunk in chunks)


def test_chunking_skips_empty_segments_and_rejects_invalid_configuration() -> None:
    assert chunk_segments([TextSegment(" ", {})], 10, 1) == []
    with pytest.raises(ValueError):
        chunk_segments([TextSegment("text", {})], 10, 10)


def test_processing_errors_do_not_disclose_local_paths() -> None:
    message = safe_error_message(Exception("Could not open 'C:\\Users\\example\\private\\broken.docx'"))
    assert "C:\\" not in message
    assert "<local path>" in message


def test_txt_markdown_and_source_code_preserve_lines(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("print('hello')\n\nvalue = 2\n", encoding="utf-8")
    segments = extract_segments(source)
    assert [item.location["line_start"] for item in segments] == [1, 3]
    assert "print('hello')" in segments[0].text


def test_csv_keeps_headers_rows_and_row_numbers(tmp_path: Path) -> None:
    source = tmp_path / "projects.csv"
    source.write_text("Project,Status,Risk\nAlpha,Delayed,Resource shortage\n", encoding="utf-8")
    segments = extract_segments(source)
    assert "Project: Alpha" in segments[0].text
    assert "Status: Delayed" in segments[0].text
    assert segments[0].location == {"row_number": 2}


def test_json_xml_and_html_extract_readable_text(tmp_path: Path) -> None:
    json_file = tmp_path / "nested.json"
    json_file.write_text(json.dumps({"project": {"name": "Alpha"}, "risks": ["schedule"]}), encoding="utf-8")
    assert any("project.name: Alpha" in item.text for item in extract_segments(json_file))
    xml_file = tmp_path / "nested.xml"
    xml_file.write_text("<root><project>Alpha</project></root>", encoding="utf-8")
    assert any("root.project: Alpha" in item.text for item in extract_segments(xml_file))
    html_file = tmp_path / "page.html"
    html_file.write_text("<h1>Project</h1><script>unsafe()</script><p>Summary</p>", encoding="utf-8")
    content = "\n".join(item.text for item in extract_segments(html_file))
    assert "Project" in content and "Summary" in content and "unsafe" not in content


def test_docx_workbook_presentation_and_pdf_extraction(tmp_path: Path) -> None:
    from docx import Document
    docx = tmp_path / "report.docx"
    word = Document()
    word.add_paragraph("Project alpha")
    table = word.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Risk"
    table.cell(0, 1).text = "Schedule"
    word.save(docx)
    assert any("Project alpha" in item.text for item in extract_segments(docx))
    assert any("Risk | Schedule" in item.text for item in extract_segments(docx))

    from openpyxl import Workbook
    xlsx = tmp_path / "tracker.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Portfolio"
    sheet.append(["Project", "Status"])
    sheet.append(["Alpha", "Delayed"])
    workbook.save(xlsx)
    excel_segments = extract_segments(xlsx)
    assert excel_segments[0].location == {"sheet_name": "Portfolio", "row_number": 2}
    assert "Project: Alpha" in excel_segments[0].text

    from pptx import Presentation
    from pptx.util import Inches
    pptx = tmp_path / "briefing.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = "Quarterly risks"
    table = slide.shapes.add_table(1, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "Risk"
    table.cell(0, 1).text = "Mitigation"
    presentation.save(pptx)
    presentation_segments = extract_segments(pptx)
    assert presentation_segments[0].location == {"slide_number": 1}
    assert "Quarterly risks" in presentation_segments[0].text
    assert "Risk | Mitigation" in presentation_segments[0].text

    import fitz
    pdf = tmp_path / "plan.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Delivery plan")
    document.save(pdf)
    document.close()
    pdf_segments = extract_segments(pdf)
    assert pdf_segments[0].location == {"page_number": 1}
    assert "Delivery plan" in pdf_segments[0].text


def test_empty_and_corrupt_documents_are_not_successful(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.touch()
    with pytest.raises(ValueError, match="empty"):
        extract_segments(empty)
    corrupt_pdf = tmp_path / "corrupt.pdf"
    corrupt_pdf.write_bytes(b"not a pdf")
    with pytest.raises(Exception):
        extract_segments(corrupt_pdf)


@pytest.mark.skipif(os.getenv("RUN_MODEL_INTEGRATION") != "1", reason="Set RUN_MODEL_INTEGRATION=1 to load the real model.")
def test_real_embedding_model_returns_finite_384_dimensional_vectors() -> None:
    from app.services.embedding_service import embed_texts
    import math

    vector = embed_texts(["A project delivery risk requires an owner and mitigation plan."])[0]
    assert len(vector) == 384
    assert all(math.isfinite(value) for value in vector)
