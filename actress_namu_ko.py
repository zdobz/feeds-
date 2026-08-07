import os
import re
import sys
import time
import random
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

# Windows 콘솔 한글 깨짐 방지
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

try:
    from scrapling import Fetcher
except ImportError:
    Fetcher = None

NAMU_TXT_PATH = r"D:\feeds\namu.txt"
MAX_WORKERS = 15  # 최대 고속 병렬 스레드 개수
file_lock = threading.Lock()
progress_lock = threading.Lock()

# 나무위키 접속용 기본 헤더
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.8,en-US;q=0.5,en;q=0.3",
    "Connection": "keep-alive"
}

# 제외할 시스템 키워드 리스트
EXCLUDE_KEYWORDS = {
    "AV 여배우", "AV 배우", "AV배우", "쉬메일", "정보", "P짱", "분류:", "틀:", "파일:", 
    "사용자:", "나무위키:", "토론:", "휴지통", "삭제", "이름", "나이", "신체", "소속사",
    "다음", "이전", "목록", "문서", "분류"
}

# 실시간 진행 상황 트래킹용 공유 전역 변수
processed_count = 0
total_todo_count = 0

def get_html_with_fallback(url):
    """scrapling Fetcher를 우선 시도하고 실패하면 urllib로 시도"""
    if Fetcher:
        try:
            page = Fetcher.get(url, verify=False)
            if page.status == 200:
                return page.html_content
        except Exception:
            pass

    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.read().decode('utf-8', errors='ignore')
    except Exception:
        return None

def parse_namu_profile(html):
    """검증 완료된 알고리즘: 전각/반각 파이프라인 통합 및 다중 테이블 검색을 통한 영/일 정밀 추출"""
    if not html:
        return None, None
        
    soup = BeautifulSoup(html, 'html.parser')
    ja_name = None
    en_name = None
    
    tables = soup.find_all('table')
    for table in tables:
        rows = table.find_all('tr')
        for row in rows:
            row_text = row.get_text()
            row_text = row_text.replace("｜", "|")
            
            if "|" in row_text:
                parts = row_text.split("|")
                if len(parts) >= 2:
                    left_part = parts[0].strip()
                    right_part = parts[1].strip()
                    
                    cleaned_left = re.sub(r'[ㄱ-ㅎㅏ-ㅣ가-힣a-zA-Z\s\(\)\[\]]', '', left_part)
                    ja_match = re.findall(r'([ぁ-んァ-ヶ一-龠\u3000-\u303F]+)', cleaned_left)
                    if ja_match:
                        ja_name = max(ja_match, key=len)
                        
                    en_match = re.search(r'([A-Za-z\s\-]+)', right_part)
                    if en_match:
                        en_name = en_match.group(1).strip()
                        
        if ja_name and en_name:
            break
            
    # 폴백: 라벨-값 순회 대조
    if not ja_name or not en_name:
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                tds = row.find_all(['td', 'th'])
                if len(tds) >= 2:
                    label = tds[0].get_text(strip=True)
                    val = tds[1].get_text(strip=True)
                    
                    if any(x in label for x in ["본명", "원어명", "원어 표기", "이름"]):
                        val_clean = re.sub(r'[ㄱ-ㅎㅏ-ㅣ가-힣a-zA-Z\s\(\)]', '', val)
                        ja_match = re.findall(r'([ぁ-んァ-ヶ一-龠]+)', val_clean)
                        if ja_match:
                            ja_name = max(ja_match, key=len)
                            
                    if any(x in label for x in ["로마자", "영어명", "영어 표기", "영문명"]):
                        en_match = re.search(r'([A-Za-z\s\-]+)', val)
                        if en_match:
                            en_name = en_match.group(1).strip()
            if ja_name and en_name:
                break
                
    return ja_name, en_name

def collect_korean_names_from_category():
    """1단계: 나무위키 AV 여배우 분류 페이지에서 순수 배우 한글 이름 목록 정밀 수집"""
    print("[1단계] 나무위키 카테고리에서 순수 여배우 한글 이름 목록 수집을 시작합니다...")
    start_url = "https://namu.wiki/w/%EB%B6%84%EB%A5%98:AV%20%EC%97%AC%EB%B0%B0%EC%9A%B0"
    current_url = start_url
    all_names = set()
    
    page_count = 1
    while current_url:
        print(f"  -> 분류 페이지 {page_count} 조회 중...")
        html = get_html_with_fallback(current_url)
        if not html:
            break
            
        soup = BeautifulSoup(html, 'html.parser')
        a_tags = soup.find_all('a', href=True)
        next_url = None
        
        for a in a_tags:
            href = urllib.parse.unquote(a['href'])
            text = a.get_text().strip()
            
            # 다음 문서 링크 추적
            if ("다음 문서" in text or "다음" in text) and ("cfrom=" in href or "cuntil=" in href):
                next_url = urllib.parse.urljoin("https://namu.wiki", href)
                
            if href.startswith('/w/') and text:
                if any(k in text for k in EXCLUDE_KEYWORDS):
                    continue
                if re.search(r'^[ㄱ-ㅎㅏ-ㅣ가-힣A-Za-z\s\(\)\/]+$', text):
                    clean_name = re.sub(r'\(.*?\)', '', text).strip()
                    if len(clean_name) >= 2 and len(clean_name) <= 12:
                        all_names.add(clean_name)
                        
        if next_url == current_url or page_count > 15:
            break
        current_url = next_url
        page_count += 1
        time.sleep(1.0)
        
    print(f"[성공] 총 {len(all_names)}명의 순수 여배우 한글 이름을 재추출했습니다.")
    return sorted(list(all_names))

