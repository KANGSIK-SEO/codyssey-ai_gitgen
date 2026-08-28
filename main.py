"""OpenAI GPT 기반 Git 커밋/PR 자동 생성 CLI.

요구:
- .env의 OPENAI_API_KEY
- pip install -r requirements.txt

사용:
  python main.py commit
  python main.py pr
  python main.py commit --safe-mode
  python main.py  # 프롬프트에서 "코드변동사항" 입력
"""
import argparse
import importlib.util
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_TEMPERATURE = None
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


def git_status_full() -> str:
    return run(["git", "status"])

# 코드를 짧게 해주세요
def git_diff() -> str:
    # staged + unstaged 모두 포함. HEAD 없는 신규 저장소는 staged만.
    try:
        return run(["git", "diff", "HEAD"])#ㄹㄹ
    except RuntimeError:
        # 첫 커밋 전: HEAD 부재. staged 변경만 반환.
        return run(["git", "diff", "--cached"])


def git_branch() -> str:
    return run(["git", "branch", "--show-current"]).strip() or "(no branch)"


def run_visible(cmd: list[str]) -> str:
    """GitHub 반영 명령을 실행하고 실패 내용을 사용자에게 전달."""#ㅂㅂ
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip()
        raise RuntimeError(f"명령 실패: {' '.join(cmd)}\n{detail}")
    return res.stdout.strip()


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


