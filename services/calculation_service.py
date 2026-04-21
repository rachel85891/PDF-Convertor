from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import re

from core.entities import AttendanceEntry, AttendanceReport, ReportTotals

_TIME_FORMAT = "%H:%M"
_TIME_PATTERN = re.compile(r"^\d{1,2}:\d{2}$")
_DECIMAL_PATTERN = re.compile(r"^-?\d+(?:[.,]\d+)?$")


class CalculationService:
    """Recalculate daily and monthly attendance totals after variations."""

    DAILY_REGULAR_HOURS: Decimal = Decimal("8.6")
    DAILY_OT_125_CAP: Decimal = Decimal("2.0")
    OT_125_MULTIPLIER: Decimal = Decimal("1.25")
    OT_150_MULTIPLIER: Decimal = Decimal("1.50")

    def recalculate(
        self,
        report: AttendanceReport,
        hourly_rate: Decimal | None = None,
    ) -> AttendanceReport:
        recalculated_report = deepcopy(report)

        recalculated_entries: list[AttendanceEntry] = []
        total_hours = Decimal("0")
        total_pay = Decimal("0")
        total_days = 0

        effective_rate = self._resolve_hourly_rate(recalculated_report, hourly_rate)

        for entry in recalculated_report.entries:
            recalculated_entry, regular_hours, overtime_125, overtime_150 = self._recalculate_entry(entry)
            recalculated_entries.append(recalculated_entry)

            day_total = regular_hours + overtime_125 + overtime_150
            total_hours += day_total
            if day_total > Decimal("0"):
                total_days += 1
            total_pay += (
                regular_hours * effective_rate
                + overtime_125 * effective_rate * self.OT_125_MULTIPLIER
                + overtime_150 * effective_rate * self.OT_150_MULTIPLIER
            )

        recalculated_report.entries = recalculated_entries
        recalculated_report.totals = ReportTotals(
            total_hours=self._q2(total_hours),
            total_pay=self._q2(total_pay),
            total_days=total_days,
        )
        return recalculated_report

    def _recalculate_entry(self, entry: AttendanceEntry) -> tuple[AttendanceEntry, Decimal, Decimal, Decimal]:
        worked_hours = self._calculate_worked_hours(entry.entry_time, entry.exit_time, entry.break_duration)

        overtime_125 = Decimal("0")
        overtime_150 = Decimal("0")
        if worked_hours > self.DAILY_REGULAR_HOURS:
            overtime_pool = worked_hours - self.DAILY_REGULAR_HOURS
            overtime_125 = min(overtime_pool, self.DAILY_OT_125_CAP)
            overtime_150 = max(overtime_pool - self.DAILY_OT_125_CAP, Decimal("0"))

        worked_q2 = self._q2(worked_hours)
        overtime_125_q2 = self._q2(overtime_125)
        overtime_150_q2 = self._q2(overtime_150)
        regular_hours_q2 = max(worked_q2 - overtime_125_q2 - overtime_150_q2, Decimal("0"))

        updated_entry = replace(
            entry,
            total_hours=self._format_decimal(worked_q2),
            overtime_125=self._format_decimal(overtime_125_q2),
            overtime_150=self._format_decimal(overtime_150_q2),
        )
        return updated_entry, regular_hours_q2, overtime_125_q2, overtime_150_q2

    def _calculate_worked_hours(self, entry_time: str, exit_time: str, break_duration: str) -> Decimal:
        if not self._is_time(entry_time) or not self._is_time(exit_time):
            return Decimal("0")

        entry_dt = datetime.strptime(entry_time, _TIME_FORMAT)
        exit_dt = datetime.strptime(exit_time, _TIME_FORMAT)
        if exit_dt <= entry_dt:
            exit_dt = exit_dt + timedelta(days=1)

        break_minutes = self._parse_break_minutes(break_duration)
        total_minutes = int((exit_dt - entry_dt).total_seconds() // 60) - break_minutes
        if total_minutes < 0:
            total_minutes = 0

        return Decimal(total_minutes) / Decimal(60)

    def _resolve_hourly_rate(self, report: AttendanceReport, provided: Decimal | None) -> Decimal:
        if provided is not None:
            return max(provided, Decimal("0"))

        if report.totals.total_hours > Decimal("0") and report.totals.total_pay > Decimal("0"):
            return report.totals.total_pay / report.totals.total_hours

        return Decimal("0")

    @staticmethod
    def _parse_break_minutes(value: str) -> int:
        cleaned = value.strip()
        if not cleaned:
            return 0

        if _TIME_PATTERN.match(cleaned):
            hours_str, minutes_str = cleaned.split(":")
            return int(hours_str) * 60 + int(minutes_str)

        if _DECIMAL_PATTERN.match(cleaned):
            hours = Decimal(cleaned.replace(",", "."))
            minutes = (hours * Decimal("60")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            return int(minutes)

        return 0

    @staticmethod
    def _is_time(value: str) -> bool:
        return bool(_TIME_PATTERN.match(value.strip()))

    @staticmethod
    def _q2(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        return f"{value:.2f}"
