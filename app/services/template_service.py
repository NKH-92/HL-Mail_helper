"""Template CRUD helpers."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.db.models import SendTemplate
from app.db.repositories import TemplateRepository


class TemplateService:
    """Manage template persistence."""

    def __init__(self, template_repository: TemplateRepository, portable_root: Path) -> None:
        self.template_repository = template_repository
        self.portable_root = portable_root
        self.attachments_root = portable_root / "templates" / "attachments"
        self.attachments_root.mkdir(parents=True, exist_ok=True)

    def save_template(self, template: SendTemplate) -> int:
        """Create or update a send template."""

        template_id = template.id
        created_template_id = False
        try:
            if template_id is None:
                initial_template = SendTemplate(
                    id=None,
                    template_name=template.template_name,
                    subject=template.subject,
                    body=template.body,
                    to_list=template.to_list,
                    cc_list=template.cc_list,
                    attachment_paths=[],
                    repeat_type=template.repeat_type,
                    send_time=template.send_time,
                    first_send_at=template.first_send_at,
                    enabled=template.enabled,
                )
                template_id = self.template_repository.upsert(initial_template)
                created_template_id = True
            attachment_paths = self._materialize_attachment_paths(template.attachment_paths, template_id)
            finalized_template = SendTemplate(
                id=template_id,
                template_name=template.template_name,
                subject=template.subject,
                body=template.body,
                to_list=template.to_list,
                cc_list=template.cc_list,
                attachment_paths=attachment_paths,
                repeat_type=template.repeat_type,
                send_time=template.send_time,
                first_send_at=template.first_send_at,
                enabled=template.enabled,
            )
            return self.template_repository.upsert(finalized_template)
        except Exception:
            if created_template_id and template_id is not None:
                self.template_repository.delete(template_id)
                shutil.rmtree(self.attachments_root / f"template_{template_id}", ignore_errors=True)
            raise

    def list_templates(self) -> list[SendTemplate]:
        """Return all templates."""

        return self.template_repository.list_all()

    def get_template(self, template_id: int) -> SendTemplate | None:
        """Return one template."""

        return self.template_repository.get(template_id)

    def set_enabled(self, template_id: int, enabled: bool) -> None:
        """Enable or disable one template."""

        self.template_repository.set_enabled(template_id, enabled)

    def delete_template(self, template_id: int) -> None:
        """Delete one template."""

        self.template_repository.delete(template_id)
        shutil.rmtree(self.attachments_root / f"template_{template_id}", ignore_errors=True)

    def _materialize_attachment_paths(self, attachment_paths: list[str], template_id: int) -> list[str]:
        """Copy selected files into template-owned storage and store relative paths."""

        target_dir = self.attachments_root / f"template_{template_id}"
        staging_dir = self.attachments_root / f".template_{template_id}.tmp"
        if not attachment_paths:
            shutil.rmtree(target_dir, ignore_errors=True)
            shutil.rmtree(staging_dir, ignore_errors=True)
            return []

        prepared_sources: list[tuple[Path, str]] = []
        for index, path_value in enumerate(attachment_paths, start=1):
            source_path = self._resolve_input_path(path_value)
            if not source_path.exists() or not source_path.is_file():
                raise FileNotFoundError(f"첨부파일을 찾을 수 없습니다: {source_path}")

            target_name = source_path.name if self._is_within_directory(source_path, target_dir) else f"{index:02d}_{source_path.name}"
            prepared_sources.append((source_path, target_name))

        shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        normalized_paths: list[str] = []
        try:
            for source_path, target_name in prepared_sources:
                staged_target = staging_dir / target_name
                shutil.copy2(source_path, staged_target)
                normalized_paths.append((target_dir / target_name).relative_to(self.portable_root).as_posix())
            shutil.rmtree(target_dir, ignore_errors=True)
            staging_dir.replace(target_dir)
            return normalized_paths
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

    def _resolve_input_path(self, path_value: str) -> Path:
        path = Path(path_value).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (self.portable_root / path).resolve()

    @staticmethod
    def _is_within_directory(path: Path, directory: Path) -> bool:
        try:
            path.relative_to(directory.resolve())
            return True
        except ValueError:
            return False
