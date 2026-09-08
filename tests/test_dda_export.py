"""Portable textual DdA exports."""

import csv
import io
import json
from dataclasses import replace

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
