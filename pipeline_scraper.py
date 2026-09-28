import os
import sys
import time
import json
import logging
import requests
import urllib3
from datetime import datetime

# 베트남 정부 SSL 경고 무시
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 로깅 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 1. Supabase 환경변수 연결 (yml 파일과 100% 일치)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://sznnlmtgoiqxgbhqjqfg.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")

if not SUPABASE_KEY:
    logging.error("FATAL: SUPABASE_KEY is missing from environment variables!")
    sys.exit(1)

# 2. 웹사이트(index.html)와 완벽히 호환되는 auctions_web 테이블용 헤더
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

# 3. 법무부 서버 방화벽 우회 세션 헤더
API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json;charset=UTF-8",
    "Referer": "https://dgts.moj.gov.vn/",
    "Origin": "https://dgts.moj.gov.vn"
}

def parse_date_clean(val):
    if not val:
        return None, ""
    val_str = str(val).strip()
    for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"]:
        try:
            dt = datetime.strptime(val_str[:10], fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%d/%m/%Y")
        except Exception:
            continue
    return None, val_str

def extract_field(item, keys, default=""):
    for k in keys:
        if k in item and item[k] is not None:
            v = str(item[k]).strip()
            if v and v.lower() != "null":
                return v
    return default

def fetch_latest_auctions(page=1, page_size=40):
    url = "https://dgts.moj.gov.vn/api/auction/search"
    payload = {
        "page": page,
        "pageSize": page_size,
        "status": "",
        "keyword": "",
        "orderBy": "createdDate",
        "orderDirection": "desc"
    }
    
    # 3회 재시도 (방화벽 지연 대비)
    for attempt in range(1, 4):
        try:
            res = requests.post(url, json=payload, headers=API_HEADERS, timeout=25, verify=False)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, dict):
                    return data.get("items") or data.get("data") or data.get("content") or []
                elif isinstance(data, list):
                    return data
            else:
                logging.warning(f"[Attempt {attempt}] dgts HTTP {res.status_code}")
        except Exception as e:
            logging.warning(f"[Attempt {attempt}] Connection issue: {e}")
        time.sleep(2)
        
    return []

def normalize_to_auctions_web(raw):
    """
    대표님의 웹사이트(index.html)가 사용하는 `auctions_web` 테이블 스키마 규격으로 정밀 매핑
    """
    raw_id = extract_field(raw, ["id", "taiSanId", "auctionId", "auctionInfoId", "dgtsId"])
    title = extract_field(raw, ["tenTaiSan", "title", "name", "propertyName", "tenThongBao"])
    
    if not title and not raw_id:
        return None
        
    category = extract_field(raw, ["loaiTaiSan", "category", "categoryName", "nhomTaiSan"], "Quyền sử dụng đất")
    city = extract_field(raw, ["tinhThanh", "province", "city", "diaChi"], "Toàn quốc")
    org = extract_field(raw, ["toChucDauGia", "organizer", "tenToChucDauGia", "companyName"], "Tổ chức hành nghề đấu giá")
    
    price_val = extract_field(raw, ["giaKhoiDiem", "startPrice", "price", "startingPrice"], "0")
    raw_digits = "".join([c for c in price_val if c.isdigit()])
    price_num = int(raw_digits) if raw_digits else 0
    price_str = f"{price_num:,} VNĐ".replace(",", ".") if price_num > 0 else "Thỏa thuận"

    date_raw = extract_field(raw, ["ngayDauGia", "auctionDate", "openDate", "auctionStartDate", "thoiGianDauGia"])
    iso_date, display_date = parse_date_clean(date_raw)
    
    deadline_raw = extract_field(raw, ["hanNopHoSo", "submitDeadline", "deadline", "thoiHanNopHoSo"], "")
    _, deadline_display = parse_date_clean(deadline_raw)

    # 탭 상태 분기
    today_str = datetime.now().strftime("%Y-%m-%d")
    status_tab = "OPEN"
    if iso_date:
        if iso_date < today_str:
            status_tab = "CLOSED"
        else:
            status_tab = "OPEN"
    else:
        status_tab = "UPCOMING"

    detail_link = f"https://dgts.moj.gov.vn/thong-tin-dau-gia/{raw_id}" if raw_id else "https://dgts.moj.gov.vn"
    doc_link = extract_field(raw, ["fileDinhKem", "linkDoc", "fileUrl"], detail_link)

    return {
        "title": title[:500],
        "category": category[:100],
        "city": city[:200],
        "org": org[:300],
        "price": price_str,
        "auction_date": iso_date,
        "auction_time_raw": display_date or "Chưa có lịch",
        "submit_deadline": deadline_display or "Theo quy chế",
        "status_tab": status_tab,
        "link_detail": detail_link,
        "link_doc": doc_link
    }

def push_to_supabase(records):
    if not records:
        return 0
        
    # [핵심] 실제 웹사이트 테이블인 auctions_web 에 직접 삽입
    url = f"{SUPABASE_URL}/rest/v1/auctions_web"
    
    # 50개 단위 배치 처리
    batch_size = 50
    inserted_count = 0
    
    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        try:
            res = requests.post(url, json=chunk, headers=SUPABASE_HEADERS, timeout=30)
            if res.status_code in [200, 201]:
                inserted_count += len(chunk)
                logging.info(f"Pushed batch {i//batch_size + 1}: {len(chunk)} items to auctions_web successfully.")
            else:
                logging.error(f"Supabase push error [{res.status_code}]: {res.text}")
        except Exception as e:
            logging.error(f"Supabase batch connection exception: {e}")
            
    return inserted_count

def run_pipeline():
    logging.info("=== Starting Daily Vietnam Auction Scraper to auctions_web ===")
    all_clean_records = []
    
    # 1페이지부터 5페이지까지 최신 공고 추출 (약 150~200건)
    for p in range(1, 6):
        items = fetch_latest_auctions(page=p, page_size=35)
        logging.info(f"Page {p}: Found {len(items)} raw auction items from Ministry of Justice.")
        
        for item in items:
            normalized = normalize_to_auctions_web(item)
            if normalized:
                all_clean_records.append(normalized)
        time.sleep(1.5)

    if all_clean_records:
        logging.info(f"Total parsed records: {len(all_clean_records)}. Syncing to Supabase...")
        total_pushed = push_to_supabase(all_clean_records)
        logging.info(f"=== Complete! Successfully updated {total_pushed} auction items into DauGia24. ===")
    else:
        logging.warning("No records could be retrieved from dgts.moj.gov.vn. Check firewall / network status.")

if __name__ == "__main__":
    run_pipeline()
