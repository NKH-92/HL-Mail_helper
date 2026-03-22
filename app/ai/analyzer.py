"""Compatibility exports for the mail classification engine."""

from app.ai.classification_engine import (
    MailAnalysisResult,
    MailValidationCorrectedResult,
    MailValidationResult,
    MailRuleResult,
    build_decision_payload,
    build_failed_analysis_fallback,
    build_rule_result,
    decide_final_category,
    finalize_analysis_payload,
    normalize_analysis_payload,
    normalize_validation_payload,
    validate_analysis,
    validate_validation,
)

__all__ = [
    "MailAnalysisResult",
    "MailValidationCorrectedResult",
    "MailValidationResult",
    "MailRuleResult",
    "build_decision_payload",
    "build_failed_analysis_fallback",
    "build_rule_result",
    "decide_final_category",
    "finalize_analysis_payload",
    "normalize_analysis_payload",
    "normalize_validation_payload",
    "validate_analysis",
    "validate_validation",
]
