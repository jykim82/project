#!/usr/bin/env python3
"""
업데이트 번들 pack 스크립트 — docs/module-version-spec.md §5 (P2 예약분)

모듈·버전·아티팩트를 지정하면 manifest.json(+sha256) 생성과 tar.gz 조립을
자동화한다. manifest 규격 정본은 slm/endpoints/module_update.py docstring —
이 스크립트는 그 규격의 "제작기"다. 조립 후 자체 재검증(멤버 경로·sha 재계산)
까지 통과해야 파일을 남긴다.

사용 예:
  # 지도 번들만
  python3 scripts/pack_update_bundle.py --name "당진 지도 갱신" \\
      --module map-bundle=2026.11:files/map/dangjin.pmtiles

  # DB 마이그레이션 묶음 (0144 이후 자동 수집, db 버전 마커 자동 생성)
  python3 scripts/pack_update_bundle.py --migrations-since 0143

  # 전체성 릴리스 (여러 모듈 + 마이그레이션)
  python3 scripts/pack_update_bundle.py --name "2026-11 정기" \\
      --module ai-weights=1.2.0:/path/chronos.tar \\
      --module map-bundle=2026.11:files/map/dangjin.pmtiles \\
      --migrations-since 0143

  python3 scripts/pack_update_bundle.py --list-modules

주의:
- backend/frontend/node-red(kind=container)는 이미지 번들 미검증 상태 —
  pack 은 허용하되 에이전트가 적용을 건너뛴다 (경고 출력)
- target_dir 는 files/ 하위 상대경로 (에이전트가 files/<target_dir>/ 에 배치)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # /Users/jykim/web
MIG_DIR = REPO / "db" / "migrations"

# 모듈 레지스트리 — tb_module_version 시드(0138)와 동기 유지
#   key: (kind, 기본 target_dir, required_sku)
REGISTRY: dict[str, tuple[str, str | None, str | None]] = {
    "backend":      ("container", None, None),
    "frontend":     ("container", None, None),
    "node-red":     ("container", None, None),
    "db":           ("data", "updates/markers", None),
    "ai-weights":   ("bundle", "models", None),
    "map-bundle":   ("bundle", "map", None),
    "vision-agent": ("container", None, "VISION_AGENT_ENABLED"),
    "epanet":       ("feature", "epanet", "EPANET_ENABLED"),
}

MIG_RE = re.compile(r"^(\d{4})_[\w\-]+\.sql$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_module(spec: str) -> dict:
    """KEY=VERSION[:ARTIFACT[:TARGET_DIR]] → 모듈 엔트리(검증 전)."""
    m = re.match(r"^([\w\-]+)=([^:]+)(?::([^:]*))?(?::(.*))?$", spec)
    if not m:
        sys.exit(f"--module 형식 오류: {spec} (KEY=VERSION[:ARTIFACT[:TARGET_DIR]])")
    key, version, artifact, target = m.group(1), m.group(2), m.group(3), m.group(4)
    if key not in REGISTRY:
        sys.exit(f"미등록 모듈: {key} (등록: {', '.join(REGISTRY)})")
    kind, default_target, sku = REGISTRY[key]
    if kind == "container":
        print(f"⚠ {key}: kind=container — 에이전트가 적용을 건너뜀 (이미지 번들 미검증)")
    if not artifact:
        sys.exit(f"{key}: 아티팩트 경로 필요 (KEY=VERSION:경로)")
    src = Path(artifact).expanduser()
    if not src.is_absolute():
        src = (REPO / src).resolve()
    if not src.exists():
        sys.exit(f"{key}: 아티팩트 없음 — {src}")
    tgt = target or default_target
    if not tgt:
        sys.exit(f"{key}: target_dir 지정 필요 (기본값 없음)")
    if tgt.startswith("/") or ".." in tgt.split("/"):
        sys.exit(f"{key}: target_dir 경로 위반 — {tgt}")
    return {"module_key": key, "version": version, "kind": kind,
            "src": src, "target_dir": tgt,
            "required_sku": sku}


def collect_migrations(since: str | None, files: list[str]) -> list[Path]:
    out: list[Path] = []
    if since:
        if not re.match(r"^\d{4}$", since):
            sys.exit(f"--migrations-since 는 4자리 번호: {since}")
        for p in sorted(MIG_DIR.glob("*.sql")):
            mm = MIG_RE.match(p.name)
            if mm and mm.group(1) > since:
                out.append(p)
    for f in files:
        p = Path(f).expanduser()
        if not p.is_absolute():
            p = (REPO / p).resolve()
        if not p.exists():
            sys.exit(f"마이그레이션 없음: {p}")
        if not MIG_RE.match(p.name):
            sys.exit(f"마이그레이션 파일명 규격 위반(NNNN_x.sql): {p.name}")
        out.append(p)
    # 중복 제거·번호순
    uniq = {p.name: p for p in out}
    return [uniq[k] for k in sorted(uniq)]


def main() -> None:
    ap = argparse.ArgumentParser(description="SLM 업데이트 번들 제작")
    ap.add_argument("--name", default=f"SLM 업데이트 {date.today().isoformat()}")
    ap.add_argument("--module", action="append", default=[],
                    metavar="KEY=VERSION[:ARTIFACT[:TARGET_DIR]]")
    ap.add_argument("--migrations-since", metavar="NNNN",
                    help="db/migrations 에서 이 번호 초과분 자동 수집")
    ap.add_argument("--migration", action="append", default=[],
                    help="개별 마이그레이션 파일 추가")
    ap.add_argument("--out", help="출력 경로 (기본 ./slm-update-YYYYMMDD.tar.gz)")
    ap.add_argument("--list-modules", action="store_true")
    args = ap.parse_args()

    if args.list_modules:
        for k, (kind, tgt, sku) in REGISTRY.items():
            print(f"{k:14} kind={kind:10} 기본 target={tgt or '-':16} sku={sku or '-'}")
        return

    modules = [parse_module(s) for s in args.module]
    migrations = collect_migrations(args.migrations_since, args.migration)

    if not modules and not migrations:
        sys.exit("담을 것이 없음 — --module 또는 --migrations-since/--migration 지정")

    # 마이그레이션이 있으면 db 모듈 자동 등록 (버전=최신 번호, 마커 아티팩트)
    # — 서버 검증이 modules 비어 있음을 거부 + 적용 성공 시 db 버전 스탬핑 효과
    tmp_marker: Path | None = None
    if migrations and not any(m["module_key"] == "db" for m in modules):
        latest = MIG_RE.match(migrations[-1].name).group(1)
        tmp_marker = Path(tempfile.mkstemp(prefix="db-", suffix=".txt")[1])
        tmp_marker.write_text(
            f"SLM db migrations marker — 최신 {latest}\n"
            + "\n".join(p.name for p in migrations) + "\n")
        marker_named = tmp_marker.parent / f"db-{latest}.txt"
        tmp_marker.rename(marker_named)
        tmp_marker = marker_named
        modules.append({"module_key": "db", "version": latest, "kind": "data",
                        "src": tmp_marker, "target_dir": "updates/markers",
                        "required_sku": None})
        print(f"db 모듈 자동 등록: v{latest} (마이그레이션 {len(migrations)}건)")

    # 아티팩트 파일명(번들 내 루트) 충돌 검사
    names = [m["src"].name for m in modules]
    if len(names) != len(set(names)):
        sys.exit(f"아티팩트 파일명 충돌: {names} — 파일명을 다르게 지정")

    manifest = {
        "name": args.name,
        "modules": [
            {"module_key": m["module_key"], "version": m["version"],
             "kind": m["kind"], "artifact": m["src"].name,
             "sha256": sha256(m["src"]), "target_dir": m["target_dir"],
             "required_sku": m["required_sku"]}
            for m in modules
        ],
        "migrations": [p.name for p in migrations],
    }

    out = Path(args.out) if args.out else Path(f"slm-update-{date.today():%Y%m%d}.tar.gz")
    with tarfile.open(out, "w:gz") as tar:
        mf_tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        mf_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
        tar.add(mf_tmp, arcname="manifest.json")
        mf_tmp.unlink()
        for m in modules:
            tar.add(m["src"], arcname=m["src"].name)
        for p in migrations:
            tar.add(p, arcname=f"migrations/{p.name}")
    if tmp_marker:
        tmp_marker.unlink(missing_ok=True)

    # ── 자체 재검증 — 서버 검증과 같은 규칙으로 되읽기 ──
    with tempfile.TemporaryDirectory() as td, tarfile.open(out) as tar:
        for mem in tar.getmembers():
            if mem.name.startswith("/") or ".." in mem.name.split("/"):
                out.unlink()
                sys.exit(f"자체검증 실패: 경로 위반 멤버 {mem.name}")
        tar.extractall(td)
        mf = json.load(open(f"{td}/manifest.json", encoding="utf-8"))
        for m in mf["modules"]:
            got = sha256(Path(td) / m["artifact"])
            if got != m["sha256"]:
                out.unlink()
                sys.exit(f"자체검증 실패: sha 불일치 {m['artifact']}")
        for mig in mf["migrations"]:
            if not (Path(td) / "migrations" / mig).exists():
                out.unlink()
                sys.exit(f"자체검증 실패: 마이그레이션 누락 {mig}")

    size_mb = out.stat().st_size / (1 << 20)
    print(f"\n✅ {out} ({size_mb:.1f} MB) — 자체검증 통과")
    print(f"   번들명: {manifest['name']}")
    for m in manifest["modules"]:
        sku = f" [SKU {m['required_sku']}]" if m["required_sku"] else ""
        print(f"   - {m['module_key']} v{m['version']} → files/{m['target_dir']}/{sku}")
    if manifest["migrations"]:
        print(f"   - migrations: {', '.join(manifest['migrations'])}")
    print("\n다음: /admin/system-modules 업데이트 번들 패널에 업로드 → 승인 →")
    print("      호스트에서 python3 scripts/update_agent.py --once")


if __name__ == "__main__":
    main()
