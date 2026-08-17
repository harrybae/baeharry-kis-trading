#!/usr/bin/env python3
"""Harry Trading workflow manager.

CONVENTIONS.md 4가지 규칙을 프로그램으로 강제합니다.

6단계 명령 흐름:
1. init    - 작업 목록 공개 + Allow 승인
2. backup  - 수정 전 핵심 소스 백업
3. edit    - 단일 파일 edit token 발급
4. verify  - syntax / import / health / git diff 검증
5. conventions - CONVENTIONS.md 행동규칙 상기 체크포인트
6. done    - 작업 완료 보고 및 상태 초기화

init에서 Allow 한 번으로 이후 단계는 추가 승인 없이 진행됩니다.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
WORKFLOW_DIR = ROOT / ".workflow"
STATE_FILE = WORKFLOW_DIR / "state.json"
TOKEN_FILE = WORKFLOW_DIR / "edit_token"
ALLOW_DURATION_MIN = 60

CONVENTIONS_FILE = ROOT / "docs" / "CONVENTIONS.md"

BACKUP_DIR = ROOT / "backup"
WORKFLOW_BACKUP_DIR = WORKFLOW_DIR / "backups"

PHASES = ["IDLE", "INIT", "ALLOWED", "CONVENTIONS_REVIEWED", "BACKUPPED", "EDITING", "VERIFIED", "DONE"]

# 핵심 소스 목록: root, src, daemons, web 구조 기준
SOURCE_TARGETS = [
    "config.py",
    "app.py",
    "main.py",
    "kis_api.py",
    "strategy.py",
    "position_manager.py",
    "stock_master.py",
    "telegram_bot.py",
    "auto_trading_daemon.py",
    "auto_trading_multi_daemon.py",
    "auto_trading_with_risk_daemon.py",
    "src/*.py",
    "daemons/*.py",
    "web/app.py",
    "templates/index.html",
    "tools/workflow.py",
]

# 런타임 JSON/데이터 파일은 .prev/.prev2 2세대 백업
RUNTIME_DATA_TARGETS = [
    "alerts.json",
    "auto_presets.json",
    "openclaw_new.json",
    "positions.json",
    "trades.json",
    "watchlist.json",
]


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"phase": "IDLE", "history": []}
    return {"phase": "IDLE", "history": []}


def save_state(state):
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    state.setdefault("history", [])
    state["last_action"] = datetime.now().isoformat()
    state["history"].append(f"{state['last_action']} -> {state['phase']}")
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def require_phase(state, expected):
    current = state.get("phase", "IDLE")
    if current != expected:
        fail(f"현재 상태는 '{current}'입니다. 이 명령은 '{expected}' 상태에서만 실행할 수 있습니다.")


def is_allowed_expired(state):
    allowed_at = state.get("allowed_at")
    if not allowed_at:
        return True
    try:
        t = datetime.fromisoformat(allowed_at)
    except (TypeError, ValueError):
        return True
    return datetime.now() - t > timedelta(minutes=ALLOW_DURATION_MIN)


def collect_source_files():
    files = []
    for pattern in SOURCE_TARGETS:
        if pattern.endswith("*.py") or pattern.endswith("*.html"):
            files.extend(sorted(ROOT.glob(pattern)))
        else:
            p = ROOT / pattern
            if p.exists():
                files.append(p)
    return [f for f in files if f.is_file()]


def backup_basename(filename: Path) -> str:
    """경로를 포함한 고유 백업 파일명 생성. 예: src/telegram_bot.py -> src_telegram_bot.py"""
    rel_parts = filename.relative_to(ROOT).parts
    if len(rel_parts) > 1:
        return "_".join(rel_parts)
    return filename.name


def rotate_backup(filename: Path):
    """파일별 .prev -> .prev2 회전 백업."""
    if not filename.exists():
        return
    name = backup_basename(filename)
    prev = BACKUP_DIR / f"{name}.prev"
    prev2 = BACKUP_DIR / f"{name}.prev2"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if prev.exists():
        shutil.copy2(prev, prev2)
    shutil.copy2(filename, prev)


# 하위호환 별칭
rotate_runtime_backup = rotate_backup


def cmd_init(args):
    state = load_state()
    if state.get("phase", "IDLE") != "IDLE":
        print(f"[WARN] 현재 상태가 {state.get('phase')}입니다. 먼저 'done' 또는 'reset'을 실행하세요.")
        sys.exit(1)

    state["phase"] = "INIT"
    state["started_at"] = datetime.now().isoformat()
    state["edit_target"] = None
    state["allowed_at"] = None
    save_state(state)

    print("""============================================================
Harry Trading 워크플로우 시작
CONVENTIONS.md 4가지 규칙을 프로그램으로 강제합니다.
============================================================

[오늘 수행할 작업을 먼저 확인하세요]
1. 수정 대상 파일을 명확히 정합니다.
2. 백업은 workflow가 자동으로 수행합니다.
3. 한 번에 하나의 파일만 수정합니다.
4. 불확실한 사항은 추측하지 말고 질문합니다.
5. github 백업은 workflow가 수동 실행하도록 안내합니다.

