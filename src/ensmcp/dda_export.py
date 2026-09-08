"""Deterministic exports for persisted DdA records."""

from __future__ import annotations

import csv
import io
import json

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
