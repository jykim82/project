#!/usr/bin/env python
"""GIS SHP 임포트 — 관할 SHP 원본 → 프런트 GIS 레이어 데이터 재생성.

고객사(관할) 변경 시 관로·밸브·경계 등 SHP 세트를 받아 실행하면
public/gis/ 의 GeoJSON + PMTiles 를 재생성한다 (코드 수정 없음).
docs/operations/offline-map-bundle.md §고객사 변경 절차.

입력 규격 (상수도 GIS 표준 코드 파일명, EPSG:5186, DBF 인코딩 CP949):
  SA100(제수밸브실) SA114(배수지) SA117(유량계) SA119(소화전)
  SA200(경계밸브) SA202(이토밸브) SA203(공기변) SA206(가압장)
  SA207(지수밸브) SA300(누수지점)
  SAA003(송수관) SAA004(배수관*) SAA005(급수관*) SAA010(공업용수관)
  SAA020(소방관) / 소블록경계 / 중블록경계
  * 같은 코드 복수 파일(블록구축신설 등)은 자동 병합

사용:
  /Users/jykim/slm/venv/bin/python scripts/import-shp-layers.py <SHP디렉토리>
  # 예) … scripts/import-shp-layers.py "docs/SHP추출"

의존: pyshp pyproj (slm venv) + tippecanoe (brew — PMTiles 생성)
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pyproj
import shapefile  # pyshp

WEB_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = WEB_ROOT / "slm-dashboard/slm-dashboard/public/gis"

# SHP 코드 → 레이어 id (gis-layers.ts 와 1:1). geojson 은 항상 생성,
# pmtiles 그룹에 속한 레이어는 tippecanoe 로 통합 타일도 생성.
CODE_TO_LAYER = {
    "SA100": "gate_valve",
    "SA114": "reservoir",
    "SA117": "flow_meter",
    "SA119": "fire_hydrant",
    "SA200": "boundary_valve",
    "SA202": "mud_valve",
    "SA203": "air_valve",
    "SA206": "pump_station",
    "SA207": "stop_valve",
    "SA300": "leak_point",
    "SAA003": "transmission",
    "SAA004": "distribution",
    "SAA005": "supply",
    "SAA010": "industrial",
    "SAA020": "fire",
    "소블록경계": "block_boundary",
    "중블록경계": "mid_block_boundary",
}
PMTILES_GROUPS = {
    "pipes.pmtiles": ["transmission", "distribution", "supply", "industrial"],
    "facilities.pmtiles": [
        "gate_valve", "boundary_valve", "stop_valve", "air_valve",
        "fire_hydrant", "leak_point",
    ],
}

_tf = pyproj.Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)


def _code_of(stem: str) -> str | None:
    """파일명에서 SHP 코드 추출 — 'SAA004(배수관_…)' → SAA004, '소블록경계' → 그대로."""
    for code in sorted(CODE_TO_LAYER, key=len, reverse=True):
        if stem.startswith(code):
            return code
    return None


def _transform_coords(points):
    return [list(_tf.transform(x, y)) for x, y in points]


def _shape_to_geometry(shp) -> dict | None:
    """pyshp shape → WGS84 GeoJSON geometry (Point/LineString/Polygon 계열)."""
    t = shp.shapeTypeName
    pts = shp.points
    if not pts:
        return None
    if t.startswith("POINT"):
        lon, lat = _tf.transform(*pts[0][:2])
        return {"type": "Point", "coordinates": [lon, lat]}
    parts = list(shp.parts) + [len(pts)]
    rings = [
        _transform_coords(p[:2] for p in pts[parts[i]:parts[i + 1]])
        for i in range(len(parts) - 1)
    ]
    if t.startswith("POLYLINE"):
        if len(rings) == 1:
            return {"type": "LineString", "coordinates": rings[0]}
        return {"type": "MultiLineString", "coordinates": rings}
    if t.startswith("POLYGON"):
        return {"type": "Polygon", "coordinates": rings}
    return None


def _read_shp(path: Path) -> list[dict]:
    """SHP 1개 → GeoJSON Feature 목록 (DBF 속성 포함, CP949 폴백)."""
    for enc in ("cp949", "euc-kr", "utf-8", "latin1"):
        try:
            r = shapefile.Reader(str(path), encoding=enc)
            fields = [f[0] for f in r.fields[1:]]
            feats = []
            for sr in r.iterShapeRecords():
                geom = _shape_to_geometry(sr.shape)
                if geom is None:
                    continue
                props = {}
                for k, v in zip(fields, sr.record):
                    if isinstance(v, bytes):
                        v = v.decode(enc, "replace")
                    props[k] = v
                feats.append({"type": "Feature", "geometry": geom, "properties": props})
            return feats
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"인코딩 판별 실패: {path.name}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    src = Path(sys.argv[1])
    if not src.is_dir():
        print(f"❌ SHP 디렉토리 없음: {src}")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 코드별 SHP 수집·병합 → 레이어 GeoJSON
    by_layer: dict[str, list[dict]] = defaultdict(list)
    skipped = []
    for shp in sorted(src.glob("*.shp")):
        # 폐쇄(철거) 관로는 현행 관망 표시 대상 아님
        if "폐쇄" in shp.stem:
            skipped.append(f"{shp.name} (폐쇄분)")
            continue
        code = _code_of(shp.stem)
        if code is None:
            skipped.append(shp.name)
            continue
        feats = _read_shp(shp)
        by_layer[CODE_TO_LAYER[code]].extend(feats)
        print(f"  {shp.name} → {CODE_TO_LAYER[code]} (+{len(feats)})")
    if skipped:
        print(f"⚠ 매핑 없는 파일 skip: {', '.join(skipped)}")
    if not by_layer:
        print("❌ 매핑되는 SHP 없음 — CODE_TO_LAYER 확인")
        return 1

    for layer, feats in by_layer.items():
        out = OUT_DIR / f"{layer}.geojson"
        out.write_text(json.dumps(
            {"type": "FeatureCollection", "features": feats}, ensure_ascii=False,
        ))
        print(f"✅ {out.name}: {len(feats)} features")

    # 2) 대규모 레이어 → PMTiles (tippecanoe)
    import shutil
    if shutil.which("tippecanoe") is None:
        print("⚠ tippecanoe 없음 — PMTiles 생성 생략 (brew install tippecanoe)")
        return 0
    for pmt_name, layers in PMTILES_GROUPS.items():
        inputs = []
        for layer in layers:
            gj = OUT_DIR / f"{layer}.geojson"
            if gj.exists():
                inputs += ["-L", f"{layer}:{gj}"]
        if not inputs:
            continue
        out = OUT_DIR / pmt_name
        cmd = ["tippecanoe", "-o", str(out), "--force",
               "-zg", "--drop-densest-as-needed", "--quiet"] + inputs
        subprocess.run(cmd, check=True)
        print(f"✅ {pmt_name}: {out.stat().st_size / 1e6:.1f}MB ({', '.join(layers)})")

    print("완료 — 프런트 재빌드 후 GIS 레이어 패널에서 확인")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
