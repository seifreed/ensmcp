"""Portable textual DdA exports."""

import csv
import io
import json
from dataclasses import replace

from docx import Document
from odf.opendocument import load  # type: ignore[import-untyped]
from odf.table import Table, TableCell, TableRow  # type: ignore[import-untyped]
from odf.teletype import extractText  # type: ignore[import-untyped]
from openpyxl import load_workbook  # type: ignore[import-untyped]

from ensmcp.dda_export import export_dda
from ensmcp.domain.dda import ExportFormat
from tests.domain.test_dda import sample_dda
from tests.support import check


def test_json_csv_and_markdown_exports_are_complete_and_escaped() -> None:
    record = sample_dda()
    json_document = export_dda(record, ExportFormat.JSON)
    check(json_document.filename == "portal-2026.json")
    check(json.loads(json_document.content)["record_id"] == record.record_id)

    csv_document = export_dda(record, ExportFormat.CSV)
    rows = list(csv.DictReader(io.StringIO(csv_document.content.decode("utf-8"))))
    check(rows[0]["measure_code"] == "org.1")
    check(rows[0]["decision_basis"] == '{"basis": "category"}')
    check(rows[0]["required_reinforcements"] == "R1")

    escaped = replace(record.measures[0], justification="uno | dos\ntres")
    markdown = export_dda(
        replace(record, measures=(escaped, *record.measures[1:])), ExportFormat.MARKDOWN
    )
    text = markdown.content.decode("utf-8")
    check(markdown.filename == "portal-2026.md")
    check("uno \\| dos tres" in text)


def test_empty_exports_keep_the_tabular_schema() -> None:
    record = replace(sample_dda(), measures=())
    csv_text = export_dda(record, ExportFormat.CSV).content.decode("utf-8")
    markdown = export_dda(record, ExportFormat.MARKDOWN).content.decode("utf-8")
    check(csv_text.startswith("measure_code,title,applicable"))
    check("| measure_code | title | applicable |" in markdown)


def test_xlsx_ods_and_docx_exports_open_with_their_native_readers() -> None:
    record = sample_dda()

    xlsx = export_dda(record, ExportFormat.XLSX)
    workbook = load_workbook(io.BytesIO(xlsx.content), read_only=True)
    values = list(workbook["DdA"].iter_rows(values_only=True))
    workbook.close()
    check(values[0][0] == "measure_code")
    check(values[1][0] == "org.1")

    ods = export_dda(record, ExportFormat.ODS)
    spreadsheet = load(io.BytesIO(ods.content))
    table = spreadsheet.getElementsByType(Table)[0]
    rows = table.getElementsByType(TableRow)
    check(extractText(rows[0].getElementsByType(TableCell)[0]) == "measure_code")
    check(extractText(rows[1].getElementsByType(TableCell)[0]) == "org.1")

    docx = export_dda(record, ExportFormat.DOCX)
    document = Document(io.BytesIO(docx.content))
    check(document.paragraphs[0].text == "Declaración de Aplicabilidad: Portal")
    check(document.tables[0].cell(0, 0).text == "measure_code")
    check(document.tables[0].cell(1, 0).text == "org.1")


def test_spreadsheet_exports_neutralize_formula_cells() -> None:
    record = sample_dda()
    measure = replace(record.measures[0], owner="=1+1")
    record = replace(record, measures=(measure, *record.measures[1:]))

    csv_document = export_dda(record, ExportFormat.CSV)
    csv_row = next(csv.DictReader(io.StringIO(csv_document.content.decode("utf-8"))))
    check(csv_row["owner"] == "'=1+1")

    xlsx_document = export_dda(record, ExportFormat.XLSX)
    workbook = load_workbook(io.BytesIO(xlsx_document.content), read_only=True)
    owner = next(workbook["DdA"].iter_rows(min_row=2))[7]
    workbook.close()
    check(owner.value == "'=1+1")
    check(owner.data_type == "s")
