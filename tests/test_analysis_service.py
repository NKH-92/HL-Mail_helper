from __future__ import annotations

import logging
import unittest

from app.ai.analyzer import build_rule_result
from app.core.config_manager import AppConfig
from app.db.models import MailRecord
from app.services.analysis_service import AnalysisService


class _FakeConfigManager:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def load(self) -> AppConfig:
        return self._config


class _FakeAddressBookService:
    @staticmethod
    def merge_config_profile(config: AppConfig) -> AppConfig:
        return config


class _FakePromptManager:
    @staticmethod
    def build_system_prompt() -> str:
        return "system"

    @staticmethod
    def build_validation_system_prompt() -> str:
        return "validator-system"

    @staticmethod
    def build_user_prompt(*args, **kwargs) -> str:
        return "user"

    @staticmethod
    def build_validation_user_prompt(*args, **kwargs) -> str:
        return "validator-user"


class _FakeGeminiClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    @staticmethod
    def has_api_key(config=None) -> bool:
        return True

    def generate_json(self, *, config, system_prompt, user_prompt, response_schema):
        self.calls.append(
            {
                "config": config,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_schema": response_schema,
            }
        )
        if not self._responses:
            raise AssertionError("No fake responses left.")
        return self._responses.pop(0)


class _FakeMailRepository:
    def __init__(self, thread_summary: str = "") -> None:
        self._thread_summary = thread_summary

    def build_thread_summary(self, mail_id: int) -> str:
        return self._thread_summary


def _make_mail(*, body_text: str) -> MailRecord:
    return MailRecord(
        id=1,
        message_id="msg-1",
        subject="Subject",
        normalized_subject="Subject",
        thread_key="thread-1",
        in_reply_to=None,
        references=[],
        sender_name="Sender",
        sender_email="sender@example.com",
        to_list=["user@example.com"],
        cc_list=[],
        received_at="2026-03-19 09:00:00",
        body_text=body_text,
        raw_preview=body_text,
        attachment_names=[],
        attachment_paths=[],
        category=None,
        priority=None,
        summary_short=None,
        summary_long=[],
        due_date=None,
        my_action_required=False,
        my_action_status=None,
        ownership_reason=[],
        confidence=None,
        status="todo",
        analysis_status="pending",
        analysis_error=None,
        created_at="2026-03-19 09:00:00",
        updated_at="2026-03-19 09:00:00",
    )


