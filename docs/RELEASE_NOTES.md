# Release Notes

## v0.1.0 - 2026-03-19

첫 공개 업로드 버전입니다.

### 포함 내용

- 데스크톱 중심 메일 워크스페이스 공개
- 우선순위 스레드 + 내 액션 보드 중심 대시보드
- Hanlim OpenAI-compatible LLM provider 기본 지원
- 백그라운드 동기화와 실시간 진행 상태 반영
- 포터블 빌드 스크립트 포함
- 사용자 매뉴얼 및 아키텍처 문서 정리

### 안정화 반영

- 수동 동기화와 자동 동기화의 동시 실행 방지
- IMAP fetch 실패 시 backfill cursor 오염 방지
- 실시간 폴링의 stale 응답이 최신 UI 상태를 덮어쓰지 않도록 보정
- thread overview cache의 concurrent invalidation 경합 완화

### 검증

- `python -m unittest discover -s tests`
- `node --check app/ui/custom_board/ui_patch.js`
- `python -m py_compile ...`

### 주의 사항

- 실제 사용자 설정 파일과 로컬 주소록, 로그, 캐시, DB는 저장소에 포함되지 않습니다.
- 비밀번호와 API 키는 OS keyring에 저장됩니다.
