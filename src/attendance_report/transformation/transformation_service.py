from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from random import Random
import calendar
import secrets
import re

from attendance_report.domain.exceptions import TransformationError
from attendance_report.domain.models import AttendanceReport, AttendanceRow

_TIME_PATTERN = re.compile(r"^\d{1,2}:\d{2}$")


class BaseTransformationStrategy(ABC):
    @abstractmethod
    def transform_row(self, row: AttendanceRow, rng: Random) -> AttendanceRow:
        ...


class TypeATransformationStrategy(BaseTransformationStrategy):
    def __init__(self, min_shift_minutes: int = -15, max_shift_minutes: int = 15) -> None:
        self.min_shift_minutes = min_shift_minutes
        self.max_shift_minutes = max_shift_minutes

    def transform_row(self, row: AttendanceRow, rng: Random) -> AttendanceRow:
        return _shift_row_times(row, rng, self.min_shift_minutes, self.max_shift_minutes)


class TypeBTransformationStrategy(BaseTransformationStrategy):
    def __init__(self, min_shift_minutes: int = -10, max_shift_minutes: int = 10) -> None:
        self.min_shift_minutes = min_shift_minutes
        self.max_shift_minutes = max_shift_minutes

    def transform_row(self, row: AttendanceRow, rng: Random) -> AttendanceRow:
        return _shift_row_times(row, rng, self.min_shift_minutes, self.max_shift_minutes)


class ValidatingStrategyDecorator(BaseTransformationStrategy):
    def __init__(self, inner: BaseTransformationStrategy) -> None:
        self.inner = inner

    def transform_row(self, row: AttendanceRow, rng: Random) -> AttendanceRow:
        transformed = self.inner.transform_row(row, rng)
        self._validate(transformed)
        return transformed

    def _validate(self, row: AttendanceRow) -> None:
        if not _is_time(row.entry_time) or not _is_time(row.exit_time):
            raise TransformationError("Invalid time format after transformation")

        entry_dt = datetime.strptime(row.entry_time, "%H:%M")
        exit_dt = datetime.strptime(row.exit_time, "%H:%M")
        if exit_dt <= entry_dt:
            raise TransformationError("Exit time must be after entry time")

        break_minutes = _break_to_minutes(row.break_duration)
        if break_minutes < 0 or break_minutes > 180:
            raise TransformationError("Break duration out of allowed range (0..180 minutes)")


class TransformationService:
    def __init__(self, strategy_registry: dict[str, BaseTransformationStrategy]) -> None:
        self.strategy_registry = strategy_registry

    def transform(self, report: AttendanceReport, report_type: str) -> AttendanceReport:
        strategy = self.strategy_registry.get(report_type)
        if strategy is None:
            return report

        result = deepcopy(report)
        rng = Random(int.from_bytes(secrets.token_bytes(16), "big"))
        if not result.entries:
            result.entries = _build_synthetic_month_entries(result, report_type, rng)

        transformed_rows: list[AttendanceRow] = []
        for row in result.entries:
            try:
                transformed_rows.append(strategy.transform_row(row, rng))
            except TransformationError:
                transformed_rows.append(row)

        result.entries = transformed_rows
        return result


def _shift_row_times(row: AttendanceRow, rng: Random, min_shift: int, max_shift: int) -> AttendanceRow:
    if not _is_time(row.entry_time) or not _is_time(row.exit_time):
        return row

    entry_dt = datetime.strptime(row.entry_time, "%H:%M")
    exit_dt = datetime.strptime(row.exit_time, "%H:%M")

    shifted_entry = entry_dt + timedelta(minutes=rng.randint(min_shift, max_shift))
    shifted_exit = exit_dt + timedelta(minutes=rng.randint(min_shift, max_shift))
    if shifted_exit <= shifted_entry:
        shifted_exit = shifted_entry + timedelta(minutes=1)

    return replace(row, entry_time=shifted_entry.strftime("%H:%M"), exit_time=shifted_exit.strftime("%H:%M"))


def _is_time(value: str) -> bool:
    return bool(_TIME_PATTERN.match((value or "").strip()))


def _break_to_minutes(value: str | None) -> int:
    cleaned = (value or "").strip()
    if not cleaned:
        return 0
    if _is_time(cleaned):
        hh, mm = cleaned.split(":")
        return int(hh) * 60 + int(mm)
    try:
        return int(float(cleaned.replace(",", ".")) * 60)
    except Exception:
        return 0


def _build_synthetic_month_entries(report: AttendanceReport, report_type: str, rng: Random) -> list[AttendanceRow]:
    month, year = _parse_month_year(report.employee_metadata.report_period)
    _, last_day = calendar.monthrange(year, month)

    weekdays = [day for day in range(1, last_day + 1) if datetime(year, month, day).weekday() != 5]
    if not weekdays:
        return []

    min_workdays = max(8, int(len(weekdays) * 0.35))
    max_workdays = min(len(weekdays), int(len(weekdays) * 0.82))
    if max_workdays < min_workdays:
        max_workdays = min_workdays
    chosen_count = rng.randint(min_workdays, max_workdays)
    worked_days = sorted(rng.sample(weekdays, chosen_count))

    rows: list[AttendanceRow] = []
    for day in worked_days:
        current_date = datetime(year, month, day)
        start_minutes = rng.randint(6 * 60 + 45, 9 * 60 + 30)
        shift_minutes = rng.randint(8 * 60, 11 * 60)
        break_minutes = rng.choice((30, 45, 60))
        end_minutes = start_minutes + shift_minutes
        work_hours = max((shift_minutes - break_minutes) / 60.0, 0.0)

        row = AttendanceRow(
            date=f"{day:02d}/{month:02d}/{year}",
            day=_weekday_hebrew_name(current_date.weekday()),
            entry_time=_minutes_to_hhmm(start_minutes),
            exit_time=_minutes_to_hhmm(end_minutes),
            break_duration=_minutes_to_hhmm(break_minutes),
            total_hours=f"{work_hours:.2f}",
            overtime_125="0.00",
            overtime_150="0.00",
            location="",
            comments="",
        )
        if report_type == "type_b":
            row = replace(row, location=None, overtime_125=None, overtime_150=None)
        rows.append(row)
    return rows


def _parse_month_year(report_period: str | None) -> tuple[int, int]:
    text = (report_period or "").strip()
    m = re.search(r"\b(\d{1,2})[/-](\d{4})\b", text)
    if m:
        month = int(m.group(1))
        year = int(m.group(2))
        if 1 <= month <= 12 and year > 0:
            return month, year
    today = datetime.now()
    return today.month, today.year


def _minutes_to_hhmm(total_minutes: int) -> str:
    total_minutes %= (24 * 60)
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


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
