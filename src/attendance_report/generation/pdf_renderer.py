from __future__ import annotations

import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Callable

import pdfplumber
import pymupdf as fitz

from attendance_report.domain.interfaces import BaseGenerator
from attendance_report.domain.models import AttendanceEntry, AttendanceReport

_FONT_CANDIDATES = (
    Path(__file__).resolve().parent.parent.parent.parent / "assets" / "fonts" / "NotoSansHebrew-Regular.ttf",
    Path(__file__).resolve().parent.parent.parent.parent / "assets" / "fonts" / "Rubik-Regular.ttf",
    Path(__file__).resolve().parent.parent.parent.parent / "assets" / "fonts" / "Assistant-Regular.ttf",
)


def _discover_hebrew_font() -> str | None:
    for candidate in _FONT_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return None


def _q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _regular_hours(entry: AttendanceEntry) -> Decimal:
    total = _to_decimal(entry.total_hours)
    ot125 = _to_decimal(entry.overtime_125)
    ot150 = _to_decimal(entry.overtime_150)
    return max(total - ot125 - ot150, Decimal("0"))


def _to_decimal(value: str | None) -> Decimal:
    cleaned = (value or "").strip().replace(",", ".")
    if not cleaned:
        return Decimal("0")
    try:
        return Decimal(cleaned)
    except Exception:
        return Decimal("0")


def _row_cell_strings_type_a(entry: AttendanceEntry) -> list[str]:
    reg = _regular_hours(entry)
    shabbat = "שבת" in (entry.day or "")
    col10 = entry.overtime_150 if shabbat else "0.00"
    return [
        entry.date,
        entry.day,
        entry.location or "",
        entry.entry_time,
        entry.exit_time,
        entry.break_duration or "",
        entry.total_hours or "",
        f"{_q2(reg):.2f}",
        entry.overtime_125 or "0.00",
        entry.overtime_150 or "0.00",
        col10,
    ]


def _row_cell_strings_type_b_card(entry: AttendanceEntry) -> list[str]:
    """Type B employee-card layout: 6 columns (see template type_b.html.j2)."""
    return [
        entry.date,
        entry.day,
        entry.entry_time,
        entry.exit_time,
        entry.total_hours,
        entry.comments or "",
    ]


def _row_cell_strings_type_b_legacy(entry: AttendanceEntry) -> list[str]:
    """Older Type B PDFs with 10 columns (OT breakdown)."""
    reg = _regular_hours(entry)
    return [
        entry.date,
        entry.day,
        entry.location or "",
        entry.entry_time,
        entry.exit_time,
        entry.break_duration or "",
        entry.total_hours or "",
        f"{_q2(reg):.2f}",
        entry.overtime_125 or "0.00",
        entry.overtime_150 or "0.00",
    ]


def _row_cell_strings_type_b(entry: AttendanceEntry) -> list[str]:
    """Type B detailed report: same 10 columns as legacy overlay / HTML template."""
    return _row_cell_strings_type_b_legacy(entry)


def _norm_hdr(cell: str) -> str:
    return re.sub(r"[^0-9a-zא-ת]+", "", (cell or "").lower())


def _type_b_cell_values_from_headers(entry: AttendanceEntry, header_texts: list[str]) -> list[str]:
    """Map entry fields to PDF column order using header row text (handles RTL column order)."""
    out: list[str] = []
    for raw in header_texts:
        h = _norm_hdr(str(raw or ""))
        if "הערות" in h:
            out.append(entry.comments or "")
        elif "תאריך" in h:
            out.append(entry.date)
        elif "יום" in h:
            out.append(entry.day or "")
        elif "כניסה" in h:
            out.append(entry.entry_time or "")
        elif "יציאה" in h:
            out.append(entry.exit_time or "")
        elif "שעות" in h or "סהכ" in h:
            out.append(entry.total_hours or "")
        else:
            out.append("")
    return out


def _snapshot_table(table: object, min_rows: int = 2) -> list[dict[str, object]]:
    rows_attr = getattr(table, "rows", None) or []
    if len(rows_attr) < min_rows:
        raise ValueError("No data table found on page.")

    text_rows = getattr(table, "extract", lambda: None)() or []

    rows: list[dict[str, object]] = []
    for idx, row in enumerate(rows_attr):
        text = text_rows[idx] if idx < len(text_rows) else None
        rows.append(
            {
                "bbox": tuple(row.bbox),
                "cells": [tuple(c) for c in row.cells],
                "text": text,
            }
        )
    return rows


