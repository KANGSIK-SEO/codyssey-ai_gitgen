"""AI 기반 Git 커밋/PR 자동 생성 CLI.

요구:
- ANTHROPIC_API_KEY 환경변수
- pip install anthropic

사용:
  python main.py commit
  python main.py pr
  python main.py commit --safe-mode
"""
import argparse
import os
import re
import subprocess
import sys
import textwrap

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 1024
MAX_FILES = 10
MAX_LINES = 200
SEP = "─" * 60


# ========== Git 수집 ==========
def run(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git failed: {' '.join(cmd)}\n{res.stderr}")
    return res.stdout


def git_status() -> str:
    return run(["git", "status", "--short"])


def git_diff() -> str:
    # staged + unstaged 모두 포함. HEAD 없는 신규 저장소는 staged만.
    try:
        return run(["git", "diff", "HEAD"])
    except RuntimeError:
        # 첫 커밋 전: HEAD 부재. staged 변경만 반환.
        return run(["git", "diff", "--cached"])


def git_branch() -> str:
    return run(["git", "branch", "--show-current"]).strip() or "(no branch)"


# ========== 안전 모드 ==========
SECRET_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[\w\-\.]{8,}"), "[REDACTED_KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
]


def mask_secrets(text: str) -> str:
    out = text
    for pat, repl in SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out


def truncate_diff(diff: str, max_files: int = MAX_FILES, max_lines: int = MAX_LINES) -> tuple[str, int]:
    """안전 모드: 파일 N개 + 줄 M줄로 자름."""
    files = []
    cur = []
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            if cur:
                files.append("\n".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        files.append("\n".join(cur))
    files = files[:max_files]
    truncated = "\n".join(files).splitlines()[:max_lines]
    return "\n".join(truncated), len(truncated)


# ========== AI 호출 ==========
COMMIT_PROMPT = """당신은 시니어 소프트웨어 엔지니어입니다. 아래 git 변경사항을 보고 한국어로 좋은 커밋 메시지를 작성하세요.

규칙:
- 1줄 제목 (50자 이내 권장, 최대 72자). Conventional Commits 형식 (feat/fix/docs/refactor/test/chore).
- 빈 줄 후 본문 (선택). 본문에는 변경된 파일/모듈 1~3개 또는 핵심 변경 사항 1~2개를 불릿으로.
- 출력은 메시지만. 설명 금지.

[Git Status]
{status}

[Git Diff]
{diff}
"""

PR_PROMPT = """당신은 시니어 소프트웨어 엔지니어입니다. 아래 git 변경사항으로 한국어 PR 초안을 작성하세요.

규칙:
- 첫 줄: PR 제목 (80자 이내). Conventional Commits 형식.
- 빈 줄 후 본문. 본문은 정확히 다음 3개 섹션 헤더를 포함하고, 각 섹션에 최소 1개 불릿:
  ## Why
  - ...
  ## What
  - ...
  ## How to Test
  - ...
- 출력은 PR 텍스트만. 설명 금지.

[Branch] {branch}

[Git Status]
{status}

[Git Diff]
{diff}
"""


def call_anthropic(prompt: str, model: str, temperature: float, max_tokens: int) -> tuple[str, int]:
    try:
        import anthropic
    except ImportError:
        print("[ERROR] anthropic 패키지가 설치되지 않았습니다. pip install anthropic")
        sys.exit(2)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[ERROR] ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
        print("# 예) export ANTHROPIC_API_KEY=\"sk-ant-...\"")
        sys.exit(2)

    client = anthropic.Anthropic()
    try:
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIConnectionError as e:
        print(f"[ERROR] 네트워크 연결 실패: {e}")
        sys.exit(2)
    except anthropic.AuthenticationError:
        print("[ERROR] API Key 인증 실패. ANTHROPIC_API_KEY 값을 확인하세요.")
        sys.exit(2)
    except anthropic.RateLimitError:
        print("[ERROR] Rate limit 도달. 잠시 후 재시도하세요.")
        sys.exit(2)
    except anthropic.APIStatusError as e:
        print(f"[ERROR] API 오류 (status={e.status_code}): {e.message}")
        sys.exit(2)

    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    # 호출 횟수: 이 함수 1회 호출당 1회. 토큰 사용량도 함께 노출.
    if hasattr(resp, "usage"):
        print(f"[INFO] Tokens — input: {resp.usage.input_tokens}, output: {resp.usage.output_tokens}")
    return text.strip(), 1


# ========== 출력 포맷 ==========
def format_commit_block(text: str) -> str:
    lines = text.splitlines()
    title = lines[0] if lines else ""
    if len(title) > 72:
        title = title[:69] + "..."
        lines[0] = title
    body = "\n".join(lines)
    return f"{SEP}\n--- Commit Message ---\n{body}\n{SEP}"


def format_pr_block(text: str) -> str:
    lines = text.splitlines()
    title = lines[0] if lines else ""
    if len(title) > 80:
        title = title[:77] + "..."
        lines[0] = title
    body_lines = lines[1:] if len(lines) > 1 else []
    body = "\n".join(body_lines).strip()
    out = []
    out.append(SEP)
    out.append("--- PR Title ---")
    out.append(title)
    out.append("")
    out.append("--- PR Body ---")
    out.append(body)
    out.append(SEP)
    return "\n".join(out)


# ========== 메인 ==========
def cmd_commit(args):
    status = git_status()
    if not status.strip():
        print("[INFO] 변경 사항이 없습니다. 커밋 메시지를 생성하지 않고 종료합니다.")
        sys.exit(0)
    diff = git_diff()
    print(f"[INFO] Git status 수집 완료: {status.count(chr(10))}개 파일 변경 감지")
    print(f"[INFO] Git diff 수집 완료: {diff.count(chr(10))}줄")

    if args.safe_mode:
        diff, n = truncate_diff(diff)
        diff = mask_secrets(diff)
        print(f"[INFO] safe-mode: diff {n}줄로 잘라 보냅니다 (마스킹 적용).")

    print("[INFO] AI API 요청 중...")
    text, calls = call_anthropic(
        COMMIT_PROMPT.format(status=status, diff=diff),
        args.model, args.temperature, args.max_tokens,
    )
    print(f"[DONE] 커밋 메시지 생성 완료 (API calls: {calls})\n")
    print(format_commit_block(text))


def cmd_pr(args):
    status = git_status()
    diff = git_diff()
    if not diff.strip() and not status.strip():
        print("[INFO] 변경 사항이 없습니다.")
        sys.exit(0)
    branch = git_branch()
    print(f"[INFO] 현재 브랜치: {branch}")

    if args.safe_mode:
        diff, n = truncate_diff(diff)
        diff = mask_secrets(diff)
        print(f"[INFO] safe-mode: diff {n}줄로 잘라 보냅니다 (마스킹 적용).")

    print("[INFO] AI API 요청 중...")
    text, calls = call_anthropic(
        PR_PROMPT.format(branch=branch, status=status, diff=diff),
        args.model, args.temperature, args.max_tokens,
    )
    print(f"[DONE] PR 초안 생성 완료 (API calls: {calls})\n")
    print(format_pr_block(text))


def main():
    p = argparse.ArgumentParser(prog="ai-gitgen")
    p.add_argument("-model", dest="model", default=DEFAULT_MODEL)
    p.add_argument("-temperature", dest="temperature", type=float, default=DEFAULT_TEMPERATURE)
    p.add_argument("-max-tokens", dest="max_tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("-safe-mode", dest="safe_mode", action="store_true",
                   help="diff에서 민감정보 마스킹 + 파일/줄 제한")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("commit").set_defaults(func=cmd_commit)
    sub.add_parser("pr").set_defaults(func=cmd_pr)

    args = p.parse_args()
    try:
        args.func(args)
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
