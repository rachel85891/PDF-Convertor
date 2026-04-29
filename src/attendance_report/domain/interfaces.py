from __future__ import annotations

from abc import ABC, abstractmethod

from attendance_report.domain.models import AttendanceReport


class BaseParser(ABC):
    """Contract for parsing an input file into an attendance report."""

    @abstractmethod
    def parse(self, file_path: str) -> AttendanceReport:
        """Parse the source file and return a structured attendance report."""


class BaseGenerator(ABC):
    """Contract for generating an output file from an attendance report."""

    @abstractmethod
    def generate(self, report: AttendanceReport, output_path: str) -> None:
        """Generate the final output at the provided path."""