def process_actress_worker(ko_name):
    """멀티스레드 개별 배우 정보 수집 워커"""
    global processed_count
    
    # 동시 스파이크 접속으로 인한 차단 우회를 위해 스레드별 Jitter 적용
    time.sleep(random.uniform(0.3, 1.5))
    
    encoded_name = urllib.parse.quote(ko_name)
    url = f"https://namu.wiki/w/{encoded_name}"
    html = get_html_with_fallback(url)
    
    ja_name = ""
    en_name = ""
    success = False
    
    if html:
        ja_name, en_name = parse_namu_profile(html)
        ja_name = ja_name if ja_name else ""
        en_name = en_name if en_name else ""
        if ja_name or en_name:
            success = True
            
    with progress_lock:
        processed_count += 1
        percent = (processed_count / total_todo_count) * 100
        if success:
            print(f"[{processed_count}/{total_todo_count}] ({percent:.1f}%) '{ko_name}' 수집 -> [성공] 영어:{en_name} / 일어:{ja_name}")
        else:
            print(f"[{processed_count}/{total_todo_count}] ({percent:.1f}%) '{ko_name}' 수집 -> [실패/미매핑] 표 매칭 정보 혹은 페이지 없음")
            
    # 성공한 정보만 즉시 파일 끝에 안전하게 쓰기
    if success:
        with file_lock:
            with open(NAMU_TXT_PATH, "a", encoding="utf-8") as out_f:
                out_f.write(f"{ko_name}||{en_name}||{ja_name}\n")
                out_f.flush()
                
    return ko_name, success, en_name, ja_name

def main():
    global total_todo_count, processed_count
    
    # 0. namu.txt 확인 및 복구 여부 판정
    todo_names = []
    completed_lines = []
    
    need_korean_collect = True
    if os.path.exists(NAMU_TXT_PATH):
        with open(NAMU_TXT_PATH, "r", encoding="utf-8") as f:
            raw_lines = [line.strip() for line in f if line.strip()]
        if len(raw_lines) >= 800 and not any(x.startswith("이전") or x.startswith("다음") for x in raw_lines):
            need_korean_collect = False
            
    if need_korean_collect:
        korean_names = collect_korean_names_from_category()
        if not korean_names:
            print("[오류] 한글 이름 목록 복구에 실패했습니다.")
            return
        with open(NAMU_TXT_PATH, "w", encoding="utf-8") as f:
            for name in korean_names:
                f.write(name + "\n")
        todo_names = list(korean_names)
    else:
        for line in raw_lines:
            if "||" in line:
                parts = line.split("||")
                ko = parts[0].strip()
                en = parts[1].strip() if len(parts) > 1 else ""
                ja = parts[2].strip() if len(parts) > 2 else ""
                if en or ja:
                    completed_lines.append(line)
                else:
                    todo_names.append(ko)
            else:
                todo_names.append(line)
                
    print(f"\n[2단계] 영/일 상세 정보 수집을 개시합니다.")
    print(f"-> 이미 완료된 데이터: {len(completed_lines)}명")
    print(f"-> 수집 대상(미수집 및 실패 복구 대상) 수: {len(todo_names)}명")
    
    if not todo_names:
        print("[완료] 모든 배우의 수집이 완료되었습니다.")
        return
        
    # 기존 성공본만 우선 파일에 다시 쓰기
    with open(NAMU_TXT_PATH, "w", encoding="utf-8") as f:
        for line in completed_lines:
            f.write(line + "\n")
            
    total_todo_count = len(todo_names)
    processed_count = 0
    
    print(f"\n* 수집 중 Ctrl + C를 누르면 중단 시점까지의 수집 완료본이 즉시 파일에 최종 병합 및 저장됩니다.")
    print(f"* 최대 성능 {MAX_WORKERS}개 고속 멀티스레드 병렬 방식을 가동합니다.\n")
    
    success_results = []
    failed_names = []
    
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_actress_worker, name): name for name in todo_names}
            for future in as_completed(futures):
                ko_name, success, en_name, ja_name = future.result()
                if success:
                    success_results.append(f"{ko_name}||{en_name}||{ja_name}")
                else:
                    failed_names.append(ko_name)
                    
    except KeyboardInterrupt:
        print("\n\n[중단 감지] 작업이 수동으로 종료되었습니다. 현재까지의 데이터를 취합하여 안전하게 저장합니다...")
        
    finally:
        with file_lock:
            with open(NAMU_TXT_PATH, "r", encoding="utf-8") as f:
                saved_lines = [line.strip() for line in f if line.strip()]
                
            all_final_lines = list(saved_lines)
            
            processed_kos = {x.split("||")[0] for x in saved_lines}
            for name in todo_names:
                if name not in processed_kos:
                    all_final_lines.append(name)
                    
            unique_lines = sorted(list(set(all_final_lines)), key=lambda x: x.split("||")[0])
            
            with open(NAMU_TXT_PATH, "w", encoding="utf-8") as f:
                for line in unique_lines:
                    f.write(line + "\n")
                    
        print(f"[완료] 데이터 파일 D:\\feeds\\namu.txt 가 성공적으로 통합 업데이트 및 복원 완료되었습니다.")

if __name__ == "__main__":
    main()
