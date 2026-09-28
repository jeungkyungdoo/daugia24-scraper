import os
import sys
import time
import json
import logging
import requests
import urllib3
from datetime import datetime

# 베트남 정부 사이트 SSL 경고 비활성화
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 로깅 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 1. Supabase 접속 인증키
DEFAULT_URL = "https://sznnlmtgoiqxgbhqjqfg.supabase.co"
DEFAULT_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InN6bm5sbXRnb2lxeGdiaHFqcWZnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg4NTM0ODUsImV4cCI6MjEwNDQyOTQ4NX0.r--e2DrkD3-kDxGsaNXD36ckv8f_r_BUwXNvEraCzuI"

SUPABASE_URL = os.environ.get("SUPABASE_URL") or DEFAULT_URL
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY") or DEFAULT_KEY

# 2. 진짜 원본 테이블인 'auctions' 전용 헤더
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

# 3. 법무부 차단 우회 브라우저 헤더
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

def normalize_to_real_auctions_table(raw):
    """
    대표님의 Supabase 진짜 원본 테이블(auctions)의 실제 컬럼명과 100% 일치 매핑
    """
    dgts_id = extract_field(raw, ["id", "taiSanId", "auctionId", "auctionInfoId", "dgtsId"])
    title = extract_field(raw, ["tenTaiSan", "title", "name", "propertyName", "tenThongBao"])
    
    if not title and not dgts_id:
        return None
        
    category = extract_field(raw, ["loaiTaiSan", "category", "categoryName", "nhomTaiSan"], "Đất/Nhà ở")
    address = extract_field(raw, ["diaChi", "tinhThanh", "province", "city"], "Toàn quốc")
    organizer = extract_field(raw, ["toChucDauGia", "organizer", "tenToChucDauGia", "companyName"], "Tổ chức hành nghề đấu giá")
    
    price_val = extract_field(raw, ["giaKhoiDiem", "startPrice", "price", "startingPrice"], "0")
    raw_digits = "".join([c for c in price_val if c.isdigit()])
    price_num = int(raw_digits) if raw_digits else 0
    price_str = f"{price_num:,} VNĐ".replace(",", ".") if price_num > 0 else "Thỏa thuận"

    deposit_val = extract_field(raw, ["tienDatTruoc", "deposit", "depositAmount"], "10% - 20%")

    date_raw = extract_field(raw, ["ngayDauGia", "auctionDate", "openDate", "auctionStartDate", "thoiGianDauGia"])
    iso_date, display_date = parse_date_clean(date_raw)
    
    deadline_raw = extract_field(raw, ["hanNopHoSo", "submitDeadline", "deadline", "thoiHanNopHoSo"], "")
    _, deadline_display = parse_date_clean(deadline_raw)

    today_str = datetime.now().strftime("%Y-%m-%d")
    status_tab = "OPEN"
    if iso_date:
        if iso_date < today_str:
            status_tab = "CLOSED"
        else:
            status_tab = "OPEN"
    else:
        status_tab = "UPCOMING"

    detail_link = f"https://dgts.moj.gov.vn/thong-tin-dau-gia/{dgts_id}" if dgts_id else "https://dgts.moj.gov.vn"
    doc_link = extract_field(raw, ["fileDinhKem", "linkDoc", "fileUrl"], f"https://dgts.moj.gov.vn/portal/exportWordThongBao?id={dgts_id}")

    # 실제 수파베이스 auctions 테이블 컬럼명 100% 매칭
    return {
        "DGTS ID": int(dgts_id) if dgts_id and dgts_id.isdigit() else None,
        "Tên tài sản đấu giá": title[:500],
        "Loại tài sản": category[:100],
        "NPL": "N",
        "Giá khởi điểm": price_str,
        "Tiền đặt trước": deposit_val,
        "Địa chỉ / Khu vực tài sản": address[:500],
        "Số tài sản trong thông báo": 1,
        "Thời hạn nộp hồ sơ": deadline_display or "Theo quy chế",
        "Thời gian tổ chức đấu giá": display_date or "Chưa có lịch",
        "Tên tổ chức đấu giá / Người có tài sản": organizer[:300],
        "Link chi tiết": detail_link,
        "Link xuất file Doc": doc_link,
        "status_tab": status_tab,
        "auction_date": iso_date,
        "result_status": "PENDING",
        "last_synced_at": datetime.utcnow().isoformat() + "Z"
    }

def push_to_supabase(records):
    if not records:
        return 0
        
    # [핵심] 뷰(View)가 아닌 진짜 원본 테이블 'auctions' 로 직접 전송
    url = f"{SUPABASE_URL}/rest/v1/auctions"
    batch_size = 50
    inserted_count = 0
    
    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        try:
            res = requests.post(url, json=chunk, headers=SUPABASE_HEADERS, timeout=30)
            if res.status_code in [200, 201]:
                inserted_count += len(chunk)
                logging.info(f"Pushed batch {i//batch_size + 1}: {len(chunk)} items to real table 'auctions' successfully.")
            else:
                logging.error(f"Supabase push error [{res.status_code}]: {res.text}")
        except Exception as e:
            logging.error(f"Supabase batch connection exception: {e}")
            
    return inserted_count

def run_pipeline():
    logging.info("=== Starting Daily Vietnam Auction Scraper to real table 'auctions' ===")
    all_clean_records = []
    
    # 1페이지부터 5페이지까지 최신 공고 추출 (약 150~200건)
    for p in range(1, 6):
        items = fetch_latest_auctions(page=p, page_size=35)
        logging.info(f"Page {p}: Found {len(items)} raw auction items from Ministry of Justice.")
        
        for item in items:
            normalized = normalize_to_real_auctions_table(item)
            if normalized:
                all_clean_records.append(normalized)
        time.sleep(1.5)

    if all_clean_records:
        logging.info(f"Total parsed records: {len(all_clean_records)}. Syncing to Supabase table 'auctions'...")
        total_pushed = push_to_supabase(all_clean_records)
        logging.info(f"=== Complete! Successfully updated {total_pushed} auction items into Supabase. ===")
    else:
        logging.warning("No records could be retrieved from dgts.moj.gov.vn. Check firewall / network status.")

if __name__ == "__main__":
    run_pipeline() 