def _pick_summary_table_type_b(tables: list[object], data_table: object) -> object | None:
    """Type B often has a small summary block above the main attendance grid."""
    if len(tables) < 2:
        return None

    data_rows = len(getattr(data_table, "rows", []) or [])
    others = [t for t in tables if t is not data_table]

    scored: list[tuple[float, object]] = []
    for t in others:
        if len(getattr(t, "rows", []) or []) < 2:
            continue
        ex = getattr(t, "extract", lambda: None)() or []
        flat = " ".join(" ".join(str(c or "") for c in (r or [])) for r in ex)
        if any(
            marker in flat
            for marker in (
                "ימים",
                "ימי עבודה",
                'סה"כ',
                "סהכ",
                "100%",
                "125%",
                "150%",
                "בונוס",
                "נסיעות",
                "מחיר לשעה",
                "שעות חודשיות",
                "שם העובד",
                "לתשלום",
            )
        ):
            bbox = getattr(t, "bbox", (0.0, 0.0, 0.0, 0.0))
            scored.append((float(bbox[1]), t))

    if scored:
        return min(scored, key=lambda x: x[0])[1]

    small = [t for t in others if len(getattr(t, "rows", []) or []) < data_rows]
    if not small:
        return None
    return min(small, key=lambda t: float(getattr(t, "bbox", (0.0, 0.0, 0.0, 0.0))[1]))


def _rollup_monthly(report: AttendanceReport) -> dict[str, Decimal]:
    monthly_ot_125 = Decimal("0")
    monthly_ot_150 = Decimal("0")
    for entry in report.entries:
        monthly_ot_125 += _to_decimal(entry.overtime_125)
        monthly_ot_150 += _to_decimal(entry.overtime_150)
    monthly_regular = max(report.totals.total_hours - monthly_ot_125 - monthly_ot_150, Decimal("0"))
    return {
        "monthly_ot_125": monthly_ot_125,
        "monthly_ot_150": monthly_ot_150,
        "monthly_regular_hours": monthly_regular,
    }


def _classify_summary_label(label: str) -> str | None:
    """Map summary row label text to a field key (employee-card layout + legacy OT layout)."""
    cleaned = label.strip()
    if "שם" in cleaned and "עובד" in cleaned:
        return "employee_name"
    if "ימי" in cleaned and "עבודה" in cleaned:
        return "work_days"
    if "שעות" in cleaned and "חודש" in cleaned and "מחיר" not in cleaned:
        return "month_hours"
    if "מחיר" in cleaned and "שעה" in cleaned:
        return "hourly_rate"
    if "לתשלום" in cleaned or ('סה"כ' in cleaned and "תשלום" in cleaned):
        return "total_pay"
    if "ימים" in cleaned:
        return "days"
    if "בונוס" in cleaned:
        return "bonus"
    if "נסיעות" in cleaned:
        return "travel"
    if "100%" in cleaned or ("שעות" in cleaned and "100" in cleaned):
        return "regular"
    if "125%" in cleaned:
        return "ot125"
    if "150%" in cleaned:
        return "ot150"
    if 'סה"כ' in cleaned or "סהכ" in cleaned:
        return "total_hours"
    return None


def _format_summary_value(key: str, report: AttendanceReport, rollups: dict[str, Decimal]) -> str:
    if key == "employee_name":
        return report.employee_metadata.employee_name or ""
    if key == "work_days":
        return str(report.totals.total_days)
    if key == "month_hours":
        return f"{report.totals.total_hours:.2f}"
    if key == "hourly_rate":
        if report.totals.total_hours > Decimal("0") and report.totals.total_pay > Decimal("0"):
            rate = report.totals.total_pay / report.totals.total_hours
            return f"₪ {float(rate):.2f}"
        return "₪ 0.00"
    if key == "total_pay":
        return f"₪ {float(report.totals.total_pay):.2f}"
    if key == "days":
        return str(report.totals.total_days)
    if key == "total_hours":
        return f"{report.totals.total_hours:.2f}"
    if key == "regular":
        return f"{rollups['monthly_regular_hours']:.2f}"
    if key == "ot125":
        return f"{rollups['monthly_ot_125']:.2f}"
    if key == "ot150":
        return f"{rollups['monthly_ot_150']:.2f}"
    if key == "bonus":
        return "0"
    if key == "travel":
        return "0"
    return ""


