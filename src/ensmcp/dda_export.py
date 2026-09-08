"""Deterministic exports for persisted DdA records."""

from __future__ import annotations

import csv
import io
import json

from docx import Document
from odf.opendocument import OpenDocumentSpreadsheet  # type: ignore[import-untyped]
from odf.table import Table, TableCell, TableRow  # type: ignore[import-untyped]
from odf.text import P  # type: ignore[import-untyped]
from openpyxl import Workbook  # type: ignore[import-untyped]

from ensmcp.domain.dda import DDARecord, ExportedDocument, ExportFormat, dda_to_dict


def _row(record: DDARecord, index: int) -> dict[str, object]:
    measure = record.measures[index]
    return {
        "measure_code": measure.measure_code,
        "title": measure.title,
        "applicable": measure.applicable,
        "required_level": measure.required_level.value if measure.required_level else "",
        "required_reinforcements": "; ".join(
            f"{item.code}{' (alternativo)' if item.alternative else ''}"
            for item in measure.required_reinforcements
        ),
        "decision_basis": json.dumps(measure.decision_basis, ensure_ascii=False),
        "implementation_status": measure.implementation_status.value,
        "owner": measure.owner,
        "justification": measure.justification,
        "evidence_references": "; ".join(measure.evidence_references),
        "exclusion_reason": measure.exclusion_reason,
        "compensatory_measures": "; ".join(measure.compensatory_measures),
        "surveillance_measures": "; ".join(measure.surveillance_measures),
        "target_date": measure.target_date.isoformat() if measure.target_date else "",
        "review_date": measure.review_date.isoformat() if measure.review_date else "",
    }


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _xlsx(headers: tuple[str, ...], rows: list[dict[str, object]]) -> bytes:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("DdA")
    sheet.append(headers)
    for row in rows:
        sheet.append([row[header] for header in headers])
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _ods(headers: tuple[str, ...], rows: list[dict[str, object]]) -> bytes:
    document = OpenDocumentSpreadsheet()
    table = Table(name="DdA")
    for values in (headers, *([row[header] for header in headers] for row in rows)):
        table_row = TableRow()
        for value in values:
            cell = TableCell(valuetype="string")
            cell.addElement(P(text=str(value)))
            table_row.addElement(cell)
        table.addElement(table_row)
    document.spreadsheet.addElement(table)
    stream = io.BytesIO()
    document.write(stream)
    return stream.getvalue()


def _docx(record: DDARecord, headers: tuple[str, ...], rows: list[dict[str, object]]) -> bytes:
    document = Document()
    document.add_heading(f"Declaración de Aplicabilidad: {record.system}", level=1)
    document.add_paragraph(f"Ámbito: {record.scope}")
    document.add_paragraph(f"Categoría: {record.category.value}")
    table = document.add_table(rows=1, cols=len(headers))
    for cell, header in zip(table.rows[0].cells, headers, strict=True):
        cell.text = header
    for row in rows:
        cells = table.add_row().cells
        for cell, header in zip(cells, headers, strict=True):
            cell.text = str(row[header])
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def export_dda(record: DDARecord, output_format: ExportFormat) -> ExportedDocument:
    if output_format is ExportFormat.JSON:
        content = json.dumps(dda_to_dict(record), ensure_ascii=False, indent=2) + "\n"
        return ExportedDocument(
            f"{record.record_id}.json", "application/json", content.encode("utf-8")
        )

    rows = [_row(record, index) for index in range(len(record.measures))]
    headers = tuple(rows[0]) if rows else tuple(_row_headers())
    if output_format is ExportFormat.CSV:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return ExportedDocument(
            f"{record.record_id}.csv", "text/csv", stream.getvalue().encode("utf-8")
        )

    if output_format is ExportFormat.XLSX:
        return ExportedDocument(
            f"{record.record_id}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _xlsx(headers, rows),
        )
    if output_format is ExportFormat.ODS:
        return ExportedDocument(
            f"{record.record_id}.ods",
            "application/vnd.oasis.opendocument.spreadsheet",
            _ods(headers, rows),
        )
    if output_format is ExportFormat.DOCX:
        return ExportedDocument(
            f"{record.record_id}.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx(record, headers, rows),
        )

    lines = [
        f"# Declaración de Aplicabilidad: {record.system}",
        "",
        f"- Ámbito: {record.scope}",
        f"- Categoría: {record.category.value}",
        f"- Actualizada: {record.updated_at.isoformat()}",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *[
            "| " + " | ".join(_markdown_cell(row[header]) for header in headers) + " |"
            for row in rows
        ],
        "",
    ]
    return ExportedDocument(
        f"{record.record_id}.md", "text/markdown", "\n".join(lines).encode("utf-8")
    )


def _row_headers() -> tuple[str, ...]:
    return (
        "measure_code",
        "title",
        "applicable",
        "required_level",
        "required_reinforcements",
        "decision_basis",
        "implementation_status",
        "owner",
        "justification",
        "evidence_references",
        "exclusion_reason",
        "compensatory_measures",
        "surveillance_measures",
        "target_date",
        "review_date",
    )
