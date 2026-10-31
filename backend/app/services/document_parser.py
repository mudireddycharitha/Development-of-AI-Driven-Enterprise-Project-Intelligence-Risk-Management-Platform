import csv
import html
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.services.chunking import TextSegment
from app.services.text_cleaner import clean_text

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS: dict[str, str] = {
    ".pdf": "PDF", ".docx": "Word document", ".doc": "Legacy Word document",
    ".txt": "Text", ".md": "Markdown", ".rtf": "Rich Text Format",
    ".csv": "CSV spreadsheet", ".xlsx": "Excel workbook", ".xls": "Legacy Excel workbook",
    ".pptx": "PowerPoint presentation", ".ppt": "Legacy PowerPoint presentation",
    ".json": "JSON", ".xml": "XML", ".html": "HTML", ".htm": "HTML",
    ".log": "Log", ".py": "Python source", ".js": "JavaScript source",
    ".ts": "TypeScript source", ".tsx": "TypeScript React source", ".jsx": "JavaScript React source",
    ".java": "Java source", ".c": "C source", ".cpp": "C++ source", ".h": "Header source",
    ".css": "CSS source", ".sql": "SQL source",
}


def _decode(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _plain_text(path: Path) -> list[TextSegment]:
    text = _decode(path)
    return [TextSegment(line.rstrip(), {"line_start": i, "line_end": i})
            for i, line in enumerate(text.splitlines(), start=1) if line.strip()]


def _document(path: Path) -> list[TextSegment]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    segments: list[TextSegment] = []
    for element in document.element.body.iterchildren():
        if element.tag.endswith("}p"):
            paragraph = Paragraph(element, document)
            if paragraph.text.strip():
                segments.append(TextSegment(paragraph.text, {}))
        elif element.tag.endswith("}tbl"):
            table = Table(element, document)
            for row_index, row in enumerate(table.rows, start=1):
                values = [cell.text.strip().replace("\n", " / ") for cell in row.cells]
                if any(values):
                    segments.append(TextSegment(
                        "Table row: " + " | ".join(values),
                        {"row_number": row_index},
                    ))
    return segments


def _spreadsheet(path: Path) -> list[TextSegment]:
    extension = path.suffix.lower()
    segments: list[TextSegment] = []
    if extension == ".csv":
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            rows = list(csv.reader(handle))
        if not rows:
            return []
        headers = [cell.strip() for cell in rows[0]]
        for row_number, row in enumerate(rows[1:], start=2):
            pairs = [f"{headers[i] or f'Column {i + 1}'}: {value.strip()}"
                     for i, value in enumerate(row) if value.strip()]
            if pairs:
                segments.append(TextSegment(
                    f"Source: {path.name}\nRow: {row_number}\n" + "\n".join(pairs),
                    {"row_number": row_number},
                ))
        return segments

    if extension == ".xlsx":
        from openpyxl import load_workbook
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                rows = sheet.iter_rows(values_only=True)
                headers = [str(value).strip() if value is not None else ""
                           for value in next(rows, ())]
                for row_number, row in enumerate(rows, start=2):
                    pairs = [f"{headers[i] or f'Column {i + 1}'}: {value}"
                             for i, value in enumerate(row)
                             if value is not None and str(value).strip()]
                    if pairs:
                        segments.append(TextSegment(
                            f"Workbook: {path.name}\nWorksheet: {sheet.title}\nRow: {row_number}\n"
                            + "\n".join(pairs),
                            {"sheet_name": sheet.title, "row_number": row_number},
                        ))
        finally:
            workbook.close()
        return segments

    import pandas as pd
    workbook = pd.ExcelFile(path, engine="xlrd")
    for sheet_name in workbook.sheet_names:
        frame = pd.read_excel(workbook, sheet_name=sheet_name, dtype=object)
        headers = [str(value) for value in frame.columns]
        for offset, row in frame.iterrows():
            pairs = [f"{headers[i]}: {value}" for i, value in enumerate(row.tolist())
                     if value is not None and str(value) != "nan"]
            if pairs:
                row_number = int(offset) + 2
                segments.append(TextSegment(
                    f"Workbook: {path.name}\nWorksheet: {sheet_name}\nRow: {row_number}\n"
                    + "\n".join(pairs),
                    {"sheet_name": str(sheet_name), "row_number": row_number},
                ))
    return segments


def _pdf(path: Path) -> list[TextSegment]:
    import fitz
    segments = []
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text")
            if text.strip():
                segments.append(TextSegment(
                    f"Page {page_number}\n{text}",
                    {"page_number": page_number},
                ))
    if not segments:
        raise ValueError("No extractable PDF text was found; this file may require OCR.")
    return segments


def _presentation(path: Path) -> list[TextSegment]:
    from pptx import Presentation
    presentation = Presentation(str(path))
    segments = []
    for slide_number, slide in enumerate(presentation.slides, start=1):
        parts = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.text.strip():
                parts.append(shape.text.strip())
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    values = [cell.text.strip().replace("\n", " / ") for cell in row.cells]
                    if any(values):
                        parts.append(" | ".join(values))
        if parts:
            segments.append(TextSegment(
                f"Slide {slide_number}\n" + "\n".join(parts),
                {"slide_number": slide_number},
            ))
    return segments


def _legacy_office(path: Path) -> list[TextSegment]:
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if not executable:
        raise ValueError(
            f"Legacy {path.suffix.upper()} conversion requires LibreOffice (soffice) on PATH."
        )
    target = "docx" if path.suffix.lower() == ".doc" else "pptx"
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory)
        result = subprocess.run(
            [executable, "--headless", "--convert-to", target, "--outdir", str(output), str(path)],
            capture_output=True, text=True, timeout=90, check=False,
        )
        converted = output / f"{path.stem}.{target}"
        if result.returncode != 0 or not converted.exists():
            logger.warning("LibreOffice conversion failed: %s", result.stderr.strip())
            raise ValueError("LibreOffice could not convert this legacy Office file.")
        return _document(converted) if target == "docx" else _presentation(converted)


