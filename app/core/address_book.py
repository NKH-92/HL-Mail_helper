"""Address book loading and recipient resolution helpers."""

from __future__ import annotations

import csv
import re
import shutil
from dataclasses import asdict, dataclass, replace
from email.utils import parseaddr
from pathlib import Path
from typing import Iterable

from app.core.config_manager import AppConfig, ConfigManager


ADDRESS_BOOK_ENCODINGS = ("utf-8-sig", "cp949", "euc-kr", "utf-8")
NAME_HEADERS = ("이름", "성명", "name", "display_name")
DEPARTMENT_HEADERS = ("부서", "소속", "department", "team")
TITLE_HEADERS = ("직위", "직책", "title", "position")
EMAIL_HEADERS = ("e-메일주소", "이메일", "메일주소", "email", "e-mail", "mail")
MOBILE_HEADERS = ("휴대전화", "휴대폰", "mobile")
PHONE_HEADERS = ("회사전화", "전화", "phone")
COMPANY_HEADERS = ("회사", "company")
ALIASES_HEADERS = ("별칭메일", "추가메일", "aliases", "alias_emails", "alternate_emails")
GROUP_ALIASES_HEADERS = ("그룹메일", "부서메일", "group_aliases", "owned_groups", "team_aliases", "shared_mailboxes")


@dataclass(frozen=True, slots=True)
class AddressBookEntry:
    name: str
    email: str
    department: str = ""
    title: str = ""
    company: str = ""
    mobile_phone: str = ""
    office_phone: str = ""
    aliases: tuple[str, ...] = ()
    group_aliases: tuple[str, ...] = ()

    @property
    def display_label(self) -> str:
        meta = " / ".join(part for part in [self.department, self.title] if part)
        if meta:
            return f"{self.name} / {meta} <{self.email}>"
        return f"{self.name} <{self.email}>"


@dataclass(frozen=True, slots=True)
class RecipientRoutingProfile:
    direct_addresses: tuple[str, ...] = ()
    cc_only_addresses: tuple[str, ...] = ()


