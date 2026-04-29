from __future__ import annotations

from abc import ABC, abstractmethod
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

import pdfplumber

from attendance_report.domain.interfaces import BaseParser
from attendance_report.domain.models import AttendanceReport, EmployeeMetadata, ReportTotals

_DATE_PATTERN = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
_TIME_PATTERN = re.compile(r"\b\d{1,2}:\d{2}\b")
_NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)?")


class TemplateReportParser(BaseParser, ABC):
    """Template Method: fixed parse flow with type-specific hooks."""

    SUMMARY_LABELS_HOURS: tuple[str, ...] = ("סה\"כ שעות חודשיות", "סה\"כ שעות", "סהכ שעות", "שעות חודשיות")
    SUMMARY_LABELS_PAY: tuple[str, ...] = ("סה\"כ לתשלום", "סהכ לתשלום", "לתשלום")

    def parse(self, file_path: str) -> AttendanceReport:
        normalized_path = str(Path(file_path))
        preview_text = self._extract_preview(normalized_path)
        full_text = self._extract_full_text(normalized_path)
        rows = self._extract_table_rows(normalized_path)

        metadata = self._parse_summary(preview_text, full_text)
        entries = self._parse_rows(rows)
        totals = self._build_totals(full_text, entries)
        return AttendanceReport(entries=entries, employee_metadata=metadata, totals=totals)

    def _parse_rows(self, rows: list[list[str]]) -> list:
        start = 0
        for idx, row in enumerate(rows):
            if self._is_header_line(row):
                self._on_header_line(row)
                start = idx + 1
                break

        parsed: list = []
        for row in rows[start:]:
            item = self._parse_row(row)
            if item is not None:
                parsed.append(item)
        return parsed

    @abstractmethod
    def _parse_summary(self, preview_text: str, full_text: str) -> EmployeeMetadata:
        ...

    @abstractmethod
    def _parse_row(self, row: list[str]):
        ...

    @abstractmethod
    def _is_header_line(self, row: list[str]) -> bool:
        ...

    def _on_header_line(self, row: list[str]) -> None:
        """Optional hook for subclasses to cache header-derived metadata like column maps."""
        return None

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
        return "\n".join(cls._extract_full_text(file_path).splitlines()[:max_lines])

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
        out: list[list[str]] = []
        for raw_line in extracted_text.split("\n"):
            line = cls._clean_text(raw_line)
            if line and _DATE_PATTERN.search(line) and _TIME_PATTERN.search(line):
                out.append([line])
        return out

    @staticmethod
    def _parse_decimal(value: str | None) -> Decimal | None:
        cleaned = (value or "").replace(",", ".").replace("₪", "").strip()
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None

    @classmethod
    def _extract_decimal_after_any_label(cls, text: str, labels: Iterable[str]) -> Decimal | None:
        for label in labels:
            match = re.search(rf"{re.escape(label)}[^0-9]*(\d+(?:[.,]\d+)?)", text)
            if not match:
                continue
            parsed = cls._parse_decimal(match.group(1))
            if parsed is not None:
                return parsed
        return None

    @classmethod
    def _build_totals(cls, report_text: str, entries: list) -> ReportTotals:
        total_hours = cls._extract_decimal_after_any_label(report_text, cls.SUMMARY_LABELS_HOURS)
        total_pay = cls._extract_decimal_after_any_label(report_text, cls.SUMMARY_LABELS_PAY)

        if total_hours is None:
            summed = Decimal("0")
            for entry in entries:
                parsed = cls._parse_decimal(entry.total_hours)
                if parsed is not None:
                    summed += parsed
            total_hours = summed

        total_days = sum(1 for entry in entries if cls._parse_decimal(entry.total_hours) not in (None, Decimal("0")))
        return ReportTotals(total_hours=total_hours, total_pay=total_pay if total_pay is not None else Decimal("0"), total_days=total_days)

    @staticmethod
    def _is_data_row(row_text: str) -> bool:
        if not _DATE_PATTERN.search(row_text):
            return False
        if any(marker in row_text for marker in ("סה\"כ", "סהכ", "ימים", "שעות", "לתשלום", "בונוס", "נסיעות")):
            return False
        if any(marker in row_text for marker in ("ימי עבודה", "מחיר לשעה", "שם העובד", "כרטיס עובד", "סה\"כ ימי", "סהכ ימי")):
            return False
        return True

    @staticmethod
    def _infer_times_from_row_text(row_text: str) -> tuple[str, str, str]:
        all_times = _TIME_PATTERN.findall(row_text)
        if not all_times:
            return "", "", ""
        if len(all_times) == 1:
            return all_times[0], "", ""
        mins = [int(v.split(":")[0]) * 60 + int(v.split(":")[1]) for v in all_times]
        break_idx = next((i for i, m in enumerate(mins) if m <= 120), None)
        work_times: list[str] = []
        break_duration = ""
        for i, value in enumerate(all_times):
            if break_idx is not None and i == break_idx:
                break_duration = value
            else:
                work_times.append(value)
        if len(work_times) >= 2:
            key = lambda x: int(x.split(":")[0]) * 60 + int(x.split(":")[1])
            return min(work_times, key=key), max(work_times, key=key), break_duration
        return all_times[0], all_times[1], break_duration

    @staticmethod
    def _infer_total_hours(row_text: str) -> str:
        values = _NUMBER_PATTERN.findall(row_text)
        decimal_candidates: list[Decimal] = []
        for token in values:
            parsed = TemplateReportParser._parse_decimal(token)
            if parsed is not None and parsed > Decimal("0") and parsed <= Decimal("24"):
                decimal_candidates.append(parsed)
        if not decimal_candidates:
            return ""
        return f"{max(decimal_candidates):.2f}"

    @staticmethod
    def _build_column_map(header_row: list[str], header_hints: dict[str, tuple[str, ...]]) -> dict[str, int]:
        mapping: dict[str, int] = {}
        for idx, cell in enumerate(header_row):
            cell_text = (cell or "").strip()
            for field_name, candidates in header_hints.items():
                if field_name in mapping:
                    continue
                if any(candidate in cell_text for candidate in candidates):
                    mapping[field_name] = idx
        return mapping

    @staticmethod
    def _value_from_row(row: list[str], col_map: dict[str, int], field_name: str) -> str:
        idx = col_map.get(field_name)
        if idx is None or idx >= len(row):
            return ""
        return (row[idx] or "").strip()
