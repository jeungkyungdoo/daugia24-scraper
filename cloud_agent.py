import os
import re
import time
import json
import xml.etree.ElementTree as ET
from datetime import datetime
import requests
from google import genai
from google.genai import types

#===========================================
# [1. 대표님 설정 영역 - 완료됨]
#============================================ 
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
PB_URL = os.environ.get("PB_URL", "https://fly-pb.fly.dev")  # 대표님 PocketBase 서버 주소
PB_ADMIN_EMAIL = os.environ.get("PB_ADMIN_EMAIL", "")
PB_ADMIN_PASSWORD = os.environ.get("PB_ADMIN_PASSWORD", "")

# 수집할 베트남 핵심 부동산/경제/법률 RSS 채널 목록
RSS_FEEDS = [
    "https://vnexpress.net/rss/bat-dong-san.rss",   # VnExpress 부동산 (도시계획, 정책, 시장)
    "https://vnexpress.net/rss/kinh-doanh.rss",     # VnExpress 경제 (금리, 은행 부실채권, 거시경제)
    "https://vnexpress.net/rss/phap-luat.rss",      # VnExpress 법률 (토지법, 경매법, 시행령, 권리분쟁)
    "https://cafeland.vn/rss/tin-tuc.rss",          # CafeLand 부동산 전문 뉴스
]

# 걸러낼 핵심 키워드 (도시계획·인프라·NPL경매 + 부동산 법률·등기·세무)
TARGET_KEYWORDS = [
    # 도시계획 / 인프라 / 대형개발
    "quy hoạch", "vành đai", "cao tốc", "sân bay", "hạ tầng",
    "đô thị", "sáp nhập", "giải phóng mặt bằng", "đấu thầu", "vingroup",
    # 경매 / 부실채권 / 금융
    "đấu giá", "nợ xấu", "phát mãi", "ngân hàng", "lãi suất",
    "tín dụng", "thu giữ", "thi hành án", "giá đất", "bảng giá đất",
    # 부동산 관련 법률 / 시행령 / 등기 / 세무
    "luật đất đai", "luật nhà ở", "luật kinh doanh bất động sản",
    "luật đấu giá", "nghị định", "thông tư", "sổ đỏ", "sổ hồng",
    "cấp giấy chứng nhận", "thuế bất động sản", "tiền sử dụng đất",
    "tranh chấp đất", "kê biên", "cưỡng chế", "chính sách"
]

# 회당 최대 발행 건수
MAX_REPORTS_PER_RUN = 8


# ==============================================================================
# [2. PocketBase 관리자 인증 및 중복 체크]
# ==============================================================================
def get_pb_admin_token():
    endpoints = [
        f"{PB_URL}/api/collections/_superusers/auth-with-password",
        f"{PB_URL}/api/admins/auth-with-password"
    ]
    payload = {"identity": PB_ADMIN_EMAIL, "password": PB_ADMIN_PASSWORD}
    for url in endpoints:
        try:
            res = requests.post(url, json=payload, timeout=15)
            if res.status_code == 200:
                return res.json().get("token")
        except Exception:
            continue
    raise RuntimeError("PocketBase 관리자 로그인 실패: 비밀번호 또는 서버 상태를 확인하세요.")


def is_already_published(token, source_url):
    headers = {"Authorization": f"Bearer {token}"}
    filter_q = f'source_url="{source_url}"'
    url = f"{PB_URL}/api/collections/ai_insights/records?filter={requests.utils.quote(filter_q)}&perPage=1"
    res = requests.get(url, headers=headers, timeout=15)
    if res.status_code == 200:
        return res.json().get("totalItems", 0) > 0
    return False


# ==============================================================================
# [3. 베트남 핵심 뉴스 RSS 수집기]
# ==============================================================================
def clean_html(raw_html):
    text = re.sub(r"<[^>]+>", " ", raw_html or "")
    return re.sub(r"\s+", " ", text).strip()


