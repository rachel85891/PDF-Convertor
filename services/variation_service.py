from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from random import Random
import re
import secrets
from decimal import Decimal, InvalidOperation
import calendar

from core.entities import AttendanceEntry, AttendanceReport

_TIME_FORMAT = "%H:%M"
_TIME_PATTERN = re.compile(r"^\d{1,2}:\d{2}$")
_PERIOD_PATTERN = re.compile(r"\b\d{1,2}[/-]\d{4}\b")
_DATE_PATTERN = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")


class ReliableVariationService:
    """Apply realistic time variations per attendance row (randomized on each run)."""

    def __init__(
        self,
        min_shift_minutes: int = -15,
        max_shift_minutes: int = 15,
        min_work_minutes: int = 180,
    ) -> None:
        if max_shift_minutes < min_shift_minutes:
            raise ValueError("Invalid variation boundaries.")
        if min_work_minutes <= 0:
            raise ValueError("Minimum work duration must be positive.")
        self.min_shift_minutes = min_shift_minutes
        self.max_shift_minutes = max_shift_minutes
        self.min_work_minutes = min_work_minutes

    def apply_variations(self, report: AttendanceReport) -> AttendanceReport:
        """
        Shift entry/exit times with random margins (default: -15..+15).
        A new random sequence is used on every run so day counts and hours differ each time.
        """
        varied_report = deepcopy(report)
        report_period = self._resolve_report_period(varied_report)
        base_rng = Random(int.from_bytes(secrets.token_bytes(16), "big"))
        if not varied_report.entries:
            varied_report.entries = self._build_synthetic_month_entries(varied_report, report_period, base_rng)

        varied_entries: list[AttendanceEntry] = []
        for entry in varied_report.entries:
            varied_entries.append(
                self._vary_single_entry(
                    entry=entry,
                    rng=base_rng,
                )
            )

        varied_report.entries = varied_entries
        return varied_report

    def _build_synthetic_month_entries(
        self,
        report: AttendanceReport,
        report_period: str,
        rng: Random,
    ) -> list[AttendanceEntry]:
        month, year = self._parse_month_year(report_period)
        _, last_day = calendar.monthrange(year, month)

        weekdays = [
            day for day in range(1, last_day + 1)
            if datetime(year, month, day).weekday() != 5  # 5 == Saturday
        ]
        if not weekdays:
            return []

        min_workdays = max(8, int(len(weekdays) * 0.35))
        max_workdays = min(len(weekdays), int(len(weekdays) * 0.82))
        if max_workdays < min_workdays:
            max_workdays = min_workdays
        chosen_count = rng.randint(min_workdays, max_workdays)
        worked_days = sorted(rng.sample(weekdays, chosen_count))

        sample_location = next((entry.location for entry in report.entries if entry.location.strip()), "")
        entries: list[AttendanceEntry] = []
        for day in worked_days:
            current_date = datetime(year, month, day)
            day_name_he = self._weekday_hebrew_name(current_date.weekday())
            date_text = f"{day:02d}/{month:02d}/{year}"

            start_minutes = rng.randint(6 * 60 + 45, 9 * 60 + 30)
            shift_minutes = rng.randint(8 * 60, 11 * 60)
            break_minutes = rng.choice((30, 45, 60))
            end_minutes = start_minutes + shift_minutes

            entry_time = self._minutes_to_time(start_minutes)
            exit_time = self._minutes_to_time(end_minutes)
            break_duration = self._minutes_to_time(break_minutes)
            work_hours = max(Decimal(shift_minutes - break_minutes) / Decimal(60), Decimal("0"))

            entries.append(
                AttendanceEntry(
                    date=date_text,
                    day=day_name_he,
                    entry_time=entry_time,
                    exit_time=exit_time,
                    break_duration=break_duration,
                    total_hours=f"{work_hours.quantize(Decimal('0.01')):.2f}",
                    overtime_125="0.00",
                    overtime_150="0.00",
                    location=sample_location,
                    comments="",
                )
            )

        return entries

    def _vary_single_entry(
        self,
        entry: AttendanceEntry,
        rng: Random,
    ) -> AttendanceEntry:
        if self._is_rest_day(entry):
            return entry

        if not self._is_time(entry.entry_time) or not self._is_time(entry.exit_time):
            return entry

        entry_shift = rng.randint(self.min_shift_minutes, self.max_shift_minutes)
        exit_shift = rng.randint(self.min_shift_minutes, self.max_shift_minutes)

        entry_dt = datetime.strptime(entry.entry_time, _TIME_FORMAT)
        exit_dt = datetime.strptime(entry.exit_time, _TIME_FORMAT)
        if exit_dt <= entry_dt:
            exit_dt = exit_dt + timedelta(days=1)

        varied_entry_dt = entry_dt + timedelta(minutes=entry_shift)
        varied_exit_dt = exit_dt + timedelta(minutes=exit_shift)

        # Keep the row valid and realistic: at least 3 hours between entry/exit.
        minimum_exit = varied_entry_dt + timedelta(minutes=self.min_work_minutes)
        if varied_exit_dt < minimum_exit:
            varied_exit_dt = minimum_exit

        varied_break = self._vary_break_duration(entry.break_duration, rng)

        return replace(
            entry,
            entry_time=varied_entry_dt.strftime(_TIME_FORMAT),
            exit_time=varied_exit_dt.strftime(_TIME_FORMAT),
            break_duration=varied_break,
        )

    @staticmethod
    def _is_time(value: str) -> bool:
        return bool(_TIME_PATTERN.match(value.strip()))

    def _resolve_report_period(self, report: AttendanceReport) -> str:
        period = report.employee_metadata.report_period.strip()
        period_match = _PERIOD_PATTERN.search(period)
        if period_match:
            return period_match.group(0)

        for entry in report.entries:
            date_text = entry.date.strip()
            if not _DATE_PATTERN.search(date_text):
                continue
            parts = re.split(r"[/-]", date_text)
            if len(parts) != 3:
                continue
            month = parts[1].zfill(2)
            year = parts[2]
            if len(year) == 2:
                year = f"20{year}"
            return f"{month}/{year}"
        today = datetime.now()
        return f"{today.month:02d}/{today.year}"

    @staticmethod
    def _parse_month_year(report_period: str) -> tuple[int, int]:
        parts = re.split(r"[/-]", report_period.strip())
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            month = int(parts[0])
            year = int(parts[1])
            if 1 <= month <= 12 and year > 0:
                return month, year
        today = datetime.now()
        return today.month, today.year

    @staticmethod
    def _weekday_hebrew_name(weekday: int) -> str:
        names = {
            0: "יום שני",
            1: "יום שלישי",
            2: "יום רביעי",
            3: "יום חמישי",
            4: "יום שישי",
            5: "שבת",
            6: "יום ראשון",
        }
        return names.get(weekday, "")

    @staticmethod
    def _minutes_to_time(total_minutes: int) -> str:
        total_minutes = total_minutes % (24 * 60)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        return f"{hours:02d}:{minutes:02d}"

    def _is_rest_day(self, entry: AttendanceEntry) -> bool:
        day_text = entry.day.strip()
        if "שבת" in day_text:
            return True
        return self._parse_decimal(entry.total_hours) == Decimal("0")

    def _vary_break_duration(self, break_duration: str, rng: Random) -> str:
        # Add small natural variance occasionally (about 30% of rows).
        if rng.random() > 0.30:
            return break_duration

        if self._is_time(break_duration):
            base_dt = datetime.strptime(break_duration, _TIME_FORMAT)
            delta = rng.choice((-1, 1)) * rng.randint(1, 5)
            varied = base_dt + timedelta(minutes=delta)
            if varied < datetime.strptime("00:00", _TIME_FORMAT):
                varied = datetime.strptime("00:00", _TIME_FORMAT)
            return varied.strftime(_TIME_FORMAT)

        base_decimal = self._parse_decimal(break_duration)
        if base_decimal is None:
            return break_duration

        delta_minutes = rng.choice((-1, 1)) * rng.randint(1, 5)
        varied_hours = base_decimal + (Decimal(delta_minutes) / Decimal(60))
        if varied_hours < Decimal("0"):
            varied_hours = Decimal("0")
        return f"{varied_hours.quantize(Decimal('0.01')):.2f}"

    @staticmethod
    def _parse_decimal(value: str) -> Decimal | None:
        cleaned = value.strip().replace(",", ".")
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
