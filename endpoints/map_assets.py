"""구축 — 지도·관망 데이터 자산 관리 (docs/offline-map-bundle.md §UI).

관할(고객사) 변경을 UI 로 처리하기 위한 업로드/상태 API.
업로드본은 files/(map|gis)/ 에 저장되고, 프런트 서빙 라우트가
**업로드본 우선 → 빌드 내장본 폴백**으로 읽는다 — 재빌드 불필요.

- POST   /setup/map-assets/basemap  — 관할 베이스맵 region.pmtiles 교체
- POST   /setup/map-assets/layers   — GIS 레이어 zip(geojson/pmtiles) 교체
- GET    /setup/map-assets/status   — 업로드본 현황
- DELETE /setup/map-assets/{kind}   — 업로드본 삭제 (내장본 복원)
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime

from fastapi import APIRouter, File, HTTPException, UploadFile

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/setup/map-assets", tags=["map-assets"])

_FILES_ROOT = os.environ.get(  # 컨테이너: /data/files (docker-compose FILES_BASE)
    "FILES_BASE",
    os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "web", "files",
    )),
)
MAP_DIR = os.path.join(_FILES_ROOT, "map")
GIS_DIR = os.path.join(_FILES_ROOT, "gis")

MAX_BASEMAP_BYTES = 1024 * 1024 * 1024   # 1GB (광역 관할 대비)
MAX_LAYERS_ZIP_BYTES = 512 * 1024 * 1024
_ALLOWED_LAYER_EXT = (".geojson", ".pmtiles")


def _file_info(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    st = os.stat(path)
    return {
        "name": os.path.basename(path),
        "size_mb": round(st.st_size / 1e6, 1),
        "updated_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
    }


@router.get("/status")
def status() -> dict:
    """업로드본 현황 — 없으면 프런트가 빌드 내장본을 사용 중."""
    layers = []
    if os.path.isdir(GIS_DIR):
        for name in sorted(os.listdir(GIS_DIR)):
            if name.lower().endswith(_ALLOWED_LAYER_EXT):
                info = _file_info(os.path.join(GIS_DIR, name))
                if info:
                    layers.append(info)
    return {
        "status": "OK",
        "basemap": _file_info(os.path.join(MAP_DIR, "region.pmtiles")),
        "layers": layers,
    }


def _save_atomic(upload: UploadFile, dest: str, max_bytes: int) -> int:
    """스트리밍 저장 (임시파일 → rename). 반환: 바이트 수."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    total = 0
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest))
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, f"파일이 너무 큽니다 (최대 {max_bytes // 1024 // 1024}MB)")
                out.write(chunk)
        os.replace(tmp, dest)
        return total
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@router.post("/basemap")
def upload_basemap(file: UploadFile = File(...)) -> dict:
    """관할 베이스맵 pmtiles 교체 — scripts/extract-map-region.sh 산출물."""
    if not (file.filename or "").lower().endswith(".pmtiles"):
        raise HTTPException(400, "pmtiles 파일만 업로드할 수 있습니다.")
    head = file.file.read(7)
    file.file.seek(0)
    if head != b"PMTiles":
        raise HTTPException(400, "PMTiles 형식이 아닙니다 (헤더 불일치).")
    dest = os.path.join(MAP_DIR, "region.pmtiles")
    size = _save_atomic(file, dest, MAX_BASEMAP_BYTES)
    logger.info("베이스맵 업로드: %s (%.1fMB)", file.filename, size / 1e6)
    return {"status": "OK", "basemap": _file_info(dest)}


@router.post("/layers")
def upload_layers(file: UploadFile = File(...)) -> dict:
    """GIS 레이어 zip 교체 — scripts/import-shp-layers.py 산출물
    (public/gis 의 *.geojson / *.pmtiles 를 zip 으로 묶은 것)."""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "zip 파일만 업로드할 수 있습니다.")
    os.makedirs(GIS_DIR, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=True) as tmp:
        total = 0
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_LAYERS_ZIP_BYTES:
                raise HTTPException(413, "zip 이 너무 큽니다 (최대 512MB)")
            tmp.write(chunk)
        tmp.flush()

        imported = []
        try:
            with zipfile.ZipFile(tmp.name) as zf:
                for entry in zf.infolist():
                    base = os.path.basename(entry.filename)  # traversal 방지
                    if not base or not base.lower().endswith(_ALLOWED_LAYER_EXT):
                        continue
                    with zf.open(entry) as src, \
                            open(os.path.join(GIS_DIR, base), "wb") as out:
                        shutil.copyfileobj(src, out)
                    imported.append(base)
        except zipfile.BadZipFile as e:
            raise HTTPException(400, "zip 파일을 열 수 없습니다.") from e

    if not imported:
        raise HTTPException(400, "zip 안에 geojson/pmtiles 파일이 없습니다.")
    logger.info("GIS 레이어 업로드: %d개 (%s)", len(imported), ", ".join(imported[:5]))
    return {"status": "OK", "imported": imported}


@router.delete("/{kind}")
def delete_uploaded(kind: str) -> dict:
    """업로드본 삭제 → 빌드 내장본으로 복원."""
    if kind == "basemap":
        path = os.path.join(MAP_DIR, "region.pmtiles")
        if os.path.isfile(path):
            os.unlink(path)
        return {"status": "OK"}
    if kind == "layers":
        removed = 0
        if os.path.isdir(GIS_DIR):
            for name in os.listdir(GIS_DIR):
                if name.lower().endswith(_ALLOWED_LAYER_EXT):
                    os.unlink(os.path.join(GIS_DIR, name))
                    removed += 1
        return {"status": "OK", "removed": removed}
    raise HTTPException(404, "kind 는 basemap | layers")
