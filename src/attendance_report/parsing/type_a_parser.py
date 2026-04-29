from __future__ import annotations

import re

from attendance_report.domain.models import AttendanceRow, EmployeeMetadata
from attendance_report.parsing.base_parser import TemplateReportParser


class TypeAParser(TemplateReportParser):
    HEADER_MARKERS = ("תאריך", "יום", "כניסה", "יציאה", 'סה"כ')
    HEADER_HINTS: dict[str, tuple[str, ...]] = {
        "date": ("תאריך", "date"),
        "day": ("יום", "day"),
        "location": ('מקום ע"נ', "מקום", "location"),
        "entry_time": ("כניסה", "שעת כניסה", "entry"),
        "exit_time": ("יציאה", "שעת יציאה", "exit"),
        "break_duration": ("הפסקה", "break"),
        "total_hours": ('סה"כ', "סהכ", "total"),
        "overtime_125": ("125", "125%"),
        "overtime_150": ("150", "150%"),
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
        company_match = re.search(r"(?:^|\n)\s*([^\n]{2,40}(?:בע\"מ|כח אדם|כוח אדם))", preview_text)
        return EmployeeMetadata(
            employee_name=employee_name_match.group(1).strip() if employee_name_match else "",
            employee_id=employee_id_match.group(1) if employee_id_match else "",
            company_name=company_match.group(1).strip() if company_match else "נ.ע. הנשר",
            report_period=report_period_match.group(0) if report_period_match else "",
            source_language="he",
        )

    def _parse_row(self, row: list[str]) -> AttendanceRow | None:
        row_text = " ".join(row)
        if not self._is_data_row(row_text):
            return None

        date = self._value_from_row(row, self._column_map, "date") or (re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", row_text).group(0) if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", row_text) else "")
        if not date:
            return None

        entry_time, exit_time, inferred_break = self._infer_times_from_row_text(row_text)
        parsed_entry = self._value_from_row(row, self._column_map, "entry_time") or entry_time
        parsed_exit = self._value_from_row(row, self._column_map, "exit_time") or exit_time

        return AttendanceRow(
            date=date,
            day=self._value_from_row(row, self._column_map, "day"),
            location=self._value_from_row(row, self._column_map, "location"),
            entry_time=parsed_entry,
            exit_time=parsed_exit,
            break_duration=self._value_from_row(row, self._column_map, "break_duration") or inferred_break or "0:00",
            total_hours=self._value_from_row(row, self._column_map, "total_hours") or self._infer_total_hours(row_text) or "0.00",
            overtime_125=self._value_from_row(row, self._column_map, "overtime_125") or "0.00",
            overtime_150=self._value_from_row(row, self._column_map, "overtime_150") or "0.00",
            comments="",
        )
