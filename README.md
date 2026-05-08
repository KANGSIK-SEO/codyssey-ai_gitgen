# AI Git 커밋 & PR 자동 생성기

Anthropic Claude API로 git diff 기반 커밋 메시지와 PR 초안 생성.

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."  # 코드에 하드코딩 X
```

## 사용

```bash
# 커밋 메시지 생성
python main.py commit

# PR 제목/본문 생성
python main.py pr

# 안전 모드 (민감정보 마스킹 + diff 제한)
python main.py commit -safe-mode

# 모델/온도/토큰 변경
python main.py commit -model claude-sonnet-4-6 -temperature 0.5 -max-tokens 2048
```

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
환경변수만 사용. 코드에 하드코딩 X. 미설정 시 친절한 에러 + exit 2.

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
