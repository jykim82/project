"""endpoints/epanet.py — EPANET 수리 시뮬레이션 API (Phase 1).

활성화: tb_comm_code (region, 'SITE_SETTING', 'EPANET_ENABLED')='Y'
SHP 위치: 환경변수 EPANET_SHP_BASE_DIR (기본 /data/files/gis/shp)
산출물: /data/files/epanet/{region}_{ts}.inp + tb_epanet_artifact 행

엔드포인트:
- GET    /admin/epanet/status                — 활성화·환경 상태
- POST   /admin/epanet/scan                  — SHP 메타 스캔 (변환 전 검증)
- POST   /admin/epanet/inp/generate          — SHP→.inp 변환 실행
- GET    /admin/epanet/inp/list              — 산출물 목록
- GET    /admin/epanet/inp/{artifact_id}/download — .inp 파일 다운로드
- DELETE /admin/epanet/inp/{artifact_id}     — 산출물 삭제

비활성(default) 시: status 는 응답, 나머지는 503.
"""


from .common import router  # noqa: F401

# 라우트 등록(데코레이터 부수효과)을 위해 모든 서브모듈 import
from . import (  # noqa: F401
    points_crud,
    flow_map,
    deviation,
    menu_settings,
    leak_headloss,
    whatif,
    assessment,
    data_quality,
    artifacts,
    simulations,
)