class AnalysisServiceTests(unittest.TestCase):
    def test_validation_pass_can_correct_first_pass_result(self) -> None:
        config = AppConfig(
            user_email="user@example.com",
            user_display_name="Tester",
            user_job_title="Lead",
            store_raw_body=True,
            preview_max_chars=4000,
        )
        first_pass = {
            "request_present": True,
            "request_target": "other",
            "request_target_is_me": False,
            "action_types": ["REVIEW"],
            "due_date": None,
            "urgency": "medium",
            "llm_category": 2,
            "evidence": ["Please review."],
            "summary": "Needs review",
            "confidence": 0.6,
        }
        validator_pass = {
            "is_valid": False,
            "corrected_result": {
                "request_present": False,
                "request_target": "unknown",
                "request_target_is_me": False,
                "action_types": ["NONE"],
                "due_date": None,
                "urgency": "none",
                "llm_category": 3,
                "final_category": 3,
                "evidence": ["Sharing the latest status only."],
                "summary": "Informational update",
                "confidence": 0.74,
            },
            "issues": ["No concrete request was present."],
        }
        gemini_client = _FakeGeminiClient([first_pass, validator_pass])
        service = AnalysisService(
            config_manager=_FakeConfigManager(config),
            address_book_service=_FakeAddressBookService(),
            prompt_manager=_FakePromptManager(),
            gemini_client=gemini_client,
            mail_repository=_FakeMailRepository(),
            logger=logging.getLogger("test"),
        )
        mail = _make_mail(body_text="Sharing the latest status only.")
        rule_result = build_rule_result(
            user_email=config.user_email,
            sender_email=mail.sender_email,
            to_list=mail.to_list,
            cc_list=mail.cc_list,
            subject=mail.subject,
            body_text=mail.body_text,
            thread_id=mail.thread_key,
            message_id=mail.message_id,
        )

        result, audit = service._analyze_mail(config, mail, rule_result=rule_result)

        self.assertFalse(result.request_present)
        self.assertEqual(result.urgency, "none")
        self.assertTrue(audit["validator_used"])
        self.assertTrue(audit["validator_applied"])
        self.assertEqual(len(gemini_client.calls), 2)

    def test_validation_pass_is_skipped_for_high_confidence_clear_mail(self) -> None:
        config = AppConfig(
            user_email="user@example.com",
            user_display_name="Tester",
            user_job_title="Lead",
            store_raw_body=True,
            preview_max_chars=4000,
        )
        first_pass = {
            "request_present": True,
            "request_target": "me",
            "request_target_is_me": True,
            "action_types": ["REPLY"],
            "due_date": None,
            "urgency": "high",
            "llm_category": 1,
            "evidence": ["Please reply with your approval by today."],
            "summary": "Reply with approval today",
            "confidence": 0.97,
        }
        gemini_client = _FakeGeminiClient([first_pass])
        service = AnalysisService(
            config_manager=_FakeConfigManager(config),
            address_book_service=_FakeAddressBookService(),
            prompt_manager=_FakePromptManager(),
            gemini_client=gemini_client,
            mail_repository=_FakeMailRepository(),
            logger=logging.getLogger("test"),
        )
        mail = _make_mail(
            body_text=(
                "Please reply with your approval by today. "
                "Thanks for reviewing the file carefully and confirming the revised proposal for the customer. "
                "Once you reply, we will send the final version."
            )
        )
        rule_result = build_rule_result(
            user_email=config.user_email,
            sender_email=mail.sender_email,
            to_list=mail.to_list,
            cc_list=mail.cc_list,
            subject=mail.subject,
            body_text=mail.body_text,
            thread_id=mail.thread_key,
            message_id=mail.message_id,
        )

        result, audit = service._analyze_mail(config, mail, rule_result=rule_result)

        self.assertTrue(result.request_present)
        self.assertFalse(audit["validator_used"])
        self.assertFalse(audit["validator_applied"])
        self.assertEqual(len(gemini_client.calls), 1)

    def test_analyze_mail_preserves_deadline_raw_for_final_persistence(self) -> None:
        config = AppConfig(
            user_email="user@example.com",
            user_display_name="Tester",
            user_job_title="Lead",
            store_raw_body=True,
            preview_max_chars=4000,
        )
        first_pass = {
            "request_present": True,
            "request_target": "me",
            "request_target_is_me": True,
            "action_types": ["REPLY"],
            "deadline": {"raw": "next Friday 5pm", "iso": "2026-03-27 17:00:00"},
            "urgency": "high",
            "llm_category": 1,
            "evidence": ["Please reply by next Friday 5pm."],
            "summary": "Reply by next Friday",
            "confidence": 0.96,
        }
        gemini_client = _FakeGeminiClient([first_pass])
        service = AnalysisService(
            config_manager=_FakeConfigManager(config),
            address_book_service=_FakeAddressBookService(),
            prompt_manager=_FakePromptManager(),
            gemini_client=gemini_client,
            mail_repository=_FakeMailRepository(),
            logger=logging.getLogger("test"),
        )
        mail = _make_mail(
            body_text=(
                "Please reply by next Friday 5pm. "
                "This message includes the full context so the analyzer does not need a second validator pass. "
                "Confirm the updated plan, acknowledge the open question, and send the reply once you finish reviewing."
            )
        )
        rule_result = build_rule_result(
            user_email=config.user_email,
            sender_email=mail.sender_email,
            to_list=mail.to_list,
            cc_list=mail.cc_list,
            subject=mail.subject,
            body_text=mail.body_text,
            thread_id=mail.thread_key,
            message_id=mail.message_id,
        )

        result, audit = service._analyze_mail(config, mail, rule_result=rule_result)

        self.assertEqual(result.due_date, "2026-03-27 17:00:00")
        self.assertEqual(audit["final_deadline_raw"], "next Friday 5pm")
        self.assertEqual(len(gemini_client.calls), 1)


if __name__ == "__main__":
    unittest.main()
