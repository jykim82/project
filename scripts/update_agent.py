#!/usr/bin/env python3
"""
업데이트 에이전트 (호스트 실행) — docs/module-version-spec.md P3

컨테이너는 자신을 교체할 수 없으므로, 승인된 번들의 실제 적용·롤백은
이 스크립트가 호스트에서 수행한다.

동작:
  files/updates/jobs/<id>.apply.json | <id>.rollback.json  (백엔드가 스풀)
    → 적용: 대상 백업 → 아티팩트 배치 → sha256 재검증 → 마이그레이션 적용
            → health 검사 → 실패 시 백업 자동 복원
    → 롤백: 적용 시점 백업으로 복원 (DB 마이그레이션은 복원하지 않는다 —
            데이터 손실 위험, 롤백 블록 수동 실행이 원칙)
  결과는 jobs/<id>.result.json — 백엔드가 목록 조회 시 DB 로 병합.

사용:
  python3 scripts/update_agent.py --once          # 스풀 1회 처리
  python3 scripts/update_agent.py --loop          # 10초 간격 상주
옵션:
  --files-dir  (기본 <repo>/files)  --health-url (기본 http://localhost:8000)
  --db-container (기본 slm-timescaledb)

안전 규칙:
  - 아티팩트 target_dir 는 files/ 하위 상대경로만 허용 (절대경로·'..' 거부)
  - kind=container 는 이미지 tar(docker load)+compose 재기동 — 이미지 번들이
    실제 확보될 때 검증 예정 (미검증 경고 출력)
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_rel(p: str) -> bool:
    return bool(p) and not p.startswith("/") and ".." not in p.split("/")


def health_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/system/modules", timeout=8) as r:
            return json.load(r).get("status") == "OK"
    except Exception:
        return False


def psql_apply(db_container: str, sql_path: Path) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["docker", "exec", "-i", db_container,
             "psql", "-U", "slm_dev", "-d", "slm", "-v", "ON_ERROR_STOP=1"],
            stdin=open(sql_path, "rb"), capture_output=True, timeout=300,
        )
        out = (r.stdout + r.stderr).decode(errors="replace")[-400:]
        return r.returncode == 0, out
    except Exception as e:
        return False, str(e)


def stamp_version(db_container: str, module_key: str, version: str) -> None:
    sql = (
        "INSERT INTO tb_module_version (module_key, name, kind, version, installed_by) "
        f"VALUES ('{module_key}', '{module_key}', 'bundle', '{version}', 'update_agent') "
        "ON CONFLICT (region, module_key) DO UPDATE SET "
        f"version='{version}', installed_at=now(), installed_by='update_agent', updated_at=now()"
    )
    subprocess.run(
        ["docker", "exec", db_container, "psql", "-U", "slm_dev", "-d", "slm",
         "-c", sql],
        capture_output=True, timeout=30,
    )


def apply_bundle(files: Path, staged: Path, manifest: dict, bundle_id: str,
                 health_url: str, db_container: str) -> tuple[str, str]:
    """적용 — (status, detail). 실패 시 이 함수 안에서 복원까지 마친다."""
    backup = files / "updates" / "backup" / bundle_id
    placed: list[tuple[Path, Path | None]] = []  # (대상, 백업본 or None=신규)
    lines: list[str] = []

    def restore() -> None:
        for target, bak in reversed(placed):
            try:
                if bak is None:
                    target.unlink(missing_ok=True)
                else:
                    shutil.copy2(bak, target)
            except Exception as e:
                lines.append(f"복원 실패 {target.name}: {e}")

    for m in manifest.get("modules", []):
        art, tgt = m.get("artifact", ""), m.get("target_dir", "")
        if not (safe_rel(art) and safe_rel(tgt)):
            return "apply_failed", f"경로 위반: {art} → {tgt}"
        if m.get("kind") == "container":
            lines.append(f"{m['module_key']}: container 종류 — 이미지 번들 확보 시 "
                         "지원 예정, 이번 적용에서 건너뜀 (미검증)")
            continue
        src = staged / art
        target_dir = files / tgt
        target = target_dir / Path(art).name
        target_dir.mkdir(parents=True, exist_ok=True)
        bak = None
        if target.exists():
            bak_dir = backup / tgt
            bak_dir.mkdir(parents=True, exist_ok=True)
            bak = bak_dir / target.name
            shutil.copy2(target, bak)
        shutil.copy2(src, target)
        placed.append((target, bak))
        if sha256(target) != (m.get("sha256") or "").lower():
            restore()
            return "apply_failed", f"배치 후 sha256 불일치: {art} — 자동 복원됨"
        lines.append(f"{m['module_key']} v{m['version']}: {tgt}/{target.name} 배치")

    for mig in manifest.get("migrations", []):
        ok, out = psql_apply(db_container, staged / "migrations" / mig)
        if not ok:
            restore()
            return "apply_failed", (f"마이그레이션 {mig} 실패 — 파일 자동 복원됨. "
                                    f"DB 는 수동 확인 필요: {out}")
        lines.append(f"migration {mig} 적용")

    if not health_ok(health_url):
        restore()
        return "apply_failed", "적용 후 health 실패 — 자동 복원됨"

    for m in manifest.get("modules", []):
        if m.get("kind") != "container":
            stamp_version(db_container, m["module_key"], m["version"])
    return "applied", " · ".join(lines) + " · health OK"


def rollback_bundle(files: Path, manifest: dict, bundle_id: str,
                    db_container: str) -> tuple[str, str]:
    backup = files / "updates" / "backup" / bundle_id
    if not backup.exists():
        return "apply_failed", "백업 없음 — 롤백 불가"
    restored = 0
    for bak in backup.rglob("*"):
        if bak.is_file():
            rel = bak.relative_to(backup)
            target = files / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bak, target)
            restored += 1
    # 버전 스탬프는 '롤백됨' 표시로 — 이전 버전 문자열은 백업 시점에 안
    # 남겼으므로 정확 복원 대신 상태를 정직하게 기록
    for m in manifest.get("modules", []):
        if m.get("kind") != "container":
            stamp_version(db_container, m["module_key"],
                          f"{m['version']} (rolled back)")
    return "rolled_back", (f"백업 {restored}개 파일 복원. DB 마이그레이션은 "
                           "자동 롤백하지 않음 — 필요 시 롤백 블록 수동 실행")


def process_jobs(files: Path, health_url: str, db_container: str) -> int:
    jobs_dir = files / "updates" / "jobs"
    done = 0
    for job_path in sorted(jobs_dir.glob("*.json")):
        if job_path.name.endswith(".result.json"):
            continue
        try:
            job = json.load(open(job_path, encoding="utf-8"))
            bundle_id = job["bundle_id"]
            action = job["action"]
            staged = files / "updates" / "staged" / bundle_id
            manifest = json.load(open(staged / "manifest.json", encoding="utf-8"))
            log(f"{action} 시작: {bundle_id} ({manifest.get('name', '')})")
            if action == "apply":
                status, detail = apply_bundle(
                    files, staged, manifest, bundle_id, health_url, db_container)
            else:
                status, detail = rollback_bundle(
                    files, manifest, bundle_id, db_container)
            result = {"bundle_id": bundle_id, "status": status,
                      "detail": f"[agent] {detail}",
                      "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            with open(jobs_dir / f"{bundle_id}.result.json", "w",
                      encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
            job_path.unlink()
            log(f"{action} 완료: {bundle_id} → {status}")
            done += 1
        except Exception as e:
            log(f"잡 처리 실패 {job_path.name}: {e}")
            job_path.rename(job_path.with_suffix(".error"))
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files-dir", default=str(REPO / "files"))
    ap.add_argument("--health-url", default="http://localhost:8000")
    ap.add_argument("--db-container", default="slm-timescaledb")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args()

    files = Path(args.files_dir)
    (files / "updates" / "jobs").mkdir(parents=True, exist_ok=True)
    if args.loop:
        log("상주 모드 (10초 간격) — Ctrl+C 종료")
        while True:
            process_jobs(files, args.health_url, args.db_container)
            time.sleep(10)
    else:
        n = process_jobs(files, args.health_url, args.db_container)
        log(f"1회 처리 완료 — 잡 {n}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