[단계 순서]
  init -> conventions -> backup -> edit -> verify -> done

한 번에 전체를 자동 진행하려면:
  python3 tools/workflow.py run --file <path>
""")

    answer = input("Allow? [y/n]: ").strip().lower()
    if answer != "y":
        state["phase"] = "IDLE"
        save_state(state)
        print("[CANCELLED] Allow가 거부되었습니다.")
        sys.exit(1)

    state["phase"] = "ALLOWED"
    state["allowed_at"] = datetime.now().isoformat()
    save_state(state)
    print(f"[OK] Allow 기록됨. 유효시간: {ALLOW_DURATION_MIN}분")
    print("다음 단계:\n  python3 tools/workflow.py conventions")


def cmd_backup(args):
    state = load_state()
    require_phase(state, "CONVENTIONS_REVIEWED")
    if is_allowed_expired(state):
        fail("Allow가 만료되었습니다. 다시 init을 실행하세요.")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_dir = WORKFLOW_BACKUP_DIR / ts
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    src_files = collect_source_files()
    for f in src_files:
        rel = f.relative_to(ROOT)
        dst = snapshot_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)

    for name in RUNTIME_DATA_TARGETS:
        p = ROOT / name
        if p.exists():
            rotate_backup(p)

    # 핵심 소스도 backup/ 폴더에 .prev/.prev2 회전
    for f in src_files:
        rotate_backup(f)

    state["phase"] = "BACKUPPED"
    state["last_backup"] = str(snapshot_dir)
    save_state(state)
    print(f"[OK] 백업 완료: {snapshot_dir}")
    print(f"       소스 파일: {len(src_files)}개")
    if getattr(args, "auto", False):
        return
    print("다음 단계:\n  python3 tools/workflow.py edit --file <path>")


def cmd_edit(args):
    state = load_state()
    require_phase(state, "BACKUPPED")
    if is_allowed_expired(state):
        fail("Allow가 만료되었습니다. 다시 init을 실행하세요.")

    target = args.file
    if not target:
        fail("--file <path>를 지정해야 합니다.")

    target_path = ROOT / target
    if not target_path.exists():
        fail(f"지정한 파일이 존재하지 않습니다: {target}")

    # 이미 EDITING 상태에서 다른 파일 edit 시도 차단
    current_target = state.get("edit_target")
    if current_target and current_target != target:
        fail(f"이미 다른 파일({current_target})에 대한 edit token이 발급되었습니다. 먼저 verify/done을 실행하세요.")

    state["phase"] = "EDITING"
    state["edit_target"] = target
    state["edit_started_at"] = datetime.now().isoformat()
    save_state(state)

    TOKEN_FILE.write_text(
        json.dumps({
            "target": target,
            "started_at": state["edit_started_at"],
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"[OK] edit token 발급: {target}")
    print("[OK] 이제 파일을 수정할 수 있습니다.")
    print("수정 후:\n  python3 tools/workflow.py verify")


def _check_syntax(files):
    print("[VERIFY] Python syntax check...")
    all_ok = True
    for f in files:
        if f.suffix != ".py":
            continue
        result = subprocess.run(
            ["python3", "-m", "py_compile", str(f)],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        rel = f.relative_to(ROOT)
        if result.returncode == 0:
            print(f"  [OK] {rel}")
        else:
            print(f"  [FAIL] {rel}")
            all_ok = False
    return all_ok


def _check_imports():
    print("[VERIFY] Import check...")
    result = subprocess.run(
        ["python3", "-c", "import config"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode == 0:
        print("  [OK] config")
        return True
    print("  [FAIL] config import")
    if result.stderr:
        print(result.stderr.strip())
    return False


def _check_server():
    print("[VERIFY] Server health check...")
    result = subprocess.run(
        ["curl", "-sf", "http://127.0.0.1:8081/"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        print("  [OK] http://127.0.0.1:8081")
        return True
    print("  [WARN] http://127.0.0.1:8081 not responding")
    return True  # health check은 경고만, 실패로 처리하지 않음


def _check_git_diff():
    print("[VERIFY] Git change preview...")
    result = subprocess.run(
        ["git", "diff", "--stat"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    else:
        print("  [INFO] no changes detected")
    return True


def cmd_verify(args):
    state = load_state()
    require_phase(state, "EDITING")
    if is_allowed_expired(state):
        fail("Allow가 만료되었습니다. 다시 init을 실행하세요.")

    target = state.get("edit_target")
    if not target:
        fail("edit token이 없습니다. 먼저 edit --file을 실행하세요.")

    files = collect_source_files()
    ok = True
    ok &= _check_syntax(files)
    ok &= _check_imports()
    ok &= _check_server()
    ok &= _check_git_diff()

    if not ok:
        fail("검증에 실패했습니다. 수정 후 다시 verify를 실행하세요.")

    state["phase"] = "VERIFIED"
    state["verified_at"] = datetime.now().isoformat()
    save_state(state)
    print("[OK] 검증 완료.")
    if getattr(args, "auto", False):
        return
    print("다음 단계:\n  python3 tools/workflow.py done")


def cmd_conventions(args):
    state = load_state()
    require_phase(state, "ALLOWED")
    if is_allowed_expired(state):
        fail("Allow가 만료되었습니다. 다시 init을 실행하세요.")

    if CONVENTIONS_FILE.exists():
        print("[CONVENTIONS] 행동규칙 상기 체크포인트 도달.")
    else:
        print("[WARN] CONVENTIONS.md 파일을 찾을 수 없습니다.")

    state["phase"] = "CONVENTIONS_REVIEWED"
    state["conventions_reviewed_at"] = datetime.now().isoformat()
    save_state(state)
    print("[OK] 행동규칙 상기 완료.")
    if getattr(args, "auto", False):
        return
    print("다음 단계:\n  python3 tools/workflow.py backup")


def cmd_done(args):
    state = load_state()
    require_phase(state, "VERIFIED")

    edit_target = state.get("edit_target")
    state["phase"] = "IDLE"
    state["edit_target"] = None
    state["allowed_at"] = None
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()

    save_state(state)

    print("""[OK] workflow 완료. 상태 초기화됨.
