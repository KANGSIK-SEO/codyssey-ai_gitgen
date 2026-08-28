# AI Git 커밋 & PR 자동 생성기

OpenAI GPT API로 git diff 기반 커밋 메시지와 PR 초안 생성.

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
echo 'OPENAI_API_KEY="sk-..."' > .env  # 실제 키 사용, Git 커밋 금지
```

프로젝트 `.venv`에 의존성이 설치되어 있으면 시스템 Python으로 실행해도 가상환경으로 자동 전환됩니다.

## 사용

```bash
# 대화형 실행 후 자연어 요청 입력
python main.py

# 커밋 메시지 생성
python main.py commit

# PR 생성: 브랜치 생성, 커밋, push, GitHub PR 생성까지 수행
python main.py pr

# GitHub에 반영하지 않고 제목/본문만 생성
python main.py pr --draft-only

# 안전 모드 (민감정보 마스킹 + diff 제한)
python main.py commit -safe-mode

# 모델/토큰 변경
python main.py commit -model gpt-5-mini -max-tokens 2048
```

인자 없이 실행하면 간단한 자연어 요청을 인식합니다.

```text
요청을 입력하세요 (예: 코드변동사항, PR 만들어줘): 코드변동사항
요청을 입력하세요 (예: 코드변동사항, PR 만들어줘): 커밋 메시지 만들어줘
요청을 입력하세요 (예: 코드변동사항, PR 만들어줘): git 코드 변경했으니 PR시켜
```

- `변동사항`, `변경사항`, `status` 포함: `git status` 출력
- `커밋`, `commit` 포함: 커밋 메시지 생성
- `PR`, `풀리퀘스트`, `pull request` 포함: PR 초안 생성

`pr` 요청은 기본적으로 변경 사항을 새 브랜치에 커밋하고 `origin`에 push한 뒤 `gh pr create`로 GitHub PR을 생성합니다. 초안만 필요하면 `--draft-only`를 사용하세요. GitHub CLI 인증은 `gh auth login -h github.com -w`로 설정합니다.

같은 기능을 `python main.py status` 명령으로 바로 실행할 수도 있습니다.

## 출력 예시

```
[INFO] Git status 수집 완료: 3개 파일 변경 감지
[INFO] Git diff 수집 완료: 128줄
[INFO] AI API 요청 중...
[DONE] 커밋 메시지 생성 완료 (API calls: 1)

────────────────────────────────────────────────────────────
--- Commit Message ---
feat: Git 변경 사항 기반 커밋 메시지 자동 생성 기능 추가

- git diff 결과를 수집해 AI 입력 컨텍스트로 전달
- 커밋 메시지 템플릿(feat/fix 등) 생성 규칙 적용
- API Key 미설정 시 안내 메시지 및 에러 처리
────────────────────────────────────────────────────────────
```

## 핵심 구현 포인트

### API Key 안전 관리
`.env`의 `OPENAI_API_KEY`만 사용하고 코드에는 하드코딩하지 않음. 프로젝트 `.env`가 없으면 `~/Desktop/.env`를 확인하며, 미설정 시 친절한 에러 + exit 2.

### 비용 제어
- 1 명령 = 1 API call (commit/pr 각각)
- 호출 횟수를 출력 로그에 표기
- temperature 기본값 0.3 (안정 + 비용)
- max_tokens 1024 기본 (필요 시 옵션으로 증가)

### 안전 모드 (`-safe-mode`)
- **마스킹**: API key 패턴, 이메일, SSN 등 정규식 기반
- **diff 제한**: 최대 10개 파일, 최대 200줄 (긴 diff로 인한 비용 폭발 방지)

### 출력 검증
- 커밋 제목: 72자 초과 시 자동 절단
- PR 제목: 80자 초과 시 자동 절단
- PR 본문: AI에게 `## Why / ## What / ## How to Test` 헤더와 각 섹션 최소 1불릿 강제 (프롬프트 레벨)

### 프롬프트 설계
- 시스템 메시지로 시니어 엔지니어 페르소나
- Conventional Commits 형식 강제
- "출력은 메시지만, 설명 금지" 명시 → 후처리 비용 감소

## 보너스 — 컨벤션 커스터마이징

`-convention` 옵션으로 프롬프트 일부 교체 (구현 가능): 팀별 prefix 규칙, 톤, 체크리스트 추가 가능.

## 운영 주의

- diff에 시크릿이 포함될 수 있음 → `-safe-mode` 권장
- 결과는 초안일 뿐. 사용자가 검토 후 적용해야 함.
- 1회 실행 시 API 호출 1회로 비용 통제됨.
