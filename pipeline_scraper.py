import os
import sys
import time
import re
import logging
import subprocess

# 1. 깃허브 환경에 beautifulsoup4 라이브러리 자동 설치
try:
    from bs4 import BeautifulSoup
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "beautifulsoup4"])
    from bs4 import BeautifulSoup

import requests
import urllib3
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 2. Supabase 접속 인증
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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"
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
    match = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", clean)
    if match:
        day, month, year = match.groups()
        try:
            dt = datetime(int(year), int(month), int(day))
            return dt.strftime("%Y-%m-%d"), dt.strftime("%d/%m/%Y")
        except Exception:
            pass
    return None, clean

def scrape_lacviet_auctions():
    items = []
    urls = [
        "https://lacvietauction.vn/tai-san-dau-gia",
        "https://lacvietauction.vn/tai-san-dau-gia?page=2"
    ]
    for url in urls:
        try:
            res = requests.get(url, headers=HEADERS, timeout=20, verify=False)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                cards = soup.select(".auction-item, .card-auction, .item-asset, .box-asset") or soup.find_all("div", class_=re.compile(r"item|product|auction"))
                for c in cards:
                    title_elem = c.find(["h3", "h4", "a"], class_=re.compile(r"title|name"))
                    if not title_elem:
                        continue
                    title = title_elem.get_text(strip=True)
                    if len(title) < 5:
                        continue
                    
                    price_elem = c.find(text=re.compile(r"VNĐ|Giá|đồng", re.I))
                    price = price_elem.strip() if price_elem else "Thỏa thuận"
                    
                    link_elem = c.find("a", href=True)
                    link = link_elem["href"] if link_elem else url
                    if not link.startswith("http"):
                        link = "https://lacvietauction.vn" + link

                    text_all = c.get_text()
                    items.append({
                        "title": title,
                        "price": price,
                        "address": "Toàn quốc",
                        "organizer": "Công ty Đấu giá Hợp danh Lạc Việt",
                        "status_text": text_all,
                        "url": link
                    })
        except Exception as e:
            logging.warning(f"Lạc Việt scrape notice: {e}")
        time.sleep(1)
    return items

def scrape_vpa_auctions():
    items = []
    target_url = "https://vpa.com.vn/danh-sach-tai-san"
    try:
        res = requests.get(target_url, headers=HEADERS, timeout=20, verify=False)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            rows = soup.find_all(["div", "article"], class_=re.compile(r"asset|item|auction"))
            for r in rows:
                t_el = r.find(["h2", "h3", "h4", "a"])
                if not t_el:
                    continue
                title = t_el.get_text(strip=True)
                if len(title) < 5:
                    continue
                
                link_el = r.find("a", href=True)
                link = link_el["href"] if link_el else target_url
                if not link.startswith("http"):
                    link = "https://vpa.com.vn" + link

                items.append({
                    "title": title,
                    "price": "Thỏa thuận",
                    "address": "Toàn quốc",
                    "organizer": "Công ty Đấu giá Hợp danh Việt Nam",
                    "status_text": r.get_text(),
                    "url": link
                })
    except Exception as e:
        logging.warning(f"VPA scrape notice: {e}")
    return items

def normalize_to_auctions(raw):
    title = raw.get("title")
    if not title:
        return None

    status_text = raw.get("status_text", "").upper()
    status_tab = "OPEN"
    result_status = "PENDING"

    # 신건, 유찰, 낙찰 탭 분류
    if any(k in status_text for k in ["KHÔNG THÀNH", "HỦY", "FAILED", "TẠM DỪNG"]):
        status_tab = "FAILED"
        result_status = "FAILED"
    elif any(k in status_text for k in ["KẾT THÚC", "ĐÃ BÁN", "THÀNH CÔNG", "CLOSED"]):
        status_tab = "CLOSED"
        result_status = "SUCCESS"
    else:
        status_tab = "OPEN"
        result_status = "PENDING"

    iso_date, display_date = parse_date(status_text)
    price_str = clean_money(raw.get("price"))

    return {
        "Tên tài sản đấu giá": str(title)[:500],
        "Loại tài sản": "Đất/Nhà ở",
        "NPL": "N",
        "Giá khởi điểm": price_str,
        "Tiền đặt trước": "10% - 20%",
        "Địa chỉ / Khu vực tài sản": raw.get("address", "Toàn quốc")[:500],
        "Số tài sản trong thông báo": 1,
        "Thời hạn nộp hồ sơ": "Theo quy chế hồ sơ",
        "Thời gian tổ chức đấu giá": display_date,
        "Tên tổ chức đấu giá / Người có tài sản": raw.get("organizer", "Tổ chức hành nghề đấu giá")[:300],
        "Link chi tiết": raw.get("url", "https://daugia24.com"),
        "Link xuất file Doc": raw.get("url", "https://daugia24.com"),
        "status_tab": status_tab,
        "auction_date": iso_date,
        "result_status": result_status,
        "last_synced_at": datetime.utcnow().isoformat() + "Z"
    }

def push_to_supabase(records):
    if not records:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/auctions"
    batch_size = 30
    pushed = 0

    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        try:
            res = requests.post(url, json=chunk, headers=SUPABASE_HEADERS, timeout=30)
            if res.status_code in [200, 201]:
                pushed += len(chunk)
                logging.info(f"Successfully pushed batch {i//batch_size + 1} ({len(chunk)} items) to 'auctions'.")
            else:
                logging.error(f"Supabase push error [{res.status_code}]: {res.text}")
        except Exception as e:
            logging.error(f"Supabase push exception: {e}")
            
    return pushed

def run_pipeline():
    logging.info("=== Starting Nationwide Auction Scraping (HTML Direct Parser) ===")
    all_raw = []
    
    # 1. 락비엣 경매 수집
    lacviet_items = scrape_lacviet_auctions()
    logging.info(f"Fetched from Lạc Việt: {len(lacviet_items)} items")
    all_raw.extend(lacviet_items)
    
    # 2. VPA 경매 수집
    vpa_items = scrape_vpa_auctions()
    logging.info(f"Fetched from VPA: {len(vpa_items)} items")
    all_raw.extend(vpa_items)

    clean_records = []
    for raw in all_raw:
        norm = normalize_to_auctions(raw)
        if norm:
            clean_records.append(norm)

    if clean_records:
        logging.info(f"Total valid parsed listings: {len(clean_records)}. Syncing to Supabase...")
        total = push_to_supabase(clean_records)
        logging.info(f"=== Complete! Synced {total} listings to 'auctions' table. ===")
    else:
        logging.warning("No listings could be parsed from web pages.")

if __name__ == "__main__":
    run_pipeline()
