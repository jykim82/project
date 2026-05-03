"""SHP 파일 스캐너 (pyshp 기반, 경량).

geopandas/GDAL 의존 없이 SHP 파일의 메타데이터·속성·지오메트리를 읽는다.
EPANET .inp 변환 입력 단계에서 사용.

SHP 인코딩: SLM 운영 SHP 는 EUC-KR 으로 작성됨. .cpg 파일이 'cp949'/'euc-kr'/'949'
을 지정한 경우 우선 사용, 아니면 EUC-KR 폴백.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


logger = logging.getLogger(__name__)


@dataclass
class ShpRecord:
    """SHP 한 레코드 — 지오메트리 + 속성."""
    geometry_type: str           # 'Point' / 'PolyLine' / 'Polygon' 등
    points: list                 # [(x, y), ...] (PolyLine/Polygon 의 경우 첫 part 만)
    attrs: dict                  # 속성 딕셔너리


@dataclass
class ShpScanResult:
    """SHP 스캔 결과 메타."""
    file_name: str
    record_count: int
    field_names: list = field(default_factory=list)
    geometry_type: Optional[str] = None
    bbox: Optional[tuple] = None        # (xmin, ymin, xmax, ymax)
    encoding: str = "euc-kr"
    sample: Optional[dict] = None       # 첫 레코드 속성 (검증용)
    error: Optional[str] = None


def _detect_encoding(shp_path: Path) -> str:
    """.cpg 파일을 보고 인코딩을 결정. 운영 SHP 는 EUC-KR 이 기본."""
    cpg = shp_path.with_suffix(".cpg")
    if cpg.exists():
        try:
            content = cpg.read_text(encoding="ascii", errors="ignore").strip().lower()
            if content in ("949", "cp949", "ms949"):
                return "cp949"
            if content in ("utf-8", "utf8"):
                return "utf-8"
            if content in ("euc-kr", "euckr"):
                return "euc-kr"
        except Exception:
            pass
    return "euc-kr"


_GEOM_TYPE_MAP = {
    1: "Point", 3: "PolyLine", 5: "Polygon", 8: "MultiPoint",
    11: "PointZ", 13: "PolyLineZ", 15: "PolygonZ",
    21: "PointM", 23: "PolyLineM", 25: "PolygonM",
}


def scan_shp(shp_path: str | Path) -> ShpScanResult:
    """SHP 파일의 메타·필드명·첫 레코드만 빠르게 스캔.

    실제 모든 레코드를 메모리에 로드하지 않으므로 대용량 파일도 안전.
    """
    p = Path(shp_path)
    result = ShpScanResult(file_name=p.name, record_count=0)

    try:
        import shapefile  # pyshp
    except ImportError as e:
        result.error = f"pyshp 라이브러리가 설치되지 않았습니다: {e}"
        return result

    if not p.exists():
        result.error = f"파일이 존재하지 않습니다: {p}"
        return result

    encoding = _detect_encoding(p)
    result.encoding = encoding

    try:
        reader = shapefile.Reader(str(p), encoding=encoding)
    except UnicodeDecodeError:
        encoding = "cp949"
        result.encoding = encoding
        try:
            reader = shapefile.Reader(str(p), encoding=encoding)
        except Exception as e:
            result.error = f"SHP 읽기 실패 (인코딩={encoding}): {e}"
            return result
    except Exception as e:
        result.error = f"SHP 읽기 실패: {e}"
        return result

    try:
        result.record_count = len(reader)
        result.field_names = [f[0] for f in reader.fields[1:]]  # [0] 은 deletion flag
        result.geometry_type = _GEOM_TYPE_MAP.get(reader.shapeType, f"type{reader.shapeType}")
        bbox = reader.bbox
        if bbox and len(bbox) >= 4:
            result.bbox = (float(bbox[0]), float(bbox[1]),
                           float(bbox[2]), float(bbox[3]))

        # 첫 레코드 샘플 (속성만)
        if result.record_count > 0:
            try:
                sr = reader.shapeRecord(0)
                result.sample = dict(sr.record.as_dict())
            except Exception as e:
                logger.warning(f"SHP 첫 레코드 샘플 추출 실패: {e}")
    finally:
        reader.close()

    return result


def iter_records(shp_path: str | Path) -> Iterable[ShpRecord]:
    """SHP 레코드를 한 건씩 yield. 메모리 효율 — 대용량 SHP 도 안전.

    PolyLine/Polygon 은 첫 part 만 사용 (다중 part 는 EPANET 변환 시 분리 처리).
    """
    p = Path(shp_path)
    if not p.exists():
        return

    encoding = _detect_encoding(p)
    try:
        import shapefile  # pyshp
        reader = shapefile.Reader(str(p), encoding=encoding)
    except UnicodeDecodeError:
        try:
            reader = shapefile.Reader(str(p), encoding="cp949")
        except Exception:
            return
    except Exception:
        return

    try:
        for sr in reader.iterShapeRecords():
            shape = sr.shape
            geom_type = _GEOM_TYPE_MAP.get(shape.shapeType, f"type{shape.shapeType}")
            pts = [(float(x), float(y)) for x, y in (shape.points or [])]
            attrs = dict(sr.record.as_dict())
            yield ShpRecord(geometry_type=geom_type, points=pts, attrs=attrs)
    finally:
        reader.close()
