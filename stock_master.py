"""
KIS 종목 마스터파일 다운로드 및 검색
- 하루 1회 자동 다운로드
- KOSPI + KOSDAQ 종목 통합 검색
"""

import os
import sqlite3
import zipfile
import io
from datetime import datetime, date
import requests

DB_PATH = os.path.expanduser("~/trading/stock_master.db")
LAST_UPDATE_PATH = os.path.expanduser("~/trading/stock_master_date.txt")

KOSPI_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
KOSDAQ_URL = "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip"


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            market TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON stocks(name)")
    conn.commit()
    conn.close()


def _needs_update():
    if not os.path.exists(LAST_UPDATE_PATH):
        return True
    if not os.path.exists(DB_PATH):
        return True
    with open(LAST_UPDATE_PATH, "r") as f:
        last = f.read().strip()
    return last != str(date.today())


def _save_update_date():
    with open(LAST_UPDATE_PATH, "w") as f:
        f.write(str(date.today()))


def _parse_master(data, market):
    """마스터파일 파싱 - 실제 구조 기반"""
    stocks = []
    lines = data.decode("cp949", errors="ignore").split("\n")
    for line in lines:
        line = line.rstrip("\r")
        if len(line) < 30:
            continue
        try:
            # 종목코드: 0~6자리
            code = line[0:6].strip()
            # ISIN: 9~21자리 (12자리) 건너뜀
            # 종목명: 21자리부터 공백 전까지
            rest = line[21:].strip()
            name = rest.split()[0].strip() if rest else ""
            if code.isdigit() and len(code) == 6 and name:
                stocks.append((code, name, market))
        except:
            continue
    return stocks


def _download_and_parse(url, market):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers, timeout=30)
    res.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        data = z.read(z.namelist()[0])
    return _parse_master(data, market)


def update_master():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 종목 마스터파일 업데이트 시작...")
    _init_db()
    try:
        kospi = _download_and_parse(KOSPI_URL, "KOSPI")
        print(f"  KOSPI: {len(kospi)}개 종목")
        kosdaq = _download_and_parse(KOSDAQ_URL, "KOSDAQ")
        print(f"  KOSDAQ: {len(kosdaq)}개 종목")
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM stocks")
        conn.executemany("INSERT OR REPLACE INTO stocks VALUES (?, ?, ?)", kospi + kosdaq)
        conn.commit()
        conn.close()
        _save_update_date()
        print(f"  총 {len(kospi)+len(kosdaq)}개 종목 저장 완료!")
        return True
    except Exception as e:
        print(f"  업데이트 실패: {e}")
        return False


def search_stocks(keyword, limit=8):
    if not os.path.exists(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH)
    try:
        if keyword.isdigit():
            cursor = conn.execute(
                "SELECT code, name, market FROM stocks WHERE code LIKE ? LIMIT ?",
                (keyword + "%", limit)
            )
        else:
            cursor = conn.execute(
                "SELECT code, name, market FROM stocks WHERE name LIKE ? LIMIT ?",
                ("%" + keyword + "%", limit)
            )
        return [{"code": r[0], "name": r[1], "market": r[2]} for r in cursor.fetchall()]
    except:
        return []
    finally:
        conn.close()


def ensure_updated():
    if _needs_update():
        return update_master()
    return True


if __name__ == "__main__":
    update_master()
    print("\n검색 테스트 - 삼성:")
    for r in search_stocks("삼성"):
        print(r)