class AddressBookService:
    """Load contacts from portable CSV files and resolve emails on demand."""

    def __init__(
        self,
        data_root: Path,
        bundle_root: Path | None = None,
        *,
        addressbook_subdir: str | Path = "addressbook",
    ) -> None:
        self.data_root = data_root
        self.bundle_root = bundle_root
        self.addressbook_root = data_root / Path(addressbook_subdir)
        self.addressbook_root.mkdir(parents=True, exist_ok=True)
        self._cached_file_key: tuple[str, int] | None = None
        self._cached_entries: list[AddressBookEntry] = []
        self._cached_entries_by_email: dict[str, AddressBookEntry] = {}
        self._cached_contact_options_limit: int | None = None
        self._cached_contact_options: list[dict[str, str]] = []
        self._bootstrap_addressbook_files()

    def list_entries(self) -> list[AddressBookEntry]:
        latest_file = self._latest_csv_file()
        if latest_file is None:
            self._cached_file_key = None
            self._cached_entries = []
            self._cached_entries_by_email = {}
            self._cached_contact_options_limit = None
            self._cached_contact_options = []
            return []

        file_key = (str(latest_file.resolve()), latest_file.stat().st_mtime_ns)
        if self._cached_file_key == file_key:
            return list(self._cached_entries)

        self._cached_entries = self._read_entries(latest_file)
        self._cached_file_key = file_key
        self._cached_entries_by_email = {entry.email: entry for entry in self._cached_entries if entry.email}
        self._cached_contact_options_limit = None
        self._cached_contact_options = []
        return list(self._cached_entries)

    def list_contact_options(self, limit: int = 1000) -> list[dict[str, str]]:
        self.list_entries()
        if self._cached_contact_options_limit == limit and self._cached_contact_options:
            return [dict(option) for option in self._cached_contact_options]

        options: list[dict[str, str]] = []
        for entry in self._cached_entries[:limit]:
            search_blob = " ".join(
                part for part in [entry.name, entry.email, entry.department, entry.title, entry.company] if part
            ).lower()
            options.append(
                {
                    "name": entry.name,
                    "email": entry.email,
                    "department": entry.department,
                    "title": entry.title,
                    "company": entry.company,
                    "label": entry.display_label,
                    "search": search_blob,
                }
            )
        self._cached_contact_options_limit = limit
        self._cached_contact_options = options
        return [dict(option) for option in options]

    def get_contact(self, email: str | None) -> AddressBookEntry | None:
        if not email:
            return None
        normalized_email = email.strip().lower()
        if not normalized_email:
            return None
        self.list_entries()
        return self._cached_entries_by_email.get(normalized_email)

    def merge_config_profile(self, config: AppConfig) -> AppConfig:
        contact = self.get_contact(config.user_email)
        if contact is None:
            return config.normalized()

        merged = replace(
            config,
            user_display_name=contact.name or config.user_display_name,
            user_department=contact.department or config.user_department,
            user_job_title=contact.title or config.user_job_title,
        )
        return merged.normalized()

    def sync_user_profile(self, config_manager: ConfigManager) -> AppConfig:
        current = config_manager.load()
        merged = self.merge_config_profile(current)
        if asdict(current) != asdict(merged):
            config_manager.save(merged)
        return merged

    def resolve_display_name(self, email: str | None, fallback_name: str | None = None) -> str:
        contact = self.get_contact(email)
        if contact and contact.name:
            return contact.name
        return (fallback_name or email or "").strip() or "-"

    def resolve_department_title(self, email: str | None) -> tuple[str, str]:
        contact = self.get_contact(email)
        if contact is None:
            return "", ""
        return contact.department, contact.title

    def resolve_user_routing_profile(self, config: AppConfig) -> RecipientRoutingProfile:
        """Return recipient addresses that should count as TO vs CC for the current user."""

        self.list_entries()
        direct_addresses: list[str] = []
        cc_only_addresses: list[str] = []
        self._append_entry_routing_addresses(
            direct_addresses,
            cc_only_addresses,
            AddressBookEntry(
                name=config.user_display_name,
                email=config.user_email,
                department=config.user_department,
                title=config.user_job_title,
            ),
        )

        contact = self.get_contact(config.user_email)
        if contact is not None:
            self._append_entry_routing_addresses(direct_addresses, cc_only_addresses, contact)

        normalized_name = _normalize_key(contact.name if contact else config.user_display_name)
        normalized_department = _normalize_key(contact.department if contact else config.user_department)
        normalized_title = _normalize_key(contact.title if contact else config.user_job_title)

        for entry in self._cached_entries:
            if entry.email == (contact.email if contact else config.user_email):
                continue
            if not normalized_name or _normalize_key(entry.name) != normalized_name:
                continue
            if normalized_department and _normalize_key(entry.department) != normalized_department:
                continue
            if normalized_title and _normalize_key(entry.title) != normalized_title:
                continue
            self._append_entry_routing_addresses(direct_addresses, cc_only_addresses, entry)
        return RecipientRoutingProfile(
            direct_addresses=tuple(direct_addresses),
            cc_only_addresses=tuple(cc_only_addresses),
        )

    def resolve_user_address_aliases(self, config: AppConfig) -> list[str]:
        """Backward-compatible flattened address list for the current user."""

        profile = self.resolve_user_routing_profile(config)
        resolved = list(profile.direct_addresses)
        for address in profile.cc_only_addresses:
            if address not in resolved:
                resolved.append(address)
        return resolved

    def resolve_recipient_tokens(self, raw_value: str) -> list[str]:
        resolved: list[str] = []
        errors: list[str] = []

        for token in _split_recipient_tokens(raw_value):
            try:
                email = self._resolve_token_to_email(token)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if email and email not in resolved:
                resolved.append(email)

        if errors:
            raise ValueError(" / ".join(errors))
        return resolved

    def _resolve_token_to_email(self, token: str) -> str:
        raw_token = token.strip()
        if not raw_token:
            raise ValueError("비어 있는 수신자 항목이 있습니다.")

        _, parsed_email = parseaddr(raw_token)
        if parsed_email and "@" in parsed_email:
            return parsed_email.strip().lower()

        if "@" in raw_token:
            return raw_token.strip().lower()

        matches = [entry for entry in self.list_entries() if _normalize_key(entry.name) == _normalize_key(raw_token)]
        if not matches:
            raise ValueError(f"주소록에서 '{raw_token}' 이름을 찾지 못했습니다.")
        if len(matches) > 1:
            labels = ", ".join(match.display_label for match in matches[:3])
            raise ValueError(f"'{raw_token}' 이름이 여러 명입니다. 목록에서 정확한 주소를 선택해 주세요: {labels}")
        return matches[0].email

    def _bootstrap_addressbook_files(self) -> None:
        if any(self.addressbook_root.glob("*.csv")):
            return

        for source_path in self._default_source_csvs():
            target_path = self.addressbook_root / source_path.name
            if source_path.resolve() == target_path.resolve():
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)

    def _default_source_csvs(self) -> list[Path]:
        candidates: list[Path] = []
        if self.bundle_root is not None:
            bundle_candidates = [
                self.bundle_root / "addressbook",
                self.bundle_root,
            ]
            for root in bundle_candidates:
                if not root.exists():
                    continue
                candidates.extend(sorted(root.glob("*.csv")))
                candidates.extend(sorted(root.glob("*주소록*.csv")))

        unique_candidates: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            try:
                resolved = str(candidate.resolve())
            except OSError:
                resolved = str(candidate)
            if resolved in seen or not candidate.is_file():
                continue
            seen.add(resolved)
            unique_candidates.append(candidate)
        return unique_candidates

    def _latest_csv_file(self) -> Path | None:
        csv_files = [path for path in self.addressbook_root.glob("*.csv") if path.is_file()]
        if not csv_files:
            return None
        return max(csv_files, key=lambda path: (path.stat().st_mtime_ns, path.name.lower()))

    def _read_entries(self, csv_path: Path) -> list[AddressBookEntry]:
        rows = _read_csv_rows(csv_path)
        entries_by_email: dict[str, AddressBookEntry] = {}

        for row in rows:
            email = _row_value(row, EMAIL_HEADERS).lower()
            if not email:
                continue
            entry = AddressBookEntry(
                name=_row_value(row, NAME_HEADERS) or email,
                email=email,
                department=_row_value(row, DEPARTMENT_HEADERS),
                title=_row_value(row, TITLE_HEADERS),
                company=_row_value(row, COMPANY_HEADERS),
                mobile_phone=_row_value(row, MOBILE_HEADERS),
                office_phone=_row_value(row, PHONE_HEADERS),
                aliases=tuple(_split_alias_tokens(_row_value(row, ALIASES_HEADERS))),
                group_aliases=tuple(_split_alias_tokens(_row_value(row, GROUP_ALIASES_HEADERS))),
            )
            entries_by_email[email] = entry

        return sorted(entries_by_email.values(), key=lambda entry: (entry.name, entry.email))

    @staticmethod
    def _append_entry_routing_addresses(
        direct_addresses: list[str],
        cc_only_addresses: list[str],
        entry: AddressBookEntry,
    ) -> None:
        for address in (entry.email, *entry.aliases):
            normalized = _normalize_email(address)
            if normalized and normalized not in direct_addresses:
                direct_addresses.append(normalized)
        for address in entry.group_aliases:
            normalized = _normalize_email(address)
            if normalized and normalized not in direct_addresses and normalized not in cc_only_addresses:
                cc_only_addresses.append(normalized)