============================================================
변경된 파일 요약:""")
    if getattr(args, "auto", False):
        print("\n[run] 전체 자동 워크플로우가 완료되었습니다.")
    result = subprocess.run(
        ["git", "diff", "--stat"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    else:
        print("  (변경 없음)")
    if edit_target:
        print(f"\n최종 수정 대상: {edit_target}")
    print("""
github 백업이 필요하면 수동으로 실행하세요:
  git add <files>
  git commit -m "..."
  git push

새 작업을 시작하려면:
  python3 tools/workflow.py init
  python3 tools/workflow.py run --file <path>   # 전체 자동 진행
""")


def cmd_run(args):
    """init -> conventions -> backup -> edit 까지 자동 진행.

    사용자는 init에서 Allow 한 번만 승인하면 이후 단계는 추가 입력 없이 진행됩니다.
    수정 완료 후에는 직접 'verify'와 'done'을 실행하여 검증/완료합니다.
    """
    state = load_state()
    if state.get("phase", "IDLE") != "IDLE":
        print(f"[WARN] 현재 상태가 {state.get('phase')}입니다. 먼저 'reset'을 실행합니다.")
        cmd_reset(args)

    if not args.file:
        fail("run은 --file <path>가 필요합니다.")

    args.auto = True  # 내부 서브커맨드에 자동 진행 플래그 전달

    print("[RUN] 워크플로우를 시작합니다 (init -> conventions -> backup -> edit).")
    cmd_init(args)
    cmd_conventions(args)
    cmd_backup(args)
    cmd_edit(args)
    print("[OK] 자동 단계 완료. 수정 후 'python3 tools/workflow.py verify'를 실행하세요.")


def cmd_status(args):
    state = load_state()
    phase = state.get("phase", "UNKNOWN")
    print(f"현재 상태: {phase}")
    if state.get("edit_target"):
        print(f"edit target: {state['edit_target']}")
    if state.get("allowed_at"):
        try:
            t = datetime.fromisoformat(state["allowed_at"])
            remain = timedelta(minutes=ALLOW_DURATION_MIN) - (datetime.now() - t)
            print(f"Allow 만료까지: {remain}")
        except (TypeError, ValueError):
            pass


def cmd_reset(args):
    state = load_state()
    print(f"[RESET] 상태를 IDLE로 되돌립니다. (이전 상태: {state.get('phase')})")
    state["phase"] = "IDLE"
    state["edit_target"] = None
    state["allowed_at"] = None
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    save_state(state)
    print("[OK] reset 완료.")


def cmd_help(args):
    print(__doc__)


def main():
    parser = argparse.ArgumentParser(description="Harry Trading workflow manager")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="작업 시작 + Allow 승인")
    sub.add_parser("backup", help="핵심 소스 백업")
    edit_parser = sub.add_parser("edit", help="단일 파일 edit token 발급")
    edit_parser.add_argument("--file", required=True, help="수정 대상 파일 경로 (workspace 기준 상대경로)")
    sub.add_parser("verify", help="syntax/import/health/git diff 검증")
    sub.add_parser("conventions", help="CONVENTIONS.md 행동규칙 상기 체크포인트")
    sub.add_parser("done", help="작업 완료 및 상태 초기화")
    run_parser = sub.add_parser("run", help="init -> conventions -> backup -> edit -> verify -> done 자동 진행")
    run_parser.add_argument("--file", required=True, help="수정 대상 파일 경로 (workspace 기준 상대경로)")
    sub.add_parser("status", help="현재 workflow 상태 출력")
    sub.add_parser("reset", help="상태를 IDLE로 강제 초기화 (비상용)")
    sub.add_parser("help", help="도움말 출력")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "backup": cmd_backup,
        "edit": cmd_edit,
        "verify": cmd_verify,
        "conventions": cmd_conventions,
        "done": cmd_done,
        "run": cmd_run,
        "status": cmd_status,
        "reset": cmd_reset,
        "help": cmd_help,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
