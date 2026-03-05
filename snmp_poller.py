"""
SNMP 스위치 포트 진단 모듈
- SNMP_ENABLED=true: pysnmp GET-WALK (ifOperStatus, dot1dTpFdbTable, ipNetToMediaTable)
- SNMP_ENABLED=false: 운영 DB 기반 현실적 Mock 데이터 생성
- 조건부 import: SNMP_ENABLED=false면 pysnmp 미사용 (설치 불필요)
"""

import hashlib
import logging
import os
import random
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)

SNMP_ENABLED = os.environ.get("SNMP_ENABLED", "false").lower() == "true"
SNMP_COMMUNITY = os.environ.get("SNMP_COMMUNITY", "public")
SNMP_TIMEOUT = int(os.environ.get("SNMP_TIMEOUT", "5"))
SNMP_PORT = int(os.environ.get("SNMP_PORT", "161"))
POLL_INTERVAL = int(os.environ.get("SNMP_POLL_INTERVAL", "180"))

# Mock 포트 수 (장비유형별)
_PORT_COUNT = {"L3": 24, "L2": 24}
_DEFAULT_PORT_COUNT = 24

# MIB-II OID 상수 (real 모드에서 사용)
_OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
_OID_IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
_OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
_OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
_OID_IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"
_OID_IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
_OID_IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"
_OID_IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"
_OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
_OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"