def _summary_value_cell_index(t0: str, t1: str) -> int:
    """Pick which of two cells holds the value (label may be left or right depending on PDF)."""
    k0 = _classify_summary_label(t0)
    k1 = _classify_summary_label(t1)
    if k0 is not None and k1 is None:
        return 1
    if k1 is not None and k0 is None:
        return 0
    d0 = bool(re.search(r"\d", t0))
    d1 = bool(re.search(r"\d", t1))
    if d0 and not d1:
        return 0
    if d1 and not d0:
        return 1
    return 1


def _overlay_summary_type_b(
    page_fitz: fitz.Page,
    summary_rows: list[dict[str, object]],
    report: AttendanceReport,
    fontfile: str | None,
) -> None:
    rollups = _rollup_monthly(report)
    for row in summary_rows:
        text = row.get("text")
        if not isinstance(text, list) or len(text) < 2:
            continue
        t0, t1 = str(text[0] or ""), str(text[1] or "")
        key = _classify_summary_label(t0) or _classify_summary_label(t1)
        if key is None:
            continue
        cells = row.get("cells")
        if not isinstance(cells, list) or len(cells) < 2:
            continue
        vidx = _summary_value_cell_index(t0, t1)
        value_cell = cells[vidx]
        rect = fitz.Rect(value_cell) + (0.5, 0.5, -0.5, -0.5)
        value = _format_summary_value(key, report, rollups)
        _fill_cell(page_fitz, rect, value, fontfile)


def _merge_row_rect(cells: list[tuple[float, float, float, float]]) -> fitz.Rect:
    x0 = min(c[0] for c in cells)
    y0 = min(c[1] for c in cells)
    x1 = max(c[2] for c in cells)
    y1 = max(c[3] for c in cells)
    return fitz.Rect(x0, y0, x1, y1)


def _overlay_table_layout(
    page_fitz: fitz.Page,
    table_rows: list[dict[str, object]],
    entries: list[AttendanceEntry],
    row_values: Callable[[AttendanceEntry], list[str]],
    expected_cols: int,
    fontfile: str | None,
) -> None:
    if len(table_rows) < 2:
        raise ValueError("Table has no data rows.")

    header_cells = table_rows[0]["cells"]
    if not isinstance(header_cells, list) or len(header_cells) != expected_cols:
        raise ValueError(f"Unexpected column count (expected {expected_cols}).")

    data_rows = table_rows[1:]
    if not data_rows:
        raise ValueError("Table has no data rows.")

    first_data = data_rows[0]
    first_cells = first_data["cells"]
    if not isinstance(first_cells, list) or len(first_cells) != expected_cols:
        raise ValueError("Header/data column mismatch.")

    first_bbox = first_data["bbox"]
    last_bbox = data_rows[-1]["bbox"]
    if not isinstance(first_bbox, tuple) or not isinstance(last_bbox, tuple):
        raise ValueError("Invalid row geometry.")
    region_top = float(first_bbox[1])
    region_bottom = float(last_bbox[3])
    region_height = max(region_bottom - region_top, 0.0)

    n_out = len(entries)
    n_tpl = len(data_rows)

    # Safety: if parsing yielded no rows, keep original source table content
    # instead of blanking all template rows.
    if n_out == 0:
        return

    if n_out <= n_tpl:
        for idx in range(n_tpl):
            row_geom = data_rows[idx]
            cells = row_geom["cells"]
            if not isinstance(cells, list):
                raise ValueError("Invalid row cells.")
            cell_tuples = [tuple(c) for c in cells]  # type: ignore[misc]
            row_rect = _merge_row_rect(cell_tuples)
            if idx < n_out:
                values = row_values(entries[idx])
                if len(values) != expected_cols:
                    raise ValueError("Row value count mismatch.")
                for col_idx, cell_box in enumerate(cell_tuples):
                    rect = fitz.Rect(cell_box)
                    rect = rect + (0.5, 0.5, -0.5, -0.5)
                    text = values[col_idx] if col_idx < len(values) else ""
                    _fill_cell(page_fitz, rect, text, fontfile)
            else:
                page_fitz.draw_rect(row_rect, color=(1, 1, 1), fill=(1, 1, 1), width=0, overlay=True)
    else:
        if region_height <= 0:
            raise ValueError("Invalid table region height.")
        col_boxes = [tuple(c) for c in first_cells]  # type: ignore[misc]
        row_h = region_height / float(n_out)
        for idx in range(n_out):
            y0 = region_top + idx * row_h
            y1 = region_top + (idx + 1) * row_h
            values = row_values(entries[idx])
            if len(values) != expected_cols:
                raise ValueError("Row value count mismatch.")
            for col_idx, cell_box in enumerate(col_boxes):
                x0, _yt, x1, _yb = cell_box
                rect = fitz.Rect(x0, y0, x1, y1)
                rect = rect + (0.5, 0.5, -0.5, -0.5)
                text = values[col_idx] if col_idx < len(values) else ""
                _fill_cell(page_fitz, rect, text, fontfile)


