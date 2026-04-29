from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(slots=True)
class AttendanceRow:
    """Shared attendance row model for all report types."""

    date: str
    day: str
    entry_time: str
    exit_time: str
    break_duration: str | None = None
    total_hours: str | None = None
    overtime_125: str | None = None
    overtime_150: str | None = None
    location: str | None = None
    comments: str | None = None


# Backward-compatible alias used across existing modules.
AttendanceEntry = AttendanceRow


@dataclass(slots=True)
class EmployeeMetadata:
    """Employee-level information displayed in the report."""

    employee_name: str = ""
    employee_id: str = ""
    company_name: str = ""
    company_logo_path: str = ""
    department: str = ""
    role: str = ""
    report_period: str = ""
    source_language: str = "he"


@dataclass(slots=True)
class ReportTotals:
    """Summary values calculated for the full attendance report."""

    total_hours: Decimal = field(default_factory=lambda: Decimal("0"))
    total_pay: Decimal = field(default_factory=lambda: Decimal("0"))
    total_days: int = 0


@dataclass(slots=True)
class AttendanceReport:
    """Complete attendance report structure used by parsers and generators."""

    entries: list[AttendanceEntry] = field(default_factory=list)
    employee_metadata: EmployeeMetadata = field(default_factory=EmployeeMetadata)
    totals: ReportTotals = field(default_factory=ReportTotals)
    source_pdf_path: str = ""