def _structured_json(path: Path) -> list[TextSegment]:
    value = json.loads(_decode(path))
    segments: list[TextSegment] = []

    def visit(item: Any, key_path: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, f"{key_path}.{key}" if key_path else str(key))
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{key_path}[{index}]")
        else:
            segments.append(TextSegment(f"{key_path}: {item}", {}))

    visit(value, "")
    return segments


def _structured_xml(path: Path) -> list[TextSegment]:
    from defusedxml import ElementTree as ET

    root = ET.fromstring(_decode(path))
    segments: list[TextSegment] = []

    def visit(element: ET.Element, parent: str = "") -> None:
        name = element.tag.rsplit("}", 1)[-1]
        location = f"{parent}.{name}" if parent else name
        if element.text and element.text.strip():
            segments.append(TextSegment(f"{location}: {element.text.strip()}", {}))
        for child in element:
            visit(child, location)
        if element.tail and element.tail.strip():
            segments.append(TextSegment(f"{location}: {element.tail.strip()}", {}))

    visit(root)
    return segments


def _html(path: Path) -> list[TextSegment]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_decode(path), "html.parser")
    for element in soup(["script", "style", "noscript", "template"]):
        element.decompose()
    text = html.unescape(soup.get_text("\n", strip=True))
    return [TextSegment(line, {}) for line in text.splitlines() if line.strip()]


def extract_segments(path: Path) -> list[TextSegment]:
    extension = path.suffix.lower()
    if extension not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported file format: {extension or '(no extension)'}")
    if path.stat().st_size == 0:
        raise ValueError("The uploaded file is empty.")
    if extension == ".pdf":
        segments = _pdf(path)
    elif extension == ".docx":
        segments = _document(path)
    elif extension in {".csv", ".xlsx", ".xls"}:
        segments = _spreadsheet(path)
    elif extension == ".pptx":
        segments = _presentation(path)
    elif extension in {".doc", ".ppt"}:
        segments = _legacy_office(path)
    elif extension == ".json":
        segments = _structured_json(path)
    elif extension == ".xml":
        segments = _structured_xml(path)
    elif extension in {".html", ".htm"}:
        segments = _html(path)
    elif extension == ".rtf":
        from striprtf.striprtf import rtf_to_text
        segments = [TextSegment(line, {}) for line in rtf_to_text(_decode(path)).splitlines() if line.strip()]
    else:
        segments = _plain_text(path)

    cleaned = [TextSegment(clean_text(segment.text), segment.location)
               for segment in segments if clean_text(segment.text)]
    if not cleaned:
        raise ValueError("No readable text could be extracted from this file.")
    return cleaned