def _fill_cell(page: fitz.Page, rect: fitz.Rect, text: str, fontfile: str | None) -> None:
    fontsize = max(min(rect.height * 0.62, 10.0), 6.0)
    content = text or ""
    for scale in (1.0, 0.85, 0.72):
        fs = max(fontsize * scale, 5.0)
        if fontfile:
            overflow = page.insert_textbox(
                rect,
                content,
                fontfile=fontfile,
                fontsize=fs,
                align=fitz.TEXT_ALIGN_CENTER,
                color=(0, 0, 0),
                fill=(1, 1, 1),
            )
        else:
            overflow = page.insert_textbox(
                rect,
                content,
                fontsize=fs,
                align=fitz.TEXT_ALIGN_CENTER,
                color=(0, 0, 0),
                fill=(1, 1, 1),
            )
        if overflow >= 0 or fs <= 5.05:
            break


def overlay_report_on_pdf(source_pdf: str, report: AttendanceReport, report_type: str, output_path: str) -> None:
    fontfile = _discover_hebrew_font()
    if report_type == "type_a":
        row_fn: Callable[[AttendanceEntry], list[str]] = _row_cell_strings_type_a
        expected_cols = 11
    elif report_type == "type_b":
        # Type B exists in two layouts:
        # 1) employee-card grid (6 cols), 2) legacy/detailed OT grid (10 cols).
        # We select the row mapper only after reading the source table headers.
        row_fn = _row_cell_strings_type_b
        expected_cols = 10
    else:
        raise ValueError(f"Unsupported report type for overlay: {report_type}")

    with pdfplumber.open(source_pdf) as pdf:
        if len(pdf.pages) < 1:
            raise ValueError("PDF has no pages.")
        page0 = pdf.pages[0]
        tables = page0.find_tables()
        if not tables:
            raise ValueError("No tables found on page.")
        data_table = max(tables, key=lambda t: len(getattr(t, "rows", []) or []))
        data_rows = _snapshot_table(data_table)
        if report_type == "type_b":
            header = data_rows[0].get("text")
            header_texts = [str(v or "") for v in header] if isinstance(header, list) else []
            header_cols = len(header_texts)
            if header_cols == 6:
                expected_cols = 6
                row_fn = lambda e: _type_b_cell_values_from_headers(e, header_texts)
            elif header_cols == 10:
                expected_cols = 10
                row_fn = _row_cell_strings_type_b_legacy
            else:
                raise ValueError(
                    f"Unsupported Type B table layout: expected 6 or 10 columns, got {header_cols}."
                )

        summary_rows: list[dict[str, object]] | None = None
        if report_type == "type_b":
            summary_table = _pick_summary_table_type_b(tables, data_table)
            if summary_table is not None:
                try:
                    summary_rows = _snapshot_table(summary_table)
                except ValueError:
                    summary_rows = None

    doc = fitz.open(source_pdf)
    try:
        if report_type == "type_b" and summary_rows:
            _overlay_summary_type_b(doc[0], summary_rows, report, fontfile)
        _overlay_table_layout(
            doc[0],
            data_rows,
            report.entries,
            row_fn,
            expected_cols,
            fontfile,
        )
        doc.save(output_path)
    finally:
        doc.close()


class SameLayoutPdfGenerator(BaseGenerator):
    """Prefer preserving the input PDF layout; fall back to Jinja HTML if overlay fails."""

    def __init__(self, report_type: str) -> None:
        self.report_type = report_type

    def generate(self, report: AttendanceReport, output_path: str) -> None:
        source = (report.source_pdf_path or "").strip()
        if source and Path(source).is_file():
            try:
                overlay_report_on_pdf(source, report, self.report_type, output_path)
                return
            except Exception as exc:
                print(f"Same-layout PDF overlay failed; using HTML template. ({exc})", file=sys.stderr)
        from attendance_report.generation.html_renderer import TypeAGenerator, TypeBGenerator

        fallback: BaseGenerator = TypeAGenerator() if self.report_type == "type_a" else TypeBGenerator()
        fallback.generate(report, output_path)
