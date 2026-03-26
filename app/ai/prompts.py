"""Prompt loading and composition."""

from __future__ import annotations

import json
from pathlib import Path
import zlib

from app.db.models import MailRecord
from app.ai.embedded_prompts import BUILTIN_PROMPTS


class PromptManager:
    """Load prompt text with optional disk overrides and built-in defaults."""

    def __init__(self, prompt_dir: Path, fallback_prompt_dir: Path | None = None) -> None:
        self.prompt_dir = prompt_dir
        self.fallback_prompt_dir = fallback_prompt_dir
        self._cached_system_prompt_signature: tuple[tuple[str, int | None], ...] | None = None
        self._cached_system_prompt: str = ""

    def _read(self, file_name: str) -> str:
        path = self.prompt_dir / file_name
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        if self.fallback_prompt_dir:
            fallback_path = self.fallback_prompt_dir / file_name
            if fallback_path.exists():
                return fallback_path.read_text(encoding="utf-8").strip()
        return BUILTIN_PROMPTS.get(file_name, "").strip()

    def build_system_prompt(self) -> str:
        """Return the shared first-pass system prompt."""

        signature = self._build_prompt_signature(
            ["classify_prompt.txt", "summarize_prompt.txt", "ownership_prompt.txt"],
        )
        if self._cached_system_prompt_signature == signature and self._cached_system_prompt:
            return self._cached_system_prompt

        parts = [
            self._read("classify_prompt.txt"),
            self._read("summarize_prompt.txt"),
            self._read("ownership_prompt.txt"),
            (
                "# FIRST-PASS OUTPUT CONTRACT\n"
                "Return JSON only.\n"
                "Do not return markdown, prose, comments, or code fences.\n"
                "Do not return final_category in this pass. The upstream decision engine computes final_category "
                "from routing facts and your semantic result.\n"
                "All fields below must be present and enum values must match exactly.\n\n"
                "{\n"
                '  "request_present": boolean,\n'
                '  "request_target": "me" | "other" | "group" | "unknown",\n'
                '  "request_target_is_me": boolean,\n'
                '  "action_types": ["REPLY" | "REVIEW" | "APPROVE" | "SUBMIT" | "MODIFY" | "SCHEDULE" | '
                '"FOLLOW_UP" | "DECIDE" | "NONE"],\n'
                '  "due_date": string | null,\n'
                '  "urgency": "high" | "medium" | "low" | "none" | "unknown",\n'
                '  "llm_category": 1 | 2 | 3,\n'
                '  "evidence": [string],\n'
                '  "summary": string,\n'
                '  "confidence": number\n'
                "}\n\n"
                "Consistency rules:\n"
                "- If request_present=false, action_types should be [\"NONE\"], llm_category must be 3, and urgency "
                "should usually be \"none\".\n"
                "- If request_target_is_me=true, llm_category should be 1.\n"
                "- If the user is only in Cc but explicitly named as the acting person, request_target must be \"me\".\n"
                "- Never change routing facts such as is_to_me, is_cc_me, or recipient_role.\n"
                "- Evidence must be the smallest set of short quotes needed to justify the decision.\n"
                "- summary must be written in Korean by default.\n"
                "- If a key business term is clearer in English, write Korean first and append the original English "
                "in parentheses on first mention. Example: 공정 밸리데이션(Process Validation).\n"
                "- Avoid English-only summaries unless the term is a fixed proper noun, product name, or acronym.\n"
                "- Evidence may remain in the original language when quoting the source email."
            ),
        ]
        self._cached_system_prompt_signature = signature
        self._cached_system_prompt = "\n\n".join(part for part in parts if part)
        return self._cached_system_prompt

    def build_validation_system_prompt(self) -> str:
        """Return the conditional second-pass validation prompt."""

        return (
            "You are a strict JSON validator and policy auditor for an email action classification system.\n\n"
            "Your job is to review:\n"
            "1) routing facts\n"
            "2) original email content\n"
            "3) a candidate JSON result\n\n"
            "You must check whether the candidate result is logically consistent with the policy.\n"
            "Treat routing facts as authoritative truth and do not override them.\n"
            "You may correct semantic interpretation fields when evidence supports it.\n"
            "Evidence must support the decision. Do not fabricate evidence.\n"
            "Action types must only use the allowed enum values.\n"
            "Return JSON only.\n"
            "No markdown.\n"
            "No comments.\n"
            "No extra keys.\n\n"
            "Required validator output schema:\n"
            "{\n"
            '  "is_valid": boolean,\n'
            '  "corrected_result": {\n'
            '    "request_present": boolean,\n'
            '    "request_target": "me" | "other" | "group" | "unknown",\n'
            '    "request_target_is_me": boolean,\n'
            '    "action_types": ["REPLY" | "REVIEW" | "APPROVE" | "SUBMIT" | "MODIFY" | "SCHEDULE" | '
            '"FOLLOW_UP" | "DECIDE" | "NONE"],\n'
            '    "due_date": string | null,\n'
            '    "urgency": "high" | "medium" | "low" | "none" | "unknown",\n'
            '    "llm_category": 1 | 2 | 3,\n'
            '    "final_category": 1 | 2 | 3,\n'
            '    "evidence": [string],\n'
            '    "summary": string,\n'
            '    "confidence": number\n'
            "  },\n"
            '  "issues": [string]\n'
            "}\n\n"
            "Validation policy:\n"
            "- final_category must exactly follow the required policy rules.\n"
            "- request_present=false requires final_category=3.\n"
            "- request_target_is_me=true requires final_category=1.\n"
            "- request_present=true and is_to_me=true requires final_category=1.\n"
            "- request_present=true and is_cc_me=true and is_to_me=false requires final_category=2 unless "
            "request_target_is_me=true.\n"
            "- If the email is short and vague, rely on thread_context when available.\n"
            "- If an email mixes sharing language and request language, prioritize the real request when supported by evidence.\n"
            "- summary must be one sentence and businesslike.\n"
            "- corrected_result.summary must be written in Korean by default.\n"
            "- If a key business term is clearer in English, write Korean first and append the original English in "
            "parentheses on first mention. Example: 공정 밸리데이션(Process Validation).\n"
            "- Avoid English-only summaries unless the term is a fixed proper noun, product name, or acronym.\n"
            "- Evidence may remain in the original language when quoting the source email."
        )

    def _build_prompt_signature(self, file_names: list[str]) -> tuple[tuple[str, int | None], ...]:
        signature: list[tuple[str, int | None]] = []
        for file_name in file_names:
            source_path = self.prompt_dir / file_name
            if source_path.exists():
                signature.append((str(source_path.resolve()), source_path.stat().st_mtime_ns))
                continue
            if self.fallback_prompt_dir:
                fallback_path = self.fallback_prompt_dir / file_name
                if fallback_path.exists():
                    signature.append((str(fallback_path.resolve()), fallback_path.stat().st_mtime_ns))
                    continue
            builtin_prompt = BUILTIN_PROMPTS.get(file_name, "")
            signature.append((f"builtin:{file_name}", zlib.crc32(builtin_prompt.encode("utf-8"))))
        return tuple(signature)

    def build_user_prompt(
        self,
        mail: MailRecord,
        thread_summary: str = "",
        model_name: str = "",
        body_char_limit: int = 4000,
        current_user: dict[str, str] | None = None,
        rule_context: dict[str, object] | None = None,
    ) -> str:
        """Render structured mail data into the user prompt."""

        del model_name
        user = current_user or {}
        rules = rule_context or {}
        body_text = self._truncate_text(mail.body_text or mail.raw_preview, body_char_limit)

        return (
            "Analyze the following email for the target user.\n\n"
            "[target_user]\n"
            f"- name: {self._format_inline_value(user.get('display_name'))}\n"
            f"- title: {self._format_inline_value(user.get('job_title'))}\n"
            f"- email: {self._format_inline_value(user.get('email'))}\n\n"
            "[routing_facts]\n"
            f"- is_to_me: {self._format_bool(rules.get('is_to_me'))}\n"
            f"- is_cc_me: {self._format_bool(rules.get('is_cc_me'))}\n"
            f"- recipient_role: {self._format_inline_value(rules.get('recipient_role') or 'NONE')}\n\n"
            "[sender]\n"
            f"- sender_name: {self._format_inline_value(mail.sender_name)}\n"
            f"- sender_email: {self._format_inline_value(mail.sender_email)}\n"
            f"- sender_type: {self._format_inline_value(rules.get('sender_type') or 'external')}\n\n"
            "[email]\n"
            f"- subject: {self._format_inline_value(mail.subject)}\n"
            "- body_text:\n"
            f"{self._format_multiline_block(body_text)}\n\n"
            "[attachments]\n"
            f"{self._format_bullet_block(mail.attachment_names)}\n\n"
            "[thread_context]\n"
            f"{self._format_thread_context(thread_summary)}\n\n"
            "[additional_instructions]\n"
            "- Treat routing_facts as authoritative truth.\n"
            "- Determine whether there is a real actionable request.\n"
            "- Determine whether the request is directed to the target user.\n"
            "- Return JSON only."
        )

    def build_validation_user_prompt(
        self,
        *,
        mail: MailRecord,
        thread_summary: str = "",
        current_user: dict[str, str] | None = None,
        rule_context: dict[str, object] | None = None,
        candidate_result: dict[str, object] | None = None,
        body_char_limit: int = 4000,
    ) -> str:
        """Render the validator-specific user prompt."""

        user = current_user or {}
        rules = rule_context or {}
        body_text = self._truncate_text(mail.body_text or mail.raw_preview, body_char_limit)
        candidate_json = json.dumps(candidate_result or {}, ensure_ascii=False, indent=2)

        return (
            "Validate the candidate classification result.\n\n"
            "[target_user]\n"
            f"- name: {self._format_inline_value(user.get('display_name'))}\n"
            f"- title: {self._format_inline_value(user.get('job_title'))}\n"
            f"- email: {self._format_inline_value(user.get('email'))}\n\n"
            "[routing_facts]\n"
            f"- is_to_me: {self._format_bool(rules.get('is_to_me'))}\n"
            f"- is_cc_me: {self._format_bool(rules.get('is_cc_me'))}\n"
            f"- recipient_role: {self._format_inline_value(rules.get('recipient_role') or 'NONE')}\n\n"
            "[sender]\n"
            f"- sender_name: {self._format_inline_value(mail.sender_name)}\n"
            f"- sender_email: {self._format_inline_value(mail.sender_email)}\n"
            f"- sender_type: {self._format_inline_value(rules.get('sender_type') or 'external')}\n\n"
            "[email]\n"
            f"- subject: {self._format_inline_value(mail.subject)}\n"
            "- body_text:\n"
            f"{self._format_multiline_block(body_text)}\n\n"
            "[attachments]\n"
            f"{self._format_bullet_block(mail.attachment_names)}\n\n"
            "[thread_context]\n"
            f"{self._format_thread_context(thread_summary)}\n\n"
            "[candidate_result]\n"
            f"{candidate_json}\n\n"
            "Return JSON only."
        )

    @staticmethod
    def _format_bool(value: object) -> str:
        return "true" if bool(value) else "false"

    @staticmethod
    def _format_inline_value(value: object) -> str:
        text = " ".join(str(value or "").split()).strip()
        return text or "-"

    @classmethod
    def _format_multiline_block(cls, value: str) -> str:
        text = value.strip() or "-"
        return text

    @classmethod
    def _format_bullet_block(cls, values: list[str]) -> str:
        cleaned = [cls._format_inline_value(value) for value in values if cls._format_inline_value(value) != "-"]
        if not cleaned:
            return "- none"
        return "\n".join(f"- {value}" for value in cleaned)

    @classmethod
    def _format_thread_context(cls, thread_summary: str) -> str:
        lines = [line.strip() for line in str(thread_summary or "").splitlines() if line.strip()]
        if not lines:
            return "- none"
        normalized: list[str] = []
        for line in lines[:5]:
            normalized.append(line if line.startswith("- ") else f"- {line}")
        return "\n".join(normalized)

    @staticmethod
    def _truncate_text(value: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            return "-"
        collapsed = text
        if len(collapsed) <= limit:
            return collapsed
        if limit <= 240:
            return f"{collapsed[: max(0, limit - 16)].rstrip()} [...]"

        separator = "\n\n[... truncated ...]\n\n"
        available = max(0, limit - len(separator))
        head_length = max(120, int(available * 0.6))
        tail_length = max(80, available - head_length)
        if head_length + tail_length > len(collapsed):
            return collapsed[:limit]
        return f"{collapsed[:head_length].rstrip()}{separator}{collapsed[-tail_length:].lstrip()}"