class SnmpPoller:
    """SNMP 폴링 관리자 (mock / real 모드 자동 전환)."""

    def __init__(self, get_conn: Callable):
        self.get_conn = get_conn
        self._switches: list[dict] = []
        self._link_map: dict[str, list[dict]] = {}  # switch_id → connected devices
        self._ip_device_map: dict[str, str] = {}  # ip → device_name

    # ------------------------------------------------------------------
    # 초기화
    # ------------------------------------------------------------------

    def load_switches(self) -> int:
        """스위치 목록 + 연결 장비 로드."""
        conn = self.get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT e.equipment_id, e.sitename, e.facilitytype,
                       e.equipmenttype, n.ip_address
                FROM tb_equipment_info e
                JOIN tb_network_info n ON e.equipment_id = n.equipment_id
                WHERE e.equipmenttype LIKE '%스위치%'
                  AND n.ip_address IS NOT NULL
                ORDER BY e.sitename, e.equipmenttype
            """)
            cols = ["equipment_id", "sitename", "facilitytype",
                    "equipmenttype", "ip_address"]
            self._switches = [dict(zip(cols, r)) for r in cur.fetchall()]

            # 연결 장비 매핑 (링크 테이블에서)
            self._link_map.clear()
            cur.execute("""
                SELECT l.source_equipment_id, l.target_equipment_id,
                       COALESCE(te.sitename, '') AS target_sitename,
                       COALESCE(te.equipmenttype, '') AS target_equiptype,
                       tn.ip_address AS target_ip
                FROM tb_network_link l
                JOIN tb_equipment_info te ON l.target_equipment_id = te.equipment_id
                LEFT JOIN tb_network_info tn ON te.equipment_id = tn.equipment_id
            """)
            for src, tgt, tsn, teq, tip in cur.fetchall():
                if src not in self._link_map:
                    self._link_map[src] = []
                self._link_map[src].append({
                    "equipment_id": tgt,
                    "sitename": tsn,
                    "equipmenttype": teq,
                    "ip_address": tip,
                })

            # IP → 장비명 매핑
            self._ip_device_map.clear()
            cur.execute("""
                SELECT n.ip_address, e.equipment_id, e.sitename, e.equipmenttype
                FROM tb_network_info n
                JOIN tb_equipment_info e ON n.equipment_id = e.equipment_id
                WHERE n.ip_address IS NOT NULL
            """)
            for ip, eid, sn, et in cur.fetchall():
                self._ip_device_map[ip] = f"{sn} {et} ({eid})"

            cur.close()
            logger.info(f"SNMP 스위치 로드 완료: {len(self._switches)}대")
            return len(self._switches)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 폴링
    # ------------------------------------------------------------------

    def poll_all(self) -> int:
        """모든 스위치 폴링 후 DB 저장."""
        polled = 0
        for sw in self._switches:
            try:
                if SNMP_ENABLED:
                    ports = self._poll_switch_real(sw)
                else:
                    ports = self._poll_switch_mock(sw)
                if ports:
                    self._save_port_status(sw["equipment_id"], ports)
                    polled += 1
            except Exception as e:
                logger.error(f"SNMP 폴링 실패 ({sw['equipment_id']}): {e}")
        return polled

    def _poll_switch_mock(self, switch: dict) -> list[dict]:
        """Mock 데이터 생성 — 결정적 seed + 실제 연결 장비 기반."""
        eq_id = switch["equipment_id"]
        eq_type = switch["equipmenttype"]
        now = datetime.now()

        # 포트 수 결정
        port_count = _DEFAULT_PORT_COUNT
        for prefix, cnt in _PORT_COUNT.items():
            if prefix in eq_type:
                port_count = cnt
                break

        # 결정적 seed (장비별 안정 레이아웃)
        seed_int = int(hashlib.md5(eq_id.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed_int)

        # 실제 연결된 장비들
        connected_devices = self._link_map.get(eq_id, [])

        ports = []
        for idx in range(1, port_count + 1):
            port_name = f"GigabitEthernet0/{idx}"

            # 연결 장비 배정 (1~N번 포트)
            dev = connected_devices[idx - 1] if idx <= len(connected_devices) else None

            if dev:
                # 연결 포트: 대부분 UP, 5분 간격으로 약간의 변동
                minute_mod = (now.minute + seed_int + idx) % 60
                oper = "up" if minute_mod > 3 else "down"  # ~95% up
                speed = rng.choice([100, 1000])
                mac = self._generate_mac(rng, idx)
                ip = dev.get("ip_address")
                dev_name = f"{dev['sitename']} {dev['equipmenttype']}"
                in_oct = rng.randint(10_000_000, 500_000_000)
                out_oct = rng.randint(10_000_000, 500_000_000)
                in_err = rng.randint(0, 5)
                out_err = rng.randint(0, 2)
            else:
                # 비연결 포트
                minute_mod = (now.minute + seed_int + idx) % 20
                oper = "up" if minute_mod < 3 else "down"  # ~15% up
                speed = 1000
                mac = None
                ip = None
                dev_name = None
                in_oct = rng.randint(0, 100_000) if oper == "up" else 0
                out_oct = rng.randint(0, 100_000) if oper == "up" else 0
                in_err = 0
                out_err = 0

            ports.append({
                "port_index": idx,
                "port_name": port_name,
                "oper_status": oper,
                "admin_status": "up",
                "speed_mbps": speed,
                "connected_mac": mac,
                "connected_ip": ip,
                "connected_device_name": dev_name,
                "in_octets": in_oct,
                "out_octets": out_oct,
                "in_errors": in_err,
                "out_errors": out_err,
            })

        return ports

    def _poll_switch_real(self, switch: dict) -> list[dict]:
        """실제 SNMP 폴링 — pysnmp 조건부 import."""
        try:
            from pysnmp.hlapi import (
                CommunityData, ContextData, ObjectType, ObjectIdentity,
                SnmpEngine, UdpTransportTarget, nextCmd,
            )
        except ImportError:
            logger.error("pysnmp 미설치 — pip install pysnmp")
            return self._poll_switch_mock(switch)

        host = switch["ip_address"]
        engine = SnmpEngine()
        community = CommunityData(SNMP_COMMUNITY, mpModel=1)
        target = UdpTransportTarget((host, SNMP_PORT),
                                    timeout=SNMP_TIMEOUT, retries=1)

        def walk(oid_prefix: str) -> dict[str, str]:
            result = {}
            for err_ind, err_st, _, var_binds in nextCmd(
                engine, community, target, ContextData(),
                ObjectType(ObjectIdentity(oid_prefix)),
                lexicographicMode=False,
            ):
                if err_ind or err_st:
                    break
                for oid_obj, val in var_binds:
                    last = str(oid_obj).split(".")[-1]
                    result[last] = str(val)
            return result

        # SNMP WALK 수행
        descrs = walk(_OID_IF_DESCR)
        oper_stats = walk(_OID_IF_OPER_STATUS)
        admin_stats = walk(_OID_IF_ADMIN_STATUS)
        speeds = walk(_OID_IF_SPEED)
        in_octets = walk(_OID_IF_IN_OCTETS)
        out_octets = walk(_OID_IF_OUT_OCTETS)
        in_errors = walk(_OID_IF_IN_ERRORS)
        out_errors = walk(_OID_IF_OUT_ERRORS)

        ports = []
        for idx_str, descr in descrs.items():
            idx = int(idx_str)
            oper_raw = oper_stats.get(idx_str, "2")
            oper = "up" if oper_raw == "1" else "down" if oper_raw == "2" else "testing"
            admin_raw = admin_stats.get(idx_str, "1")
            admin = "up" if admin_raw == "1" else "down" if admin_raw == "2" else "testing"
            speed_bps = int(speeds.get(idx_str, "0"))

            ports.append({
                "port_index": idx,
                "port_name": descr,
                "oper_status": oper,
                "admin_status": admin,
                "speed_mbps": speed_bps // 1_000_000,
                "connected_mac": None,  # MAC/ARP 테이블은 추후 확장
                "connected_ip": None,
                "connected_device_name": None,
                "in_octets": int(in_octets.get(idx_str, "0")),
                "out_octets": int(out_octets.get(idx_str, "0")),
                "in_errors": int(in_errors.get(idx_str, "0")),
                "out_errors": int(out_errors.get(idx_str, "0")),
            })

        return sorted(ports, key=lambda p: p["port_index"])

    # ------------------------------------------------------------------
    # DB 저장 / 조회
    # ------------------------------------------------------------------

    def _save_port_status(self, equipment_id: str, ports: list[dict]):
        """tb_snmp_port_status UPSERT."""
        conn = self.get_conn()
        try:
            cur = conn.cursor()
            for p in ports:
                cur.execute("""
                    INSERT INTO tb_snmp_port_status
                        (equipment_id, port_index, port_name, oper_status,
                         admin_status, speed_mbps, connected_mac, connected_ip,
                         connected_device_name, in_octets, out_octets,
                         in_errors, out_errors, polled_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                    ON CONFLICT (equipment_id, port_index) DO UPDATE SET
                        port_name = EXCLUDED.port_name,
                        oper_status = EXCLUDED.oper_status,
                        admin_status = EXCLUDED.admin_status,
                        speed_mbps = EXCLUDED.speed_mbps,
                        connected_mac = EXCLUDED.connected_mac,
                        connected_ip = EXCLUDED.connected_ip,
                        connected_device_name = EXCLUDED.connected_device_name,
                        in_octets = EXCLUDED.in_octets,
                        out_octets = EXCLUDED.out_octets,
                        in_errors = EXCLUDED.in_errors,
                        out_errors = EXCLUDED.out_errors,
                        polled_at = now()
                """, (
                    equipment_id, p["port_index"], p["port_name"],
                    p["oper_status"], p["admin_status"], p["speed_mbps"],
                    p["connected_mac"], p["connected_ip"],
                    p["connected_device_name"],
                    p["in_octets"], p["out_octets"],
                    p["in_errors"], p["out_errors"],
                ))
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def get_ports(self, equipment_id: str) -> list[dict]:
        """특정 스위치 포트 상태 조회."""
        conn = self.get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT port_index, port_name, oper_status, admin_status,
                       speed_mbps, connected_mac, connected_ip,
                       connected_device_name, in_octets, out_octets,
                       in_errors, out_errors, polled_at
                FROM tb_snmp_port_status
                WHERE equipment_id = %s
                ORDER BY port_index
            """, (equipment_id,))
            cols = [
                "port_index", "port_name", "oper_status", "admin_status",
                "speed_mbps", "connected_mac", "connected_ip",
                "connected_device_name", "in_octets", "out_octets",
                "in_errors", "out_errors", "polled_at",
            ]
            rows = []
            for r in cur.fetchall():
                row = dict(zip(cols, r))
                if row["polled_at"]:
                    row["polled_at"] = row["polled_at"].isoformat()
                rows.append(row)
            cur.close()
            return rows
        finally:
            conn.close()

    def get_system_info(self, equipment_id: str) -> Optional[dict]:
        """스위치 시스템 정보 (장비정보 + 포트 요약)."""
        sw = next((s for s in self._switches
                    if s["equipment_id"] == equipment_id), None)
        if not sw:
            return None

        conn = self.get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE oper_status = 'up') AS up_count,
                       COUNT(*) FILTER (WHERE oper_status = 'down') AS down_count,
                       MAX(polled_at) AS polled_at
                FROM tb_snmp_port_status
                WHERE equipment_id = %s
            """, (equipment_id,))
            r = cur.fetchone()
            cur.close()
            return {
                **sw,
                "total_ports": r[0] or 0,
                "up_ports": r[1] or 0,
                "down_ports": r[2] or 0,
                "polled_at": r[3].isoformat() if r[3] else None,
            }
        finally:
            conn.close()

    def get_summary(self) -> list[dict]:
        """전체 스위치 포트 상태 요약."""
        conn = self.get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT p.equipment_id,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE p.oper_status = 'up') AS up_count,
                       COUNT(*) FILTER (WHERE p.oper_status = 'down') AS down_count,
                       MAX(p.polled_at) AS polled_at
                FROM tb_snmp_port_status p
                GROUP BY p.equipment_id
                ORDER BY p.equipment_id
            """)
            rows = []
            sw_map = {s["equipment_id"]: s for s in self._switches}
            for eid, total, up, down, polled in cur.fetchall():
                sw = sw_map.get(eid, {})
                rows.append({
                    "equipment_id": eid,
                    "sitename": sw.get("sitename", ""),
                    "equipmenttype": sw.get("equipmenttype", ""),
                    "ip_address": sw.get("ip_address", ""),
                    "total": total,
                    "up": up,
                    "down": down,
                    "polled_at": polled.isoformat() if polled else None,
                })
            cur.close()
            return rows
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 유틸
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_mac(rng: random.Random, port_idx: int) -> str:
        """결정적 MAC 주소 생성."""
        b = [0x00, 0x1A, 0x2B,
             rng.randint(0, 255),
             (port_idx >> 8) & 0xFF,
             port_idx & 0xFF]
        return ":".join(f"{x:02x}" for x in b)
