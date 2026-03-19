# HL-Mail_helper

사내 메일을 로컬 환경에서 동기화하고, AI로 요약/분류/액션 추출을 수행한 뒤
`우선순위 스레드`와 `내 액션 보드` 중심으로 보여주는 포터블 메일 워크스페이스입니다.

기본 실행 경로는 데스크톱 앱이며, Streamlit 경로는 호환/디버그용으로 유지됩니다.

## 주요 기능

- IMAP 메일 동기화 및 로컬 SQLite 저장
- AI 기반 메일 요약, 중요도 판별, 후속조치 추출
- 우선순위 스레드 정리 및 내 액션 보드 제공
- 예약 발송 및 반복 발송
- 포터블 빌드 생성

## 실행 방법

### 1. 의존성 설치

```powershell
pip install -r requirements.txt
```

### 2. 데스크톱 앱 실행

```powershell
python run_portable.py
```

### 3. Streamlit 호환 경로 실행

```powershell
streamlit run app/main.py
```

## 초기 설정

실제 사용자 설정 파일은 저장소에 포함되지 않습니다.

- 예시 파일: [config/settings.example.json](config/settings.example.json)
- 실제 사용 시 생성되는 파일: `config/settings.json`
- 메일 비밀번호 및 API 키는 파일이 아니라 OS keyring에 저장됩니다.

현재 기본 AI 연동 대상은 Hanlim OpenAI-compatible API입니다.

## 검증 명령

기본 검증:

```powershell
python -m unittest discover -s tests
```

프런트 JS 문법 확인:

```powershell
node --check app/ui/custom_board/ui_patch.js
```

패키징 확인:

```powershell
python build/build_portable.py
```

## 빌드

```powershell
python build/build_portable.py
```

빌드 결과물은 `release/` 아래에 생성됩니다.

## 저장소 구조

- `run_portable.py`: 기본 데스크톱 런처
- `app/runtime_context.py`: 공용 서비스 초기화
- `app/ai/`: AI 프롬프트, 정규화, 소유자 판정
- `app/core/`: 설정, 보안, 스케줄러, 공용 유틸
- `app/db/`: SQLite 스키마, 모델, repository
- `app/mail/`: IMAP/SMTP 연동
- `app/services/`: 동기화, 분석, 발송 orchestration
- `app/ui/`: 데스크톱 브리지, Streamlit 어댑터, UI 상태 helper
- `app/ui/custom_board/`: 메인 HTML/CSS/JS 프런트엔드
- `build/`: 포터블 패키징 스크립트
- `docs/`: 아키텍처 문서와 사용자 매뉴얼
- `tests/`: 자동화 테스트

## 문서

- 아키텍처: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 사용자 매뉴얼: [docs/USER_MANUAL.md](docs/USER_MANUAL.md)
- 릴리즈 노트: [docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md)

## 저장소에 포함되지 않는 항목

다음 항목은 로컬 런타임/사용자 데이터이므로 Git에서 제외됩니다.

- `config/settings.json`
- `addressbook/`
- `cache/`
- `data/`
- `logs/`
- `dist/`
- `release/`
- `build/MailAI_Portable/`

## 참고

- 자동 동기화와 예약 발송은 앱이 실행 중일 때만 동작합니다.
- 대시보드 payload 변경 시에는 `app/ui/ui_state_helpers.py`를 먼저 수정한 뒤 데스크톱/Streamlit/UI를 함께 맞춰야 합니다.