def load_openai_environment() -> Path | None:
    """프로젝트 .env를 우선하고, 사용자의 공용 Desktop .env를 대체 경로로 사용."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("[ERROR] python-dotenv 패키지가 설치되지 않았습니다.")
        print("# 설치: python3 -m pip install -r requirements.txt")
        sys.exit(2)

    candidates = [
        Path(__file__).resolve().parent / ".env",
        Path.cwd() / ".env",
        Path.home() / "Desktop" / ".env",
    ]
    for env_path in dict.fromkeys(candidates):
        if env_path.is_file():
            load_dotenv(env_path, override=False)
            if os.environ.get("OPENAI_API_KEY"):
                return env_path
    return None


def call_openai(prompt: str, model: str, temperature: float | None, max_tokens: int) -> tuple[str, int]:
    env_path = load_openai_environment()
    if not os.environ.get("OPENAI_API_KEY"):
        print("[ERROR] .env에서 OPENAI_API_KEY를 찾지 못했습니다.")
        print('# 예) OPENAI_API_KEY="sk-..."')
        sys.exit(2)

    try:
        import openai
        from openai import OpenAI
    except ImportError:
        print("[ERROR] openai 패키지가 설치되지 않았습니다.")
        print("# 설치: python3 -m pip install -r requirements.txt")
        sys.exit(2)

    print(f"[INFO] 환경 설정 로드: {env_path}")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    try:
        request = dict(
            model=model,
            input=prompt,
            max_output_tokens=max_tokens,
        )
        if temperature is not None:
            request["temperature"] = temperature
        resp = client.responses.create(**request)
    except openai.APIConnectionError as e:
        print(f"[ERROR] 네트워크 연결 실패: {e}")
        sys.exit(2)
    except openai.AuthenticationError:
        print("[ERROR] API Key 인증 실패. OPENAI_API_KEY 값을 확인하세요.")
        sys.exit(2)
    except openai.RateLimitError:
        print("[ERROR] Rate limit 도달. 잠시 후 재시도하세요.")
        sys.exit(2)
    except openai.APIStatusError as e:
        print(f"[ERROR] API 오류 (status={e.status_code}): {e.message}")
        sys.exit(2)

    text = resp.output_text
    if resp.usage:
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
    text, calls = call_openai(
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
    text, calls = call_openai(
        PR_PROMPT.format(branch=branch, status=status, diff=diff),
        args.model, args.temperature, args.max_tokens,
    )
    print(f"[DONE] PR 초안 생성 완료 (API calls: {calls})\n")
    print(format_pr_block(text))
    if not args.draft_only:
        apply_pr_to_github(text)


def split_pr_text(text: str) -> tuple[str, str]:
    lines = text.strip().splitlines()
    title = (lines[0] if lines else "AI generated update")[:80]
    body = "\n".join(lines[1:]).strip()
    return title, body


def apply_pr_to_github(text: str):
    """변경 사항을 커밋/push하고 GitHub PR을 실제 생성."""
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        print("[ERROR] GitHub 로그인이 만료되어 PR을 반영하지 못했습니다.")
        print("# 먼저 실행: gh auth login -h github.com -w")
        return

    title, body = split_pr_text(text)
    branch = git_branch()
    if branch in {"main", "master", "(no branch)"}:
        branch = f"ai-gitgen/update-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        run_visible(["git", "switch", "-c", branch])
        print(f"[INFO] PR 브랜치 생성: {branch}")

    run_visible(["git", "add", "--all"])
    staged = run_visible(["git", "diff", "--cached", "--name-only"])
    if not staged:
        print("[INFO] 커밋할 변경 사항이 없어 GitHub PR 생성을 건너뜁니다.")
        return

    run_visible(["git", "commit", "-m", title])
    print(f"[DONE] Git 커밋 완료: {title}")
    run_visible(["git", "push", "--set-upstream", "origin", branch])
    print(f"[DONE] 원격 브랜치 반영 완료: origin/{branch}")
    pr_url = run_visible(["gh", "pr", "create", "--title", title, "--body", body])
    print(f"[DONE] GitHub PR 생성 완료: {pr_url}")


def cmd_status(_args=None): #ㄴㄴ
    print("[INFO] 현재 저장소의 코드 변동 사항입니다.\n")
    print(git_status_full().rstrip())


def run_interactive_prompt(parser):
    try:
        prompt = input('요청을 입력하세요 (예: 코드변동사항, PR 만들어줘): ').strip()
    except EOFError:
        parser.print_help()
        return

    normalized = re.sub(r"\s+", "", prompt).lower()

    # 더 구체적인 생성 의도를 먼저 판별한다. 예: "코드 변경했으니 PR시켜"
    if "pr" in normalized or "풀리퀘스트" in normalized or "pullrequest" in normalized:
        cmd_pr(parser.parse_args(["pr"]))
        return

    if "커밋" in normalized or "commit" in normalized:
        cmd_commit(parser.parse_args(["commit"]))
        return

    if "변동사항" in normalized or "변경사항" in normalized or "status" in normalized:
        cmd_status()
        return

    print(f"[ERROR] 지원하지 않는 프롬프트입니다: {prompt}")
    print('예시: "코드변동사항", "커밋 메시지 만들어줘", "PR 만들어줘"')


def ensure_project_runtime():
    """전역 의존성이 없으면 설치된 프로젝트 가상환경으로 자동 재실행."""
    if importlib.util.find_spec("openai") and importlib.util.find_spec("dotenv"):
        return

    venv_dir = Path(__file__).resolve().parent / ".venv"
    venv_python = venv_dir / "bin" / "python"
    if venv_python.is_file() and Path(sys.prefix) != venv_dir:
        os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


def main():
    ensure_project_runtime()
    p = argparse.ArgumentParser(prog="ai-gitgen")
    p.add_argument("-model", dest="model", default=DEFAULT_MODEL)
    p.add_argument("-temperature", dest="temperature", type=float, default=DEFAULT_TEMPERATURE)
    p.add_argument("-max-tokens", dest="max_tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("-safe-mode", dest="safe_mode", action="store_true",
                   help="diff에서 민감정보 마스킹 + 파일/줄 제한")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("commit").set_defaults(func=cmd_commit)
    pr_parser = sub.add_parser("pr")
    pr_parser.add_argument("--draft-only", action="store_true",
                           help="GitHub에 반영하지 않고 PR 초안만 출력")
    pr_parser.set_defaults(func=cmd_pr)
    sub.add_parser("status", help="현재 저장소의 git status 출력").set_defaults(func=cmd_status)

    args = p.parse_args()
    try:
        if not args.cmd:
            run_interactive_prompt(p)
            return
        args.func(args)
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
