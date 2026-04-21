from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

import pdfplumber

from core.entities import AttendanceEntry, AttendanceReport, EmployeeMetadata, ReportTotals
from core.interfaces import BaseParser

_DATE_PATTERN = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
_TIME_PATTERN = re.compile(r"\b\d{1,2}:\d{2}\b")
_NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)?")


class _ParserUtilities:
    """Reusable helpers for PDF table extraction and value normalization."""

    _TYPE_A_HEADERS: dict[str, tuple[str, ...]] = {
        "date": ("תאריך", "date"),
        "day": ("יום", "day"),
        "location": ('מקום ע"נ', "מקום", "גליליון", "גונן", "location"),
        "entry_time": ("כניסה", "שעת כניסה", "entry"),
        "exit_time": ("יציאה", "שעת יציאה", "exit"),
        "break_duration": ("הפסקה", "break"),
        "total_hours": ('סה"כ', "סהכ", "total"),
        "overtime_125": ("125", "125%"),
        "overtime_150": ("150", "150%"),
    }
    _TYPE_B_HEADERS: dict[str, tuple[str, ...]] = {
        "date": ("תאריך", "date"),
        "day": ("יום", "day"),
        "entry_time": ("כניסה", "שעת כניסה", "entry"),
        "exit_time": ("יציאה", "שעת יציאה", "exit"),
        "total_hours": ('סה"כ', "סהכ", "total"),
    }
    _SUMMARY_MARKERS: tuple[str, ...] = ("סה\"כ", "סהכ", "ימים", "שעות", "לתשלום", "בונוס", "נסיעות")

    @staticmethod
    def _clean_text(value: str | None) -> str:
        if value is None:
            return ""
        normalized = value.replace("\n", " ").replace("\r", " ").strip()
        return "" if normalized == "None" else normalized

    @classmethod
    def _extract_full_text(cls, file_path: str) -> str:
        chunks: list[str] = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = cls._clean_text(page.extract_text())
                if page_text:
                    chunks.append(page_text)
        return "\n".join(chunks)

    @classmethod
    def _extract_preview(cls, file_path: str, max_lines: int = 14) -> str:
        text = cls._extract_full_text(file_path)
        return "\n".join(text.splitlines()[:max_lines])

    @classmethod
    def _extract_table_rows(cls, file_path: str) -> list[list[str]]:
        rows: list[list[str]] = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if not row:
                            continue
                        cleaned = [cls._clean_text(cell) for cell in row]
                        if any(cell for cell in cleaned):
                            rows.append(cleaned)
                if not tables:
                    rows.extend(cls._extract_rows_from_page_text(page))
        return rows

    @classmethod
    def _extract_rows_from_page_text(cls, page: object) -> list[list[str]]:
        extracted_text = getattr(page, "extract_text")() if hasattr(page, "extract_text") else ""
        if not extracted_text:
            words = getattr(page, "extract_words")() if hasattr(page, "extract_words") else []
            extracted_text = " ".join(word.get("text", "") for word in words)

        if not extracted_text or not extracted_text.strip():
            return []

        candidates: list[list[str]] = []
        for raw_line in extracted_text.split("\n"):
            line = cls._clean_text(raw_line)
            if not line:
                continue
            if _DATE_PATTERN.search(line) and _TIME_PATTERN.search(line):
                candidates.append([line])
        return candidates

    @classmethod
    def _find_header_row_index(
        cls,
        rows: list[list[str]],
        header_hints: dict[str, tuple[str, ...]],
    ) -> int:
        for idx, row in enumerate(rows):
            row_text = " ".join(row)
            matched_fields = 0
            for candidates in header_hints.values():
                if any(candidate in row_text for candidate in candidates):
                    matched_fields += 1
            if matched_fields >= 3:
                return idx
        return -1

    @classmethod
    def _build_column_map(
        cls,
        header_row: list[str],
        header_hints: dict[str, tuple[str, ...]],
    ) -> dict[str, int]:
        mapping: dict[str, int] = {}
        for idx, cell in enumerate(header_row):
            for field_name, candidates in header_hints.items():
                if field_name in mapping:
                    continue
                if any(candidate in cell for candidate in candidates):
                    mapping[field_name] = idx
        return mapping

    @staticmethod
    def _value_from_row(row: list[str], col_map: dict[str, int], field_name: str) -> str:
        index = col_map.get(field_name)
        if index is None or index >= len(row):
            return ""
        return row[index].strip()

    @staticmethod
    def _parse_decimal(value: str) -> Decimal | None:
        cleaned = value.replace(",", ".").replace("₪", "").strip()
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None

    @classmethod
    def _extract_decimal_after_any_label(cls, text: str, labels: Iterable[str]) -> Decimal | None:
        for label in labels:
            pattern = re.compile(rf"{re.escape(label)}[^0-9]*(\d+(?:[.,]\d+)?)")
            match = pattern.search(text)
            if not match:
                continue
            parsed = cls._parse_decimal(match.group(1))
            if parsed is not None:
                return parsed
        return None

    @classmethod
    def _is_data_row(cls, row: list[str]) -> bool:
        row_text = " ".join(row)
        if not _DATE_PATTERN.search(row_text):
            return False
        return not any(marker in row_text for marker in cls._SUMMARY_MARKERS)

    @classmethod
    def _extract_entry(cls, row: list[str], col_map: dict[str, int], supports_overtime: bool) -> AttendanceEntry | None:
        if not cls._is_data_row(row):
            return None

        row_text = " ".join(row)
        parsed_date = cls._value_from_row(row, col_map, "date")
        if not parsed_date:
            date_match = _DATE_PATTERN.search(row_text)
            parsed_date = date_match.group(0) if date_match else ""
        if not parsed_date:
            return None

        parsed_entry = cls._value_from_row(row, col_map, "entry_time")
        parsed_exit = cls._value_from_row(row, col_map, "exit_time")
        parsed_break = cls._value_from_row(row, col_map, "break_duration")
        inferred_entry, inferred_exit, inferred_break = cls._infer_times_from_row_text(row_text)
        parsed_entry = parsed_entry or inferred_entry
        parsed_exit = parsed_exit or inferred_exit
        parsed_break = parsed_break or inferred_break

        parsed_total = cls._value_from_row(row, col_map, "total_hours")
        if not parsed_total:
            values = _NUMBER_PATTERN.findall(row_text)
            parsed_total = cls._infer_total_hours(values)

        overtime_125 = cls._value_from_row(row, col_map, "overtime_125") if supports_overtime else "0.00"
        overtime_150 = cls._value_from_row(row, col_map, "overtime_150") if supports_overtime else "0.00"

        return AttendanceEntry(
            date=parsed_date,
            day=cls._value_from_row(row, col_map, "day"),
            entry_time=parsed_entry,
            exit_time=parsed_exit,
            break_duration=parsed_break,
            total_hours=parsed_total,
            overtime_125=overtime_125 or "0.00",
            overtime_150=overtime_150 or "0.00",
            location=cls._value_from_row(row, col_map, "location"),
        )

    @classmethod
    def _infer_times_from_row_text(cls, row_text: str) -> tuple[str, str, str]:
        all_times = _TIME_PATTERN.findall(row_text)
        if not all_times:
            return "", "", ""
        if len(all_times) == 1:
            return all_times[0], "", ""

        minutes_values = [cls._time_to_minutes(value) for value in all_times]
        break_idx: int | None = None
        for idx, minutes in enumerate(minutes_values):
            if minutes <= 120:
                break_idx = idx
                break

        work_times: list[str] = []
        break_duration = ""
        for idx, value in enumerate(all_times):
            if break_idx is not None and idx == break_idx:
                break_duration = value
                continue
            work_times.append(value)

        if len(work_times) >= 2:
            entry = min(work_times, key=cls._time_to_minutes)
            exit_ = max(work_times, key=cls._time_to_minutes)
            return entry, exit_, break_duration

        return all_times[0], all_times[1], break_duration

    @staticmethod
    def _time_to_minutes(value: str) -> int:
        parts = value.split(":")
        return int(parts[0]) * 60 + int(parts[1])

    @classmethod
    def _infer_total_hours(cls, raw_values: list[str]) -> str:
        if not raw_values:
            return ""
        decimal_candidates: list[Decimal] = []
        for token in raw_values:
            parsed = cls._parse_decimal(token)
            if parsed is None:
                continue
            if parsed > Decimal("0") and parsed <= Decimal("24"):
                decimal_candidates.append(parsed)
        if not decimal_candidates:
            return ""
        return f"{max(decimal_candidates):.2f}"

    @classmethod
    def _build_metadata(cls, preview_text: str, default_company_name: str = "") -> EmployeeMetadata:
        report_period_match = re.search(r"\b\d{1,2}[/-]\d{4}\b", preview_text)
        employee_id_match = re.search(r"(?:מס['\s]*עובד|ת\.ז)\D*(\d+)", preview_text)
        employee_name_match = re.search(r"(?:שם(?:\s*עובד)?|עובד)\s*[:\-]?\s*([^\n|]{2,40})", preview_text)
        company_match = re.search(r"(?:^|\n)\s*([^\n]{2,40}(?:בע\"מ|כח אדם|כוח אדם))", preview_text)
        company_name = company_match.group(1).strip() if company_match else default_company_name
        return EmployeeMetadata(
            employee_name=employee_name_match.group(1).strip() if employee_name_match else "",
            employee_id=employee_id_match.group(1) if employee_id_match else "",
            company_name=company_name,
            company_logo_path="",
            department="",
            role="",
            report_period=report_period_match.group(0) if report_period_match else "",
            source_language="he",
        )

    @classmethod
    def _build_totals(cls, report_text: str, entries: list[AttendanceEntry]) -> ReportTotals:
        total_hours = cls._extract_decimal_after_any_label(
            report_text,
            labels=("סה\"כ שעות חודשיות", "סה\"כ שעות", "סהכ שעות", "שעות חודשיות"),
        )
        total_pay = cls._extract_decimal_after_any_label(
            report_text,
            labels=("סה\"כ לתשלום", "סהכ לתשלום", "לתשלום"),
        )

        if total_hours is None:
            summed = Decimal("0")
            for entry in entries:
                parsed = cls._parse_decimal(entry.total_hours)
                if parsed is not None:
                    summed += parsed
            total_hours = summed

        return ReportTotals(
            total_hours=total_hours,
            total_pay=total_pay if total_pay is not None else Decimal("0"),
            total_days=sum(1 for entry in entries if cls._parse_decimal(entry.total_hours) not in (None, Decimal("0"))),
        )


