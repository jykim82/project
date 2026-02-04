import json
import re
import psycopg2
from typing import Dict, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# =================================================
# FastAPI
# =================================================
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# =================================================
# Request Model
# =================================================
class AskRequest(BaseModel):
    user_question: str


# =================================================
# DB 설정
# =================================================
DB_HOST = "112.166.183.65"
DB_PORT = "25479"
DB_NAME = "postgres"
DB_USER = "postgres"
DB_PASSWORD = "DJpost0827///"


def execute_query(sql: str):
    if not sql.strip():
        raise ValueError("SQL is empty")

    conn, cur = None, None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cur = conn.cursor()
        cur.execute(sql)
        cols = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        return rows, cols
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# =================================================
# example3.json 로딩
# =================================================
with open("example3.json", "r", encoding="utf-8") as f:
    EXAMPLES = json.load(f)


# =================================================
# Site / Facility / Block 추출
# =================================================
SITE_KEYWORDS = ["신평", "송악1", "송악2", "행정", "합덕", "순성", "고대리", "남산1"]


def extract_site_name(text: str) -> str:
    for site in SITE_KEYWORDS:
        if site in text:
            return site
    return ""


def extract_block_level(text: str) -> str:
    for lvl in ["소블록", "중블록", "대블록"]:
        if lvl in text:
            return lvl
    return ""


def extract_facilitytype(text: str, block_level: str) -> str:
    """
    facilitytype은 SQL WHERE 조건에 직접 들어감
    """
    if block_level:
        return block_level
    if "배수지" in text:
        return "배수지"
    if "가압장" in text:
        return "가압장"
    if "감압" in text:
        return "감압시설"
    return ""


def extract_datainfo(text: str) -> str:
    if "압력" in text:
        return "압력"
    if "유량" in text:
        return "유량"
    if "수위" in text:
        return "수위"
    return ""


# =================================================
# 질문 정규화
# =================================================
def normalize_question(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(
        r"(의|은|는|이|가|을|를|에|에서|으로|와|과|도|만|까지|부터)",
        "",
        text
    )
    text = re.sub(r"[?!.]", "", text)
    return text


# =================================================
# 질문 → example 매칭
# =================================================
def match_example(user_question: str):
    normalized_user_q = normalize_question(user_question)

    site = extract_site_name(user_question)
    block_level = extract_block_level(user_question)
    facilitytype = extract_facilitytype(user_question, block_level)
    datainfo = extract_datainfo(user_question)

    print("QUESTION :", user_question)
    print("NORMALIZED:", normalized_user_q)
    print("SITENAME :", site)
    print("BLOCK    :", block_level)
    print("FACILITY :", facilitytype)
    print("DATAINFO :", datainfo)

    for ex in EXAMPLES:
        for q in ex.get("questions", []):
            if normalize_question(q) in normalized_user_q:
                sql = ex.get("sql", "").strip()
                if not sql:
                    return None, None, site

                sql = (
                    sql.replace("{sitename}", site)
                       .replace("{block_level}", block_level)
                       .replace("{facilitytype}", facilitytype)
                       .replace("{datainfo}", datainfo)
                )
                return sql, ex, site

    return None, None, site


# =================================================
# Answer Template 처리
# =================================================
def render_answer_template(template: str, value_map: dict) -> str:
    text = template
    for key, value in value_map.items():
        text = text.replace("{" + key + "}", "" if value is None else str(value))
    return text


def clean_answer(text: str) -> str:
    lines = []
    for line in text.split("\n"):
        if re.search(r"\{.*?\}", line):
            continue
        if "None" in line:
            continue
        if line.strip() == "":
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def agent_2_text_generation(rows, cols, ex):
    if not rows:
        return "조회된 데이터가 없습니다."

    value_map = dict(zip(cols, rows[0]))
    template = ex.get("answer_template")

    if template:
        return clean_answer(render_answer_template(template, value_map))

    return None


# =================================================
# /ask API
# =================================================
@app.post("/ask")
async def ask(req: AskRequest):
    sql, ex, site = match_example(req.user_question)

    if not sql:
        return {
            "status": "ERROR",
            "message": "질문을 이해하지 못했습니다."
        }

    rows, cols = execute_query(sql)
    answer = agent_2_text_generation(rows, cols, ex)

    resp = {
        "status": "OK",
        "sql": sql
    }

    if answer:
        resp["answer"] = answer
    else:
        resp["data"] = [dict(zip(cols, r)) for r in rows]

    return resp
