class AttendanceReportError(Exception):
    """Base exception for attendance report processing."""


class UnsupportedReportTypeError(AttendanceReportError):
    """Raised when no supported report type can be resolved."""


class ReportParseError(AttendanceReportError):
    """Raised when parsing fails for an input report."""


class ReportGenerationError(AttendanceReportError):
    """Raised when rendering output fails."""


class TransformationError(AttendanceReportError):
    """Raised when transformed attendance row fails validation."""
