from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable

import pdfplumber

from attendance_report.domain.interfaces import BaseGenerator, BaseParser
from attendance_report.generation.pdf_renderer import SameLayoutPdfGenerator
from attendance_report.parsing.type_a_parser import TypeAParser
from attendance_report.parsing.type_b_parser import TypeBParser

ReportDetector = Callable[[str], bool]


@dataclass(slots=True)
class ReportProcessor:
    """Bundled processor pair returned by the factory."""

    parser: BaseParser
    generator: BaseGenerator
    report_type: str


class ReportProcessorFactory:
    """Registry-based factory for scalable parser/generator selection."""

    _registry: list[tuple[ReportDetector, type[BaseParser], type[BaseGenerator], str]] = []

    @classmethod
    def register(
        cls,
        detector: ReportDetector,
        parser_cls: type[BaseParser],
        generator_cls: type[BaseGenerator],
        report_type: str,
    ) -> None:
        cls._registry.append((detector, parser_cls, generator_cls, report_type))

    @classmethod
    def create(cls, file_path: str) -> ReportProcessor:
        # Naming convention (e.g. a_r_*.pdf vs n_r_*_n.pdf) must win before text heuristics,
        # otherwise Type A's loose rules or "best parser" can steal Type B files.
        hinted = cls._create_by_filename_hint(file_path)
        if hinted is not None:
            return hinted

        preview = cls._read_pdf_text(file_path)
        for detector, parser_cls, generator_cls, report_type in cls._registry:
            if detector(preview):
                return ReportProcessor(
                    parser=parser_cls(),
                    generator=generator_cls(),
                    report_type=report_type,
                )
        fallback = cls._create_by_best_parser(str(Path(file_path)))
        if fallback is not None:
            return fallback
        raise ValueError(
            f"Unsupported report format: {file_path}. "
            "Could not detect text signature and fallback parsing found no attendance rows."
        )

    @classmethod
    def create_for_type(cls, report_type: str) -> ReportProcessor:
        for _, parser_cls, generator_cls, registered_type in cls._registry:
            if registered_type == report_type:
                return ReportProcessor(
                    parser=parser_cls(),
                    generator=generator_cls(),
                    report_type=registered_type,
                )
        raise ValueError(f"Unsupported report type override: {report_type}")

    @staticmethod
    def _read_pdf_text(file_path: str) -> str:
        pdf_text_parts: list[str] = []
        normalized_path = str(Path(file_path))
        with pdfplumber.open(normalized_path) as pdf:
            # Sample first pages to support robust detection while keeping it fast.
            for page in pdf.pages[:2]:
                extracted = page.extract_text() or ""
                if not extracted.strip():
                    words = page.extract_words() or []
                    extracted = " ".join(word.get("text", "") for word in words if word.get("text"))
                pdf_text_parts.append(extracted)
        return "\n".join(pdf_text_parts)

    @classmethod
    def _create_by_best_parser(cls, file_path: str) -> ReportProcessor | None:
        preview = cls._read_pdf_text(file_path)
        # If the PDF looks like Type B, never pick Type A just because it parsed more rows.
        if _type_b_fingerprint(preview):
            return cls.create_for_type("type_b")

        best_result: tuple[int, type[BaseParser], type[BaseGenerator], str] | None = None
        for _, parser_cls, generator_cls, report_type in cls._registry:
            try:
                parsed = parser_cls().parse(file_path)
            except Exception:
                continue

            entry_count = len(parsed.entries)
            if best_result is None or entry_count > best_result[0]:
                best_result = (entry_count, parser_cls, generator_cls, report_type)

        if best_result is None or best_result[0] == 0:
            return None

        _, parser_cls, generator_cls, report_type = best_result
        return ReportProcessor(
            parser=parser_cls(),
            generator=generator_cls(),
            report_type=report_type,
        )

    @classmethod
    def _create_by_filename_hint(cls, file_path: str) -> ReportProcessor | None:
        name = Path(file_path).name.lower()
        if name.startswith("a_") or "_a_" in name:
            return cls.create_for_type("type_a")
        if name.startswith("n_") or "_n_" in name:
            return cls.create_for_type("type_b")
        return None


def _normalize_for_detection(text: str) -> str:
    lowered = text.lower()
    # Keep only alphanumeric Hebrew/Latin chars to make matching robust to PDF punctuation noise.
    return re.sub(r"[^0-9a-zא-ת]+", "", lowered)


def _type_b_fingerprint(preview_text: str) -> bool:
    """Heuristics for Type B (employee-card style). Kept separate from Type A fallbacks."""
    normalized = _normalize_for_detection(preview_text)
    if "כרטיסעובד" in normalized:
        return True
    # Subtitle fragments: "מפורט עם שעות נוספות"
    if "עםשעותנוספות" in normalized or "מפורטעםשעות" in normalized:
        return True
    if "מפורט" in normalized and "שעותנוספות" in normalized:
        return True
    if "מפורט" in normalized and "נוספות" in normalized and "נוכחות" in normalized:
        return True
    if "דוחנוכחות" in normalized and "מפורט" in normalized:
        return True
    return False


def _is_type_a(preview_text: str) -> bool:
    if _type_b_fingerprint(preview_text):
        return False
    normalized = _normalize_for_detection(preview_text)
    company_marker_match = any(
        marker in normalized
        for marker in (
            "נעהנשר",
            "נהנשר",
            "הנשרכחאדם",
        )
    )
    if company_marker_match:
        return True
    if "דוחנוכחותחודשי" in normalized or (
        "חודשי" in normalized and "נוכחות" in normalized and "מפורט" not in normalized
    ):
        return True
    # Do NOT use generic 125%/150%/break columns here — Type B PDFs contain them too and would
    # be misclassified as Type A when Type B fingerprint text is missing from extraction.
    return False


def _is_type_b(preview_text: str) -> bool:
    return _type_b_fingerprint(preview_text)


# Type B must be registered before Type A: Type A's fallback detector matches any PDF that
# contains 125%/150%/break columns, which Type B reports also include.
ReportProcessorFactory.register(_is_type_b, TypeBParser, lambda: SameLayoutPdfGenerator("type_b"), "type_b")
ReportProcessorFactory.register(_is_type_a, TypeAParser, lambda: SameLayoutPdfGenerator("type_a"), "type_a")
