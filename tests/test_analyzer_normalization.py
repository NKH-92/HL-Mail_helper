from __future__ import annotations

import unittest

from app.ai.analyzer import (
    build_decision_payload,
    build_rule_result,
    normalize_analysis_payload,
    normalize_validation_payload,
    validate_analysis,
    validate_validation,
)


class AnalyzerNormalizationTests(unittest.TestCase):
    def test_normalizes_partial_payload_from_legacy_like_keys(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "action_required": True,
                "action_owner": "me",
                "action_type": [{"type": "review"}],
                "evidence": ["Please review and reply today."],
                "confidence": 0.95,
            },
            fallback_subject="Weekly report",
        )

        self.assertTrue(normalized["request_present"])
        self.assertEqual(normalized["request_target"], "me")
        self.assertTrue(normalized["request_target_is_me"])
        self.assertEqual(normalized["action_types"], ["REVIEW"])
        self.assertEqual(normalized["summary"], "Weekly report")
        self.assertEqual(normalized["llm_category"], 1)
        self.assertEqual(normalized["evidence"], ["Please review and reply today."])

    def test_prompt_echo_falls_back_to_safe_defaults(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "instructions": {"task": "analyze"},
                "mail": {"subject": "ignored"},
            },
            fallback_subject="Needs review",
        )

        self.assertFalse(normalized["request_present"])
        self.assertEqual(normalized["request_target"], "unknown")
        self.assertFalse(normalized["request_target_is_me"])
        self.assertEqual(normalized["action_types"], ["NONE"])
        self.assertEqual(normalized["summary"], "Needs review")
        self.assertEqual(normalized["llm_category"], 3)
        self.assertEqual(normalized["urgency"], "none")

    def test_due_date_is_normalized_to_sqlite_friendly_format(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "request_present": True,
                "request_target": "me",
                "request_target_is_me": True,
                "action_types": ["REPLY"],
                "summary": "Deadline test",
                "due_date": "2026/03/08 17:30",
                "llm_category": 1,
                "evidence": ["Please confirm by 2026/03/08 17:30."],
                "confidence": 0.9,
            }
        )

        self.assertEqual(normalized["due_date"], "2026-03-08 17:30:00")

    def test_actionable_result_without_evidence_lowers_confidence(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "request_present": True,
                "request_target": "me",
                "request_target_is_me": True,
                "action_types": ["REPLY"],
                "summary": "Request without evidence",
                "llm_category": 1,
                "confidence": 0.9,
            }
        )

        self.assertTrue(normalized["request_present"])
        self.assertEqual(normalized["evidence"], [])
        self.assertLessEqual(normalized["confidence"], 0.35)

    def test_nested_deadline_and_request_fields_are_preserved(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "request_present": True,
                "request_target": "me",
                "request_target_is_me": True,
                "action_types": ["APPROVE"],
                "summary": "Approval request",
                "deadline": {"raw": "today 5 PM", "iso": "2026-03-08T17:00"},
                "evidence": ["Please approve by today 5 PM."],
                "llm_category": 1,
                "confidence": 0.88,
            }
        )

        self.assertEqual(normalized["due_date"], "2026-03-08 17:00:00")
        self.assertEqual(normalized["deadline_raw"], "today 5 PM")
        self.assertEqual(normalized["action_types"], ["APPROVE"])

    def test_deadline_is_inferred_from_evidence_when_model_omits_due_date(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "request_present": True,
                "request_target": "me",
                "request_target_is_me": True,
                "action_types": ["REPLY"],
                "summary": "Reply requested",
                "evidence": ["Please reply by Friday 2 PM."],
                "llm_category": 1,
                "confidence": 0.91,
            },
            body_text="Please reply by Friday 2 PM.",
            received_at="2026-03-11 09:00:00",
        )

        self.assertEqual(normalized["due_date"], "2026-03-13 14:00:00")
        self.assertEqual(normalized["deadline_raw"], "Please reply by Friday 2 PM.")

    def test_build_decision_payload_preserves_deadline_raw_text(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "request_present": True,
                "request_target": "me",
                "request_target_is_me": True,
                "action_types": ["REPLY"],
                "summary": "Reply by Friday",
                "deadline": {"raw": "Friday 2 PM", "iso": "2026-03-13 14:00:00"},
                "evidence": ["Please reply by Friday 2 PM."],
                "llm_category": 1,
                "confidence": 0.9,
            }
        )
        rule_result = build_rule_result(
            user_email="user@example.com",
            sender_email="sender@example.com",
            to_list=["user@example.com"],
            cc_list=[],
            subject="Reply",
            body_text="Please reply by Friday 2 PM.",
            thread_id="thread-deadline",
            message_id="msg-deadline",
        )

        decision_payload = build_decision_payload(
            rule_result=rule_result,
            analysis=validate_analysis(normalized),
            model_name="model",
            analyzed_at="2026-03-08 09:00:00",
            raw_llm_json="{}",
            deadline_raw=normalized["deadline_raw"],
        )

        self.assertEqual(decision_payload["due_date"], "2026-03-13 14:00:00")
        self.assertEqual(decision_payload["deadline_raw"], "Friday 2 PM")

    def test_validation_payload_recomputes_final_category_from_policy(self) -> None:
        rule_result = build_rule_result(
            user_email="user@example.com",
            sender_email="sender@example.com",
            to_list=["user@example.com"],
            cc_list=[],
            subject="FYI",
            body_text="Sharing the latest status only.",
            thread_id="thread-1",
            message_id="msg-1",
        )
        normalized = normalize_validation_payload(
            {
                "is_valid": False,
                "corrected_result": {
                    "request_present": False,
                    "request_target": "unknown",
                    "request_target_is_me": False,
                    "action_types": ["NONE"],
                    "due_date": None,
                    "urgency": "none",
                    "llm_category": 3,
                    "final_category": 1,
                    "evidence": ["Sharing the latest status only."],
                    "summary": "Informational update",
                    "confidence": 0.74,
                },
                "issues": ["Candidate overcalled the email as actionable."],
            },
            fallback_subject="FYI",
            body_text="Sharing the latest status only.",
            rule_result=rule_result,
        )
        validated = validate_validation(normalized)

        self.assertFalse(validated.is_valid)
        self.assertEqual(validated.corrected_result.final_category, 3)
        self.assertEqual(validated.corrected_result.urgency, "none")
        self.assertIn("Candidate overcalled the email as actionable.", validated.issues)

    def test_rule_result_treats_known_aliases_as_my_recipients(self) -> None:
        rule_result = build_rule_result(
            user_email="user@example.com",
            known_recipient_addresses=["user@example.com", "qa.alias@example.com"],
            sender_email="sender@example.com",
            to_list=["qa.alias@example.com"],
            cc_list=[],
            subject="Alias target",
            body_text="Please review this.",
            thread_id="thread-2",
            message_id="msg-2",
        )

        self.assertTrue(rule_result.is_to_me)
        self.assertFalse(rule_result.is_cc_me)
        self.assertEqual(rule_result.recipient_role, "TO")
        self.assertEqual(rule_result.rule_category, 1)

    def test_rule_result_treats_group_alias_in_to_as_cc(self) -> None:
        rule_result = build_rule_result(
            user_email="user@example.com",
            known_recipient_addresses=["user@example.com"],
            cc_only_recipient_addresses=["quality-group@example.com"],
            sender_email="sender@example.com",
            to_list=["quality-group@example.com"],
            cc_list=[],
            subject="Group alias target",
            body_text="Please review this with the team.",
            thread_id="thread-3",
            message_id="msg-3",
        )

        self.assertFalse(rule_result.is_to_me)
        self.assertTrue(rule_result.is_cc_me)
        self.assertEqual(rule_result.recipient_role, "CC")
        self.assertEqual(rule_result.rule_category, 2)


if __name__ == "__main__":
    unittest.main()