def _read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    last_error: Exception | None = None
    for encoding in ADDRESS_BOOK_ENCODINGS:
        try:
            with csv_path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                return [_normalize_row(row) for row in reader if row]
        except UnicodeDecodeError as exc:
            last_error = exc
        except OSError as exc:
            last_error = exc
            break
    if last_error is not None:
        raise last_error
    return []


def _normalize_row(row: dict[str, str | None]) -> dict[str, str]:
    return {str(key).strip(): str(value or "").strip() for key, value in row.items() if key is not None}


def _row_value(row: dict[str, str], headers: Iterable[str]) -> str:
    normalized_row = {_normalize_key(key): value for key, value in row.items()}
    for header in headers:
        value = normalized_row.get(_normalize_key(header), "").strip()
        if value:
            return value
    return ""


def _normalize_key(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


def _normalize_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def _split_recipient_tokens(raw_value: str) -> list[str]:
    return [token.strip() for token in re.split(r"[,;\n]+", raw_value or "") if token.strip()]


def _split_alias_tokens(raw_value: str) -> list[str]:
    resolved: list[str] = []
    for token in _split_recipient_tokens(raw_value):
        _, parsed_email = parseaddr(token)
        normalized = _normalize_email(parsed_email or token)
        if normalized and "@" in normalized and normalized not in resolved:
            resolved.append(normalized)
    return resolved
