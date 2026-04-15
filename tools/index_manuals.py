"""
[E-025 P3] 장비 매뉴얼 RAG 인덱서

docs/매뉴얼/*.pdf 를 스캔해서:
  1. 페이지 단위로 텍스트 추출
  2. 100자 미만 페이지 스킵 (표지/빈 페이지)
  3. snowflake-arctic-embed2 (Ollama)로 임베딩 생성
  4. data/manual_embeddings/<manual_id>.npz에 임베딩 캐시
  5. tb_equipment_manual에 메타 등록

실행:
  docker exec slm-backend python3 /app/tools/index_manuals.py

재실행 시 이미 인덱싱된 매뉴얼은 스킵 (title + file_url 매칭).
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

from typing import Optional

import httpx
import numpy as np
import psycopg2
import pypdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("index_manuals")

# ─────────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────────

MANUALS_SRC_DIR = os.environ.get("MANUALS_SRC_DIR", "/tmp/manuals_src")
# [폐쇄망 납품] /app/data/manuals = ../slm/data/manuals (호스트 바인드 마운트)
# 컨테이너 ephemeral FS였던 /web/files/manuals에서 이동.
MANUALS_DEST_DIR = os.environ.get(
    "MANUALS_DEST_DIR",
    os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "manuals")),
)
EMBEDDINGS_DIR = os.environ.get(
    "EMBEDDINGS_DIR",
    os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "manual_embeddings")),
)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "snowflake-arctic-embed2:latest")

MIN_PAGE_CHARS = 100  # 이 미만은 표지/빈 페이지로 간주

# [E-025 review #2] 인덱싱에서 제외할 파일명 패턴 (case insensitive substring)
# master-k: 스캔 이미지 PDF라 pypdf extract_text가 빈 결과 — OCR 없이는 사용 불가
SKIP_FILENAME_PATTERNS = [
    "master-k",
]

DB_HOST = os.environ.get("DB_HOST", "timescaledb")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "slm_dev_1234")


def _db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )


# ─────────────────────────────────────────────────────────────────────
# 파일명 → 메타 (equipment_type / brand / model) 매핑
# ─────────────────────────────────────────────────────────────────────

def _infer_meta(filename: str) -> dict:
    """파일명 패턴에서 equipment_type / brand / model / title 추출."""
    name = filename.replace(".pdf", "")

    meta = {"equipment_type": "기타", "brand": None, "model": None, "title": name}

    lower = name.lower()

    # LS ELECTRIC 계열
    if lower.startswith(("xgb", "xgt", "xgr", "xgl", "xgf", "xgk", "xgp")) or "xgt" in lower or "xgb" in lower:
        meta["brand"] = "LS ELECTRIC"
        meta["equipment_type"] = "PLC"
        # 모델명 추출
        m = re.match(r"(X[A-Z]+[-_][A-Z0-9]+)", name.upper())
        if m:
            meta["model"] = m.group(1).replace("_", "-")
        elif "XGT" in name.upper():
            meta["model"] = "XGT Series"
        elif "XGB" in name.upper():
            meta["model"] = "XGB Series"

    # GLOFA / master-k
    if "glofa" in lower:
        meta["brand"] = "LS ELECTRIC"
        meta["equipment_type"] = "PLC"
        meta["model"] = "GLOFA GM"
    if "master-k" in lower or "master_k" in lower:
        meta["brand"] = "LS ELECTRIC"
        meta["equipment_type"] = "PLC"
        meta["model"] = "Master-K"

    # 인버터: G100, iS7
    if "g100" in lower or "is7" in lower:
        meta["brand"] = "LS ELECTRIC"
        meta["equipment_type"] = "인버터"
        meta["model"] = "G100" if "g100" in lower else "iS7"

    # AC&T System 계열
    if "iiot rtu" in lower or "rtu hw" in lower:
        meta["brand"] = "AC&T System"
        meta["equipment_type"] = "RTU"
        meta["model"] = "IIoT RTU"
    if "etos" in lower:
        meta["brand"] = "AC&T System"
        meta["equipment_type"] = "RTU"
        meta["model"] = "ETOS-XP"
    if "etherfos" in lower:
        meta["brand"] = "AC&T System"
        meta["equipment_type"] = "모뎀"  # 이더넷 광통신 스위치
        meta["model"] = "EtherFOS-EZ"
    if "4g-210n" in lower or "rcs-xg" in lower or "4g_210" in lower or name.startswith("4G"):
        meta["brand"] = "AC&T System"
        meta["equipment_type"] = "모뎀"
        meta["model"] = "4G-210N"

    return meta


# ─────────────────────────────────────────────────────────────────────
# PDF → 페이지별 텍스트
# ─────────────────────────────────────────────────────────────────────

def _extract_pages(pdf_path: str) -> list[tuple[int, str]]:
    """PDF → [(page_num, text)]. 100자 미만 페이지 스킵."""
    reader = pypdf.PdfReader(pdf_path)
    out = []
    for i in range(len(reader.pages)):
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception as e:
            logger.warning(f"page {i+1} extract 실패: {e}")
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= MIN_PAGE_CHARS:
            out.append((i + 1, text))
    return out


# ─────────────────────────────────────────────────────────────────────
# Ollama 임베딩
# ─────────────────────────────────────────────────────────────────────

def _embed_batch(texts: list[str]) -> np.ndarray:
    """Ollama /api/embed 배치 호출. (N, dim) float32 반환."""
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
    embs = np.array(data["embeddings"], dtype=np.float32)
    # L2 normalize (cosine similarity = dot product)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs = embs / norms
    return embs


# ─────────────────────────────────────────────────────────────────────
# [E-025 review #5] 단일 PDF 인덱싱 — 관리자 업로드 endpoint에서 재사용
# ─────────────────────────────────────────────────────────────────────

def index_single_pdf(
    src_path: str,
    filename: str,
    conn,
    meta_override: dict | None = None,
    uploaded_by: str = "admin_upload",
) -> dict:
    """단일 PDF를 추출·임베딩·저장·DB UPSERT. 기존 main() 루프 로직을 재사용.

    Args:
        src_path: 추출할 PDF 절대 경로
        filename: title 추론용 원본 파일명
        conn: psycopg2 connection (커밋은 호출자 책임)
        meta_override: `_infer_meta` 결과를 덮어쓸 필드 (equipment_type/brand/model/title)
        uploaded_by: tb_equipment_manual.uploaded_by

    Returns:
        {"status": "ok"|"skipped"|"empty", "manual_id": int?, "page_count": int, "chunk_count": int, "title": str}

    Raises:
        ValueError: 빈 매뉴얼(0 페이지) 또는 임베딩 실패
    """
    os.makedirs(EMBEDDINGS_DIR, exist_ok=True)
    os.makedirs(MANUALS_DEST_DIR, exist_ok=True)

    meta = _infer_meta(filename)
    if meta_override:
        meta.update({k: v for k, v in meta_override.items() if v})

    cur = conn.cursor()

    # 기존 row 확인 (title 매칭)
    cur.execute(
        "SELECT manual_id, embedding_key FROM tb_equipment_manual WHERE title = %s",
        (meta["title"],),
    )
    existing = cur.fetchone()
    manual_id_existing: Optional[int] = None
    if existing:
        mid, existing_key = existing
        existing_npz = os.path.join(EMBEDDINGS_DIR, f"{existing_key}.npz") if existing_key else None
        if existing_npz and os.path.exists(existing_npz):
            cur.close()
            return {
                "status": "skipped",
                "manual_id": int(mid),
                "title": meta["title"],
                "reason": "이미 인덱싱됨",
            }
        manual_id_existing = int(mid)

    # 1. 페이지 추출
    pages = _extract_pages(src_path)
    if not pages:
        cur.close()
        return {"status": "empty", "title": meta["title"], "reason": "텍스트 추출 불가"}

    # 2. 청크 분할 (긴 페이지 overlap)
    chunks: list[tuple[int, str]] = []
    for page_num, text in pages:
        if len(text) > 3000:
            start = 0
            while start < len(text):
                chunk = text[start : start + 1500]
                if len(chunk) >= MIN_PAGE_CHARS:
                    chunks.append((page_num, chunk))
                start += 1200
        else:
            chunks.append((page_num, text))

    # 3. 임베딩
    BATCH = 16
    all_embs = []
    for i in range(0, len(chunks), BATCH):
        batch_texts = [c[1] for c in chunks[i : i + BATCH]]
        embs = _embed_batch(batch_texts)
        all_embs.append(embs)
    embeddings = np.concatenate(all_embs, axis=0)

    # 4. 파일 복사 + npz 저장
    import shutil as _sh
    dest_filename = filename.replace(" ", "_")
    dest_path = os.path.join(MANUALS_DEST_DIR, dest_filename)
    if not os.path.exists(dest_path):
        _sh.copyfile(src_path, dest_path)
    file_url = f"/api/proxy/files/manual/{dest_filename}"

    embedding_key = re.sub(r"[^\w\-]", "_", meta["title"])[:80]
    npz_path = os.path.join(EMBEDDINGS_DIR, f"{embedding_key}.npz")
    np.savez_compressed(
        npz_path,
        embeddings=embeddings.astype(np.float32),
        pages=np.array([c[0] for c in chunks], dtype=np.int32),
        texts=np.array([c[1] for c in chunks], dtype=object),
    )

    # 5. DB UPSERT
    if manual_id_existing:
        cur.execute(
            """
            UPDATE tb_equipment_manual
            SET equipment_type=%s, brand=%s, model=%s, file_url=%s,
                page_count=%s, embedding_key=%s
            WHERE manual_id=%s
            """,
            (meta["equipment_type"], meta["brand"], meta["model"],
             file_url, len(pages), embedding_key, manual_id_existing),
        )
        manual_id = manual_id_existing
    else:
        cur.execute(
            """
            INSERT INTO tb_equipment_manual
                (equipment_type, brand, model, title, file_url, page_count, embedding_key, uploaded_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING manual_id
            """,
            (meta["equipment_type"], meta["brand"], meta["model"],
             meta["title"], file_url, len(pages), embedding_key, uploaded_by),
        )
        manual_id = int(cur.fetchone()[0])

    conn.commit()
    cur.close()
    return {
        "status": "ok",
        "manual_id": manual_id,
        "title": meta["title"],
        "page_count": len(pages),
        "chunk_count": len(chunks),
        "equipment_type": meta["equipment_type"],
        "brand": meta["brand"],
        "model": meta["model"],
        "file_url": file_url,
    }


# ─────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(EMBEDDINGS_DIR, exist_ok=True)
    os.makedirs(MANUALS_DEST_DIR, exist_ok=True)

    if not os.path.isdir(MANUALS_SRC_DIR):
        logger.error(f"MANUALS_SRC_DIR 없음: {MANUALS_SRC_DIR}")
        sys.exit(1)

    pdfs = sorted([f for f in os.listdir(MANUALS_SRC_DIR) if f.lower().endswith(".pdf")])
    # SKIP 패턴 필터링 (review-items #2)
    filtered_pdfs = []
    for f in pdfs:
        lower = f.lower()
        if any(pat.lower() in lower for pat in SKIP_FILENAME_PATTERNS):
            logger.info(f"SKIP (exclude 패턴): {f}")
            continue
        filtered_pdfs.append(f)
    pdfs = filtered_pdfs
    logger.info(f"대상 PDF: {len(pdfs)}개 (exclude 패턴 적용 후)")

    conn = _db()
    cur = conn.cursor()

    total_pages = 0
    total_chunks = 0
    skipped = 0

    for filename in pdfs:
        src_path = os.path.join(MANUALS_SRC_DIR, filename)
        meta = _infer_meta(filename)

        # 기존 인덱스 확인 (title 기반 idempotent)
        cur.execute(
            "SELECT manual_id, embedding_key FROM tb_equipment_manual WHERE title = %s",
            (meta["title"],),
        )
        existing = cur.fetchone()
        if existing:
            manual_id, existing_key = existing
            existing_npz = os.path.join(EMBEDDINGS_DIR, f"{existing_key}.npz") if existing_key else None
            if existing_npz and os.path.exists(existing_npz):
                logger.info(f"SKIP (이미 인덱싱됨): {filename} → manual_id={manual_id}")
                skipped += 1
                continue
            # npz만 없으면 재생성
            logger.info(f"RE-EMBED (npz 없음): {filename}")
            manual_id_existing = manual_id
        else:
            manual_id_existing = None

        # 1. 페이지 추출
        logger.info(f"[{filename}] extract...")
        t0 = time.time()
        pages = _extract_pages(src_path)
        logger.info(f"  {len(pages)} pages (≥{MIN_PAGE_CHARS} chars), {int((time.time()-t0)*1000)}ms")

        if not pages:
            logger.warning(f"  빈 매뉴얼 — 스킵")
            continue

        # 청크 크기 제한 (임베딩 토큰 한계 회피 — 단일 페이지 3000자로 자름)
        chunks = []
        for page_num, text in pages:
            if len(text) > 3000:
                # 긴 페이지는 1500자 overlap 300으로 분할
                start = 0
                while start < len(text):
                    chunk = text[start : start + 1500]
                    if len(chunk) >= MIN_PAGE_CHARS:
                        chunks.append((page_num, chunk))
                    start += 1200  # 300자 overlap
            else:
                chunks.append((page_num, text))

        # 2. 임베딩 (배치)
        logger.info(f"  embedding {len(chunks)} chunks...")
        t0 = time.time()
        BATCH = 16
        all_embs = []
        for i in range(0, len(chunks), BATCH):
            batch_texts = [c[1] for c in chunks[i : i + BATCH]]
            embs = _embed_batch(batch_texts)
            all_embs.append(embs)
        embeddings = np.concatenate(all_embs, axis=0)
        logger.info(f"  embedded {embeddings.shape}, {int((time.time()-t0)*1000)}ms")

        # 3. 파일 복사 + npz 저장
        dest_filename = filename.replace(" ", "_")
        dest_path = os.path.join(MANUALS_DEST_DIR, dest_filename)
        if not os.path.exists(dest_path):
            import shutil
            shutil.copyfile(src_path, dest_path)

        file_url = f"/api/proxy/files/manual/{dest_filename}"

        embedding_key = re.sub(r"[^\w\-]", "_", meta["title"])[:80]
        npz_path = os.path.join(EMBEDDINGS_DIR, f"{embedding_key}.npz")
        np.savez_compressed(
            npz_path,
            embeddings=embeddings.astype(np.float32),
            pages=np.array([c[0] for c in chunks], dtype=np.int32),
            texts=np.array([c[1] for c in chunks], dtype=object),
        )
        logger.info(f"  saved {npz_path}")

        # 4. DB UPSERT
        if manual_id_existing:
            cur.execute(
                """
                UPDATE tb_equipment_manual
                SET equipment_type=%s, brand=%s, model=%s, file_url=%s,
                    page_count=%s, embedding_key=%s
                WHERE manual_id=%s
                """,
                (
                    meta["equipment_type"], meta["brand"], meta["model"],
                    file_url, len(pages), embedding_key, manual_id_existing,
                ),
            )
            logger.info(f"  UPDATE manual_id={manual_id_existing}")
        else:
            cur.execute(
                """
                INSERT INTO tb_equipment_manual
                    (equipment_type, brand, model, title, file_url, page_count, embedding_key, uploaded_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING manual_id
                """,
                (
                    meta["equipment_type"], meta["brand"], meta["model"],
                    meta["title"], file_url, len(pages), embedding_key, "index_manuals",
                ),
            )
            new_id = cur.fetchone()[0]
            logger.info(f"  INSERT manual_id={new_id}")

        conn.commit()
        total_pages += len(pages)
        total_chunks += len(chunks)

    cur.close()
    conn.close()

    logger.info(
        f"완료: {len(pdfs) - skipped}개 신규 인덱싱 / {skipped}개 스킵 / "
        f"총 {total_pages} 페이지 / {total_chunks} 청크"
    )


if __name__ == "__main__":
    main()