class TypeAParser(BaseParser, _ParserUtilities):
    """Parser for detailed reports with location and overtime columns."""

    def parse(self, file_path: str) -> AttendanceReport:
        normalized_path = str(Path(file_path))
        preview_text = self._extract_preview(normalized_path)
        full_text = self._extract_full_text(normalized_path)
        rows = self._extract_table_rows(normalized_path)

        header_index = self._find_header_row_index(rows, self._TYPE_A_HEADERS)
        header_row = rows[header_index] if header_index >= 0 else []
        col_map = self._build_column_map(header_row, self._TYPE_A_HEADERS) if header_row else {}
        data_rows = rows[header_index + 1 :] if header_index >= 0 else rows

        entries: list[AttendanceEntry] = []
        for row in data_rows:
            entry = self._extract_entry(row, col_map, supports_overtime=True)
            if entry is not None:
                entries.append(entry)

        return AttendanceReport(
            entries=entries,
            employee_metadata=self._build_metadata(preview_text, default_company_name="נ.ע. הנשר"),
            totals=self._build_totals(full_text, entries),
        )


class TypeBParser(BaseParser, _ParserUtilities):
    """Parser for simpler employee-card style attendance reports."""

    def parse(self, file_path: str) -> AttendanceReport:
        normalized_path = str(Path(file_path))
        preview_text = self._extract_preview(normalized_path)
        full_text = self._extract_full_text(normalized_path)
        rows = self._extract_table_rows(normalized_path)

        header_index = self._find_header_row_index(rows, self._TYPE_B_HEADERS)
        header_row = rows[header_index] if header_index >= 0 else []
        col_map = self._build_column_map(header_row, self._TYPE_B_HEADERS) if header_row else {}
        data_rows = rows[header_index + 1 :] if header_index >= 0 else rows

        entries: list[AttendanceEntry] = []
        for row in data_rows:
            entry = self._extract_entry(row, col_map, supports_overtime=False)
            if entry is not None:
                entries.append(entry)

        return AttendanceReport(
            entries=entries,
            employee_metadata=self._build_metadata(preview_text, default_company_name=""),
            totals=self._build_totals(full_text, entries),
        )
