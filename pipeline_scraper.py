import os
import sys
import time
import json
import logging
import requests
import urllib3
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DEFAULT_URL = "https://sznnlmtgoiqxgbhqjqfg.supabase.co"
DEFAULT_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InN6bm5sbXRnb2lxeGdiaHFqcWZnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg4NTM0ODUsImV4cCI6MjEwNDQyOTQ4NX0.r--e2DrkD3-kDxGsaNXD36ckv8f_r_BUwXNvEraCzuI"

SUPABASE_URL = os.environ.get("SUPABASE_URL") or DEFAULT_URL
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY") or DEFAULT_KEY

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi,en-US;q=0.9,en;q=0.8"
}

def clean_money(val):
    if not val:
        return "Thỏa thuận"
    digits = "".join([c for c in str(val) if c.isdigit()])
    if digits and int(digits) > 0:
        return f"{int(digits):,} VNĐ".replace(",", ".")
    return str(val)

def parse_date(date_str):
    if not date_str:
        return None, "Chưa có lịch"
    clean = str(date_str).strip()
    for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"]:
        try:
            dt = datetime.strptime(clean[:19], fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%d/%m/%Y")
        except Exception:
            continue
    return None, clean

def fetch_nationwide_auction_houses():
    all_properties = []
    
    target_sources = [
        {"name": "Đấu giá Lạc Việt (Toàn Quốc)", "url": "https://lacvietauction.vn/api/auction-assets?page=1&limit=40"},
        {"name": "Công ty Đấu giá Hợp danh Việt Nam", "url": "https://vpa.com.vn/api/auction/all-listings"},
        {"name": "Cổng Đấu giá Tài sản Quốc gia (Portal Mirror)", "url": "https://daugiaso5.vn/api/public/auctions"}
    ]

    for source in target_sources:
        try:
            res = requests.get(source["url"], headers=HEADERS, timeout=15, verify=False)
            if res.status_code == 200:
                data = res.json()
                items = data.get("items") or data.get("data") or data.get("list") or (data if isinstance(data, list) else [])
                logging.info(f"[{source['name']}] Successfully collected {len(items)} listings.")
                
                for item in items:
                    item["_source_org"] = source["name"]
                    all_properties.append(item)
            else:
                logging.warning(f"[{source['name']}] HTTP {res.status_code}")
        except Exception as e:
            logging.warning(f"Notice on [{source['name']}]: {str(e)[:60]}")
        time.sleep(1)

    return all_properties

def normalize_to_auctions(raw):
    title = raw.get("title") or raw.get("tenTaiSan") or raw.get("name") or raw.get("assetName")
    if not title:
        return None

    org = raw.get("organizer") or raw.get("toChucDauGia") or raw.get("_source_org") or "Tổ chức hành nghề đấu giá"
    cat = raw.get("category") or raw.get("loaiTaiSan") or "Quyền sử dụng đất & Bất động sản"
    addr = raw.get("address") or raw.get("diaChi") or raw.get("province") or "Toàn quốc"
    
    price_val = raw.get("startPrice") or raw.get("giaKhoiDiem") or raw.get("price") or "0"
    price_str = clean_money(price_val)
    deposit = clean_money(raw.get("deposit") or raw.get("tienDatTruoc") or "10% - 20%")

    date_raw = raw.get("auctionDate") or raw.get("ngayDauGia") or raw.get("openDate")
    iso_date, display_date = parse_date(date_raw)

    deadline_raw = raw.get("deadline") or raw.get("hanNopHoSo") or ""
    _, display_deadline = parse_date(deadline_raw)

    status_raw = str(raw.get("status") or raw.get("trangThai") or "").upper()
    status_tab = "OPEN"
    result_status = "PENDING"
    
    today_str = datetime.now().strftime("%Y-%m-%d")

    if "KHÔNG THÀNH" in status_raw or "HỦY" in status_raw or "FAILED" in status_raw:
        status_tab = "FAILED"
        result_status = "FAILED"
    elif "KẾT THÚC" in status_raw or "THÀNH CÔNG" in status_raw or "CLOSED" in status_raw or (iso_date and iso_date < today_str):
        status_tab = "CLOSED"
        result_status = "SUCCESS"
    else:
        status_tab = "OPEN"
        result_status = "PENDING"

    item_id = str(raw.get("id") or raw.get("assetId") or int(time.time()))
    detail_link = raw.get("url") or raw.get("link") or f"https://daugia24.com/detail/{item_id}"
    doc_link = raw.get("docUrl") or raw.get("fileDinhKem") or detail_link

    return {
        "DGTS ID": int(item_id) if item_id.isdigit() else None,
        "Tên tài sản đấu giá": str(title)[:500],
        "Loại tài sản": str(cat)[:100],
        "NPL": "N",
        "Giá khởi điểm": price_str,
        "Tiền đặt trước": deposit,
        "Địa chỉ / Khu vực tài sản": str(addr)[:500],
        "Số tài sản trong thông báo": 1,
        "Thời hạn nộp hồ sơ": display_deadline or "Theo quy chế",
        "Thời gian tổ chức đấu giá": display_date,
        "Tên tổ chức đấu giá / Người có tài sản": str(org)[:300],
        "Link chi tiết": detail_link,
        "Link xuất file Doc": doc_link,
        "status_tab": status_tab,
        "auction_date": iso_date,
        "result_status": result_status,
        "last_synced_at": datetime.utcnow().isoformat() + "Z"
    }

def push_to_supabase(records):
    if not records:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/auctions"
    batch_size = 50
    pushed = 0

    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        try:
            res = requests.post(url, json=chunk, headers=SUPABASE_HEADERS, timeout=30)
            if res.status_code in [200, 201]:
                pushed += len(chunk)
                logging.info(f"Successfully synced batch {i//batch_size + 1} ({len(chunk)} listings) to 'auctions'.")
            else:
                logging.error(f"Supabase error [{res.status_code}]: {res.text}")
        except Exception as e:
            logging.error(f"Push exception: {e}")
            
    return pushed

def run_pipeline():
    logging.info("=== Starting Nationwide Auction Houses Scraper (All Statuses) ===")
    raw_items = fetch_nationwide_auction_houses()
    logging.info(f"Total raw listings fetched across national auction houses: {len(raw_items)}")

    clean_records = []
    for raw in raw_items:
        normalized = normalize_to_auctions(raw)
        if normalized:
            clean_records.append(normalized)

    if clean_records:
        logging.info(f"Prepared {len(clean_records)} normalized auction listings (Open/Failed/Closed).")
        total = push_to_supabase(clean_records)
        logging.info(f"=== Complete! Successfully updated {total} properties to DauGia24. ===")
    else:
        logging.warning("No listings retrieved in this cycle.")

if __name__ == "__main__":
    run_pipeline()
