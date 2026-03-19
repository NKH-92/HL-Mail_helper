from __future__ import annotations

import unittest

from app.ai.analyzer import normalize_analysis_payload


class AnalyzerNormalizationTests(unittest.TestCase):
    def test_normalizes_partial_payload_and_dict_action_items(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "category": "FYI",
                "priority": "low",
                "mail_action_items": [{"action": "회의자료 확인", "status": "open"}],
                "my_action_items": [{"task": "종합 리포트 확인", "action_type": "review_needed"}],
                "evidence": ["종합 리포트 확인 부탁드립니다."],
                "confidence": 0.95,
            },
            fallback_subject="월간 보고",
        )

        self.assertEqual(normalized["one_line_summary"], "월간 보고")
        self.assertEqual(normalized["summary_3lines"], ["월간 보고"])
        self.assertEqual(normalized["mail_action_items"], ["회의자료 확인"])
        self.assertEqual(normalized["my_action_items"], ["종합 리포트 확인"])
        self.assertEqual(normalized["classification"], "ACTION_SHARED")
        self.assertEqual(normalized["my_action_status"], "review_needed")
        self.assertTrue(normalized["my_action_required"])
        self.assertEqual(normalized["action_owner"], "team")
        self.assertEqual(normalized["evidence"], ["종합 리포트 확인 부탁드립니다."])

    def test_prompt_echo_falls_back_to_safe_defaults(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "instructions": {"task": "회사 메일 분석"},
                "mail": {"subject": "ignored"},
            },
            fallback_subject="수동 확인 필요 메일",
        )

        self.assertEqual(normalized["category"], "ETC")
        self.assertEqual(normalized["priority"], "unknown")
        self.assertEqual(normalized["classification"], "UNCLEAR")
        self.assertEqual(normalized["one_line_summary"], "수동 확인 필요 메일")
        self.assertEqual(normalized["my_action_status"], "reference_only")
        self.assertFalse(normalized["my_action_required"])

    def test_due_date_is_normalized_to_sqlite_friendly_format(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "category": "ACT",
                "priority": "high",
                "one_line_summary": "기한 테스트",
                "summary_3lines": ["기한 테스트"],
                "mail_action_items": [],
                "my_action_required": True,
                "my_action_status": "direct_action",
                "my_action_items": ["확인"],
                "evidence": ["3월 8일 17:30까지 확인 부탁드립니다."],
                "due_date": "2026/03/08 17:30",
                "confidence": 0.9,
            }
        )
        dropped = normalize_analysis_payload(
            {
                "category": "ACT",
                "priority": "high",
                "one_line_summary": "기한 테스트",
                "summary_3lines": ["기한 테스트"],
                "mail_action_items": [],
                "my_action_required": True,
                "my_action_status": "direct_action",
                "my_action_items": ["확인"],
                "evidence": ["가능하면 다음 주 화요일까지 봐 주세요."],
                "due_date": "next Tuesday",
                "confidence": 0.9,
            }
        )

        self.assertEqual(normalized["due_date"], "2026-03-08 17:30:00")
        self.assertIsNone(dropped["due_date"])

    def test_actionable_result_without_evidence_downgrades_to_unclear(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "classification": "ACTION_SELF",
                "priority": "high",
                "one_line_summary": "근거 없는 요청",
                "summary_3lines": ["근거 없는 요청"],
                "my_action_required": True,
                "my_action_status": "direct_action",
                "my_action_items": ["회신"],
                "confidence": 0.9,
            }
        )

        self.assertEqual(normalized["classification"], "UNCLEAR")
        self.assertFalse(normalized["my_action_required"])
        self.assertEqual(normalized["my_action_status"], "reference_only")
        self.assertEqual(normalized["my_action_items"], [])

    def test_nested_deadline_and_suggested_task_title_are_preserved(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "classification": "APPROVAL_REQUEST",
                "priority": "medium",
                "one_line_summary": "결재 요청",
                "summary_3lines": ["결재 요청"],
                "action_required": True,
                "action_owner": "me",
                "action_type": ["approve"],
                "deadline": {"raw": "오늘 오후 5시까지", "iso": "2026-03-08T17:00"},
                "evidence": ["오늘 오후 5시까지 승인 부탁드립니다."],
                "reason": "승인 요청이 직접 확인됩니다.",
                "suggested_task_title": "결재 승인",
                "confidence": 0.88,
            }
        )

        self.assertEqual(normalized["classification"], "APPROVAL_REQUEST")
        self.assertEqual(normalized["due_date"], "2026-03-08 17:00:00")
        self.assertEqual(normalized["deadline_raw"], "오늘 오후 5시까지")
        self.assertEqual(normalized["my_action_items"], ["결재 승인"])
        self.assertEqual(normalized["suggested_task_title"], "결재 승인")


    def test_deadline_raw_is_normalized_against_received_time(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "classification": "ACTION_SELF",
                "priority": "high",
                "one_line_summary": "Reply requested",
                "summary_3lines": ["Reply requested"],
                "mail_action_items": ["Send reply"],
                "my_action_required": True,
                "my_action_status": "direct_action",
                "my_action_items": ["Send reply"],
                "deadline_raw": "Friday 2:30 PM",
                "evidence": ["Please reply by Friday 2:30 PM."],
                "reason": "The sender asked for a response by Friday afternoon.",
                "confidence": 0.9,
            },
            received_at="2026-03-11 09:00:00",
        )

        self.assertEqual(normalized["due_date"], "2026-03-13 14:30:00")
        self.assertEqual(normalized["deadline_raw"], "Friday 2:30 PM")

    def test_deadline_is_inferred_from_evidence_when_model_omits_deadline_fields(self) -> None:
        normalized = normalize_analysis_payload(
            {
                "classification": "ACTION_SELF",
                "priority": "high",
                "one_line_summary": "Send the revised quote",
                "summary_3lines": ["Vendor requested the revised quote."],
                "mail_action_items": ["Send revised quote"],
                "my_action_required": True,
                "my_action_status": "direct_action",
                "my_action_items": ["Send revised quote"],
                "evidence": ["Please reply by Friday 2 PM."],
                "reason": "The sender expects the revised quote by Friday afternoon.",
                "confidence": 0.91,
            },
            body_text="Please reply by Friday 2 PM.",
            received_at="2026-03-11 09:00:00",
        )

        self.assertEqual(normalized["due_date"], "2026-03-13 14:00:00")
        self.assertEqual(normalized["deadline_raw"], "Please reply by Friday 2 PM.")


if __name__ == "__main__":
    unittest.main()
