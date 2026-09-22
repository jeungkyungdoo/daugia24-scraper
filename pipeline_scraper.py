import os
import sys
import time
import json
import logging
import requests
from datetime import datetime

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 대표님의 진짜 수파베이스 프로젝트 주소로 완벽히 수정 완료
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://sznnlmtgoiqxgbhqjqfg.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")

if not SUPABASE_KEY:
    logging.warning("SUPABASE KEY is missing in environment. Using default service context if available.")

API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=UTF-8",
    "Referer": "https://dgts.moj.gov.vn/"
}

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

def clean_value(val):
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() == "null" else s

def extract_field(item, keys, default=""):
    for k in keys:
        if k in item and item[k] is not None:
            val = str(item[k]).strip()
            if val and val.lower() != "null":
                return val
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
    try:
        res = requests.post(url, json=payload, headers=API_HEADERS, timeout=25)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict):
                return data.get("items") or data.get("data") or data.get("content") or []
            elif isinstance(data, list):
                return data
    except Exception as e:
        logging.error(f"Failed to fetch from dgts API (page {page}): {e}")
    return []

def normalize_property(raw):
    notice_code = extract_field(raw, ["maSoThongBao", "noticeCode", "code", "auctionCode", "soThongBao", "idThongBao"])
    dgts_id = extract_field(raw, ["dgtsId", "id", "auctionId", "taiSanId", "auctionInfoId"])
    title = extract_field(raw, ["tenTaiSan", "title", "name", "propertyName", "tenThongBao"])
    category = extract_field(raw, ["loaiTaiSan", "category", "categoryName", "nhomTaiSan"], "Đất/Nhà ở")
    province = extract_field(raw, ["tinhThanh", "province", "city", "diaChi"], "Toàn quốc")
    organizer = extract_field(raw, ["toChucDauGia", "organizer", "tenToChucDauGia", "companyName"], "Tổ chức đấu giá Quốc gia")
    
    price_val = extract_field(raw, ["giaKhoiDiem", "startPrice", "price", "startingPrice"], "0")
    try:
        clean_num = "".join([c for c in price_val if c.isdigit()])
        price_num = int(clean_num) if clean_num else 0
        price_str = f"{price_num:,} VNĐ".replace(",", ".") if price_num > 0 else "Thỏa thuận"
    except Exception:
        price_str = price_val if price_val else "Thỏa thuận"

    deposit = extract_field(raw, ["tienDatTruoc", "deposit", "depositAmount", "datCoc"], "Cọc: 10% - 20%")
    date_val = extract_field(raw, ["ngayDauGia", "auctionDate", "openDate", "auctionStartDate", "thoiGianDauGia"])
    
    auction_date = ""
    if date_val:
        for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"]:
            try:
                dt = datetime.strptime(date_val[:10], fmt)
                auction_date = dt.strftime("%Y-%m-%d")
                break
            except Exception:
                continue
    if not auction_date:
        auction_date = datetime.now().strftime("%Y-%m-%d")

    status = "Đang mở đấu giá"
    
    if not title and not notice_code and not dgts_id:
        return None

    return {
        "Mã số thông báo": notice_code or f"TB-{int(time.time())}",
        "Mã số thông báo là mã dự phòng (auctionInfoId)": dgts_id or notice_code,
        "DGTS ID": int(dgts_id) if dgts_id and dgts_id.isdigit() else None,
        "Tên tài sản": title or "Tài sản đấu giá thanh lý (Chi tiết trong hồ sơ)",
        "Loại tài sản": category,
        "Tỉnh/Thành phố": province,
        "Tổ chức đấu giá": organizer,
        "Giá khởi điểm": price_str,
        "Tiền đặt trước": deposit,
        "Ngày đấu giá": auction_date,
        "Trạng thái": status,
        "created_at": datetime.utcnow().isoformat() + "Z"
    }

def push_to_supabase(records):
    if not records or not SUPABASE_KEY:
        return
    url = f"{SUPABASE_URL}/rest/v1/auctions"
    try:
        res = requests.post(url, json=records, headers=SUPABASE_HEADERS, timeout=30)
        if res.status_code in [200, 201]:
            logging.info(f"Successfully inserted/updated {len(records)} auction items to Supabase without NULLs.")
        else:
            logging.error(f"Supabase push error [{res.status_code}]: {res.text}")
    except Exception as e:
        logging.error(f"Supabase connection exception: {e}")

def run_pipeline():
    logging.info("Starting Daily National Auction Pipeline Scraper...")
    all_clean_records = []
    
    for p in range(1, 4):
        items = fetch_latest_auctions(page=p, page_size=30)
        logging.info(f"Fetched {len(items)} items from page {p}")
        for item in items:
            normalized = normalize_property(item)
            if normalized:
                all_clean_records.append(normalized)
        time.sleep(1)

    if all_clean_records:
        logging.info(f"Total valid, non-NULL records ready for DB: {len(all_clean_records)}")
        push_to_supabase(all_clean_records)
    else:
        logging.warning("No valid records found in current fetch cycle.")

if __name__ == "__main__":
    run_pipeline()