def fetch_filtered_news():
    collected = []
    seen_links = set()

    for feed_url in RSS_FEEDS:
        try:
            res = requests.get(feed_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if res.status_code != 200:
                continue
            root = ET.fromstring(res.content)
            for item in root.findall(".//item"):
                title = clean_html(item.findtext("title"))
                link = (item.findtext("link") or "").strip()
                desc = clean_html(item.findtext("description"))

                if not title or not link or link in seen_links:
                    continue

                combined = f"{title} {desc}".lower()
                if any(kw in combined for kw in TARGET_KEYWORDS):
                    seen_links.add(link)
                    collected.append({
                        "title": title,
                        "link": link,
                        "description": desc
                    })
        except Exception as e:
            print(f"[RSS 수집 경고] {feed_url}: {e}")

    return collected


# ==============================================================================
# [4. Gemini AI 부동산·법률·경매 수석 애널리스트 분석 엔진]
# ==============================================================================
def analyze_with_gemini(client, article):
    prompt = f"""
Bạn là Chuyên gia Phân tích Pháp lý Bất động sản, Quy hoạch & Đấu giá Tài sản hàng đầu tại Việt Nam (DauGia24.com).
Hãy đọc tin tức gốc dưới đây và viết một báo cáo phân tích chuyên sâu bằng tiếng Việt chuẩn xác, sắc bén dành cho nhà đầu tư bất động sản & người tham gia đấu giá tài sản:

[Tin tức gốc]
- Tiêu đề gốc: {article['title']}
- Tóm tắt gốc: {article['description']}
- Nguồn: {article['link']}

Yêu cầu xuất ra ĐÚNG định dạng JSON với 5 trường sau (không giải thích thêm ngoài JSON):
{{
  "title": "Tiêu đề báo cáo phân tích hấp dẫn, súc tích (dưới 95 ký tự), nêu bật tác động pháp lý, quy hoạch hoặc cơ hội săn tài sản đấu giá",
  "category": "Chọn ĐÚNG 1 trong 5 mã sau: QUY_HOACH, HA_TANG, CANH_BAO, DU_AN, PHAP_LUAT (Nếu liên quan đến Luật Đất đai, Luật Đấu giá, Nghị định, Thông tư, Sổ đỏ/Sổ hồng, Thuế, Tranh chấp, Kê biên thì BẮT BUỘC chọn PHAP_LUAT)",
  "province": "Tên tỉnh/thành phố liên quan (ví dụ: Hà Nội, TP. Hồ Chí Minh, Đà Nẵng, Long An, Bình Dương, Đồng Nai... Nếu là chính sách/luật áp dụng chung cả nước thì ghi: Toàn quốc)",
  "summary": "Tóm tắt cốt lõi 2-3 câu ngắn gọn (khoảng 180-250 ký tự) để người đọc nắm ngay điểm mấu chốt",
  "risk_warning": "1 câu cảnh báo rủi ro cụ thể (bẫy pháp lý, tranh chấp, quy hoạch treo, lãi suất, thổi giá) giúp nhà đầu tư không mắc sai lầm",
  "content_html": "Bài phân tích chi tiết định dạng HTML sạch (chỉ dùng thẻ <p>, <ul>, <li>, <strong>) gồm 3 phần: 1) Điểm nhấn thông tin & Căn cứ pháp lý/quy hoạch; 2) Tác động thực tế đến mặt bằng giá & quyền lợi người mua; 3) Chiến lược săn tài sản đấu giá giá gốc an toàn."
}}
"""
    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3,
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            if attempt == 0 and "503" in str(e):
                time.sleep(3)
                continue
            raise e


# ==============================================================================
# [5. 메인 실행 파이프라인]
# ==============================================================================
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] DauGia24 AI 에이전트 가동 시작...")
    token = get_pb_admin_token()
    client = genai.Client(api_key=GEMINI_API_KEY)

    articles = fetch_filtered_news()
    print(f"수집된 핵심 후보 기사 (법률·규획·경매): {len(articles)}건")

    published_count = 0
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    for art in articles:
        if published_count >= MAX_REPORTS_PER_RUN:
            break

        if is_already_published(token, art["link"]):
            continue

        try:
            report = analyze_with_gemini(client, art)
            valid_cats = {"QUY_HOACH", "HA_TANG", "CANH_BAO", "DU_AN", "PHAP_LUAT"}
            cat = str(report.get("category", "QUY_HOACH")).upper().strip()
            if cat not in valid_cats:
                cat = "QUY_HOACH"

            payload = {
                "title": report.get("title", art["title"])[:200],
                "category": cat,
                "province": report.get("province", "Toàn quốc")[:80],
                "summary": report.get("summary", art["description"])[:500],
                "risk_warning": report.get("risk_warning", "")[:400],
                "content_html": report.get("content_html", f"<p>{art['description']}</p>"),
                "source_url": art["link"]
            }

            res = requests.post(
                f"{PB_URL}/api/collections/ai_insights/records",
                headers=headers,
                json=payload,
                timeout=15
            )
            if res.status_code in (200, 201):
                published_count += 1
                print(f"✅ 업로드 성공 [{payload['category']} | {payload['province']}]: {payload['title']}")
            else:
                print(f"❌ PocketBase 저장 실패 ({res.status_code}): {res.text}")

            time.sleep(2)
        except Exception as e:
            print(f"❌ AI 분석 오류: {e}")

    print(f"작업 완료! 신규 발행된 AI 인사이트 리포트: {published_count}건")


if __name__ == "__main__":
    main()
