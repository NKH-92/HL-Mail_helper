"""Prompt loading and composition."""

from __future__ import annotations

import json
from pathlib import Path

from app.db.models import MailRecord


class PromptManager:
    """Load prompt text files from the portable prompt directory."""

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
        return ""

    def build_system_prompt(self) -> str:
        """Return the shared system prompt."""

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
                "출력은 반드시 JSON 객체 하나만 사용하라. "
                "summary_3lines, evidence, action_type는 배열이며 "
                "classification과 action_owner는 지정 enum만 사용하라. "
                "due_date는 정규화 가능할 때만 넣고, deadline_raw는 원문 표현을 유지하라. "
                "confidence는 0과 1 사이 숫자다."
            ),
        ]
        self._cached_system_prompt_signature = signature
        self._cached_system_prompt = "\n\n".join(part for part in parts if part)
        return self._cached_system_prompt

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
            signature.append((file_name, None))
        return tuple(signature)

    def build_user_prompt(
        self,
        mail: MailRecord,
        thread_summary: str = "",
        model_name: str = "",
        body_char_limit: int = 4000,
        current_user: dict[str, str] | None = None,
    ) -> str:
        """Render structured mail data into the user prompt."""

        if model_name.strip().lower().startswith("gemma-"):
            return self._build_gemma_prompt(
                mail,
                thread_summary,
                body_char_limit=body_char_limit,
                current_user=current_user or {},
            )

        payload = {
            "current_user": {
                "email": (current_user or {}).get("email", ""),
                "display_name": (current_user or {}).get("display_name", ""),
                "department": (current_user or {}).get("department", ""),
                "job_title": (current_user or {}).get("job_title", ""),
            },
            "subject": mail.subject,
            "sender_name": mail.sender_name,
            "sender_email": mail.sender_email,
            "to": mail.to_list,
            "cc": mail.cc_list,
            "received_at": mail.received_at,
            "attachment_names": mail.attachment_names,
            "body_preview": mail.raw_preview[:body_char_limit],
            "thread_summary": thread_summary,
        }
        instructions = {
            "task": "회사 메일을 분석하여 지정 JSON 스키마로 반환",
            "rule": [
                "To/Cc 정보만으로 action_required를 단정하지 말 것",
                "본문과 thread_summary에서 행동 요청, 책임 주체, 기한, 산출물을 확인할 것",
                "근거가 부족하면 classification을 UNCLEAR로 두고 action_required는 false로 둘 것",
                "evidence에는 본문 또는 thread_summary에서 직접 근거가 되는 문장/구절만 넣을 것",
                "mail_action_items와 my_action_items를 분리하고, 추측성 할 일은 만들지 말 것",
                "공유/참고는 FYI 또는 ANNOUNCEMENT로 보수 처리할 것",
            ],
        }
        return json.dumps({"instructions": instructions, "mail": payload}, ensure_ascii=False, indent=2)

    @staticmethod
    def _build_gemma_prompt(
        mail: MailRecord,
        thread_summary: str,
        body_char_limit: int = 4000,
        current_user: dict[str, str] | None = None,
    ) -> str:
        """Return a simpler plain-text prompt for Gemma compatibility mode."""

        user = current_user or {}
        return f"""
        회사 메일을 분석해서 JSON 객체 하나만 출력하라.
        설명문, 머리말, 코드블록, 마크다운을 붙이지 마라.

        반드시 아래 키를 모두 포함하라.
        - category: ACT/FYI/APR/SCH/QLT/ETC 중 하나
        - priority: high/medium/low/unknown 중 하나
        - classification: ACTION_SELF/ACTION_SHARED/APPROVAL_REQUEST/FYI/ANNOUNCEMENT/UNCLEAR 중 하나
        - one_line_summary: 문자열
        - summary_3lines: 문자열 배열 최대 3개
        - mail_action_items: 문자열 배열
        - my_action_required: true 또는 false
        - my_action_status: direct_action/review_needed/reference_only 중 하나
        - my_action_items: 문자열 배열
        - action_owner: me/team/other/unknown 중 하나
        - action_type: reply/review/approve/submit/prepare/attend/monitor/none 중 0개 이상 배열
        - due_date: YYYY-MM-DD 또는 YYYY-MM-DD HH:MM:SS 또는 null
        - deadline_raw: 원문 기한 표현 또는 null
        - evidence: 직접 근거 문장 배열
        - ownership_reason: 문자열 배열
        - reason: 판정 이유 2~3문장
        - suggested_task_title: 할일 제목 또는 null
        - confidence: 0과 1 사이 숫자

        중요 규칙:
        - 수신자 정보만으로 action_required를 단정하지 마라
        - evidence는 반드시 본문 또는 최근 스레드 요약의 직접 문장을 사용하라
        - 근거가 부족하면 classification=UNCLEAR, my_action_required=false로 둬라
        - mail_action_items는 메일 전체에서 누군가 해야 하는 일
        - my_action_items는 그중 사용자가 실제 해야 하는 일
        - 사용자가 CC 수신자면 직접 지목/명시 요청 근거가 없을 때 보수적으로 판단하라
        - 공유/참고는 FYI 또는 ANNOUNCEMENT로 처리하라

        현재 사용자:
        - 이메일: {user.get("email", "-") or "-"}
        - 이름: {user.get("display_name", "-") or "-"}
        - 부서: {user.get("department", "-") or "-"}
        - 직책: {user.get("job_title", "-") or "-"}

        메일 정보:
        제목: {mail.subject}
        발신자명: {mail.sender_name}
        발신자메일: {mail.sender_email}
To: {", ".join(mail.to_list) or "-"}
Cc: {", ".join(mail.cc_list) or "-"}
수신시각: {mail.received_at or "-"}
첨부파일명: {", ".join(mail.attachment_names) or "-"}

최근 스레드 요약:
{thread_summary or "-"}

본문 미리보기:
{mail.raw_preview[:body_char_limit] or "-"}
""".strip()
