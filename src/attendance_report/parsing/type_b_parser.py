from __future__ import annotations

import re

from attendance_report.domain.models import AttendanceRow, EmployeeMetadata
from attendance_report.parsing.base_parser import TemplateReportParser


class TypeBParser(TemplateReportParser):
    HEADER_MARKERS = ("תאריך", "יום", "כניסה", "יציאה", "הערות")
    HEADER_HINTS: dict[str, tuple[str, ...]] = {
        "date": ("תאריך", "date"),
        "day": ("יום בשבוע", "יום", "day"),
        "entry_time": ("שעת כניסה", "כניסה", "entry"),
        "exit_time": ("שעת יציאה", "יציאה", "exit"),
        "total_hours": ('סה"כ שעות', "סהכ שעות", 'סה"כ', "סהכ", "total"),
        "comments": ("הערות", "remarks"),
        "break_duration": ("הפסקה", "break"),
        "location": ('מקום ע"נ', "מקום", "location"),
    }

    def __init__(self) -> None:
        self._column_map: dict[str, int] = {}

    def _is_header_line(self, row: list[str]) -> bool:
        row_text = " ".join(row)
        return sum(1 for marker in self.HEADER_MARKERS if marker in row_text) >= 3

    def _on_header_line(self, row: list[str]) -> None:
        self._column_map = self._build_column_map(row, self.HEADER_HINTS)

    def _parse_summary(self, preview_text: str, full_text: str) -> EmployeeMetadata:
        report_period_match = re.search(r"\b\d{1,2}[/-]\d{4}\b", preview_text)
        employee_id_match = re.search(r"(?:מס['\s]*עובד|ת\.ז)\D*(\d+)", preview_text)
        employee_name_match = re.search(r"(?:שם(?:\s*עובד)?|עובד)\s*[:\-]?\s*([^\n|]{2,40})", preview_text)
        period = report_period_match.group(0) if report_period_match else ""
        if not period:
            card_m = re.search(r"כרטיס\s*עובד\s*לחודש[:\s]*([^\n\r]+)", full_text)
            if card_m:
                period = card_m.group(1).strip()
        return EmployeeMetadata(
            employee_name=employee_name_match.group(1).strip() if employee_name_match else "",
            employee_id=employee_id_match.group(1) if employee_id_match else "",
            company_name="",
            report_period=period,
            source_language="he",
        )

    def _parse_row(self, row: list[str]) -> AttendanceRow | None:
        row_text = " ".join(row)
        if not self._is_data_row(row_text):
            return None

        date_m = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", row_text)
        date = self._value_from_row(row, self._column_map, "date") or (date_m.group(0) if date_m else "")
        if not date:
            return None

        inferred_entry, inferred_exit, inferred_break = self._infer_times_from_row_text(row_text)
        return AttendanceRow(
            date=date,
            day=self._value_from_row(row, self._column_map, "day"),
            entry_time=self._value_from_row(row, self._column_map, "entry_time") or inferred_entry,
            exit_time=self._value_from_row(row, self._column_map, "exit_time") or inferred_exit,
            total_hours=self._value_from_row(row, self._column_map, "total_hours") or self._infer_total_hours(row_text) or "0.00",
            comments=self._value_from_row(row, self._column_map, "comments"),
            break_duration=self._value_from_row(row, self._column_map, "break_duration") or inferred_break or None,
            overtime_125=None,
            overtime_150=None,
            location=self._value_from_row(row, self._column_map, "location") or None,
        )
