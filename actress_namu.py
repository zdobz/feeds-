import os
import re
import sys
import sqlite3

# Windows 콘솔 한글 깨짐 방지
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = r"D:\feeds\videos.db"
NAMU_LINK_TXT_PATH = r"D:\feeds\namu_link.txt"

def init_korean_name_columns(db_path):
    """feeds 테이블에 korean_name 및 namu_link 컬럼이 없는 경우 동적 추가"""
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(feeds)")
    cols = [col[1] for col in cur.fetchall()]
    
    if "korean_name" not in cols:
        try:
            print("[정보] feeds 테이블에 korean_name 컬럼을 추가합니다.")
            cur.execute("ALTER TABLE feeds ADD COLUMN korean_name TEXT")
            conn.commit()
        except sqlite3.OperationalError as e:
            print(f"[경고] korean_name 컬럼 추가 실패: {e}")
            
    if "namu_link" not in cols:
        try:
            print("[정보] feeds 테이블에 namu_link 컬럼을 추가합니다.")
            cur.execute("ALTER TABLE feeds ADD COLUMN namu_link TEXT")
            conn.commit()
        except sqlite3.OperationalError as e:
            print(f"[경고] namu_link 컬럼 추가 실패: {e}")
            
    conn.close()

def clean_japanese_name(name):
    """일어명에서 불필요한 괄호, 한자 표기 중복, 공백 등을 제거하여 매칭 정밀도 향상"""
    name = re.sub(r'\(.*?\)', '', name)
    name = re.sub(r'\s+', '', name)
    return name.strip()

def load_db_actresses(db_path):
    """DB feeds 테이블에서 기존 배우 목록 조회"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, title, english_name FROM feeds WHERE section = 'adult'")
    rows = cur.fetchall()
    conn.close()
    return rows

def update_korean_names_bulk(db_path, match_results):
    """매칭된 결과를 트랜잭션을 적용해 효율적으로 대량 업데이트"""
    if not match_results:
        return
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.executemany("UPDATE feeds SET korean_name = ?, namu_link = ? WHERE id = ?", match_results)
        conn.commit()
        print(f"[성공] 총 {len(match_results)}명의 한글명 및 나무위키 주소를 데이터베이스에 반영했습니다.")
    except Exception as e:
        print(f"[오류] DB 일괄 업데이트 실패: {e}")
        conn.rollback()
    finally:
        conn.close()

def main():
    print("[시작] 정형화된 namu_link.txt 데이터를 로드하여 DB 한글명 매칭 및 업데이트를 진행합니다.")
    init_korean_name_columns(DB_PATH)
    
    if not os.path.exists(NAMU_LINK_TXT_PATH):
        print(f"[오류] 정리 완료된 텍스트 파일 {NAMU_LINK_TXT_PATH}이 존재하지 않습니다.")
        return
        
    db_actresses = load_db_actresses(DB_PATH)
    print(f"-> DB 등록 배우 수: {len(db_actresses)}명")
    
    extracted_data = []
    # 정리된 한글||영어||일어||링크 형식 파싱
    with open(NAMU_LINK_TXT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("||")
            if len(parts) >= 4:
                ko = parts[0].strip()
                en = parts[1].strip()
                ja = parts[2].strip()
                namu_link = parts[3].strip()
                extracted_data.append((ko, en, ja, namu_link))
                
    print(f"-> 정형 파일 내 로드된 배우 수: {len(extracted_data)}명")
    
    match_results = []
    matched_set = set()
    
    for ko_name, wiki_en, wiki_ja, namu_link_url in extracted_data:
        match_found = False
        
        for db_id, db_title, db_eng in db_actresses:
            if db_id in matched_set:
                continue
                
            db_titles = [clean_japanese_name(t) for t in db_title.split('/')]
            
            # 1. 일어명 매칭
            if wiki_ja and len(clean_japanese_name(wiki_ja)) >= 2:
                clean_wiki_ja = clean_japanese_name(wiki_ja)
                if any(clean_wiki_ja in t or t in clean_wiki_ja for t in db_titles if t):
                    match_results.append((ko_name, namu_link_url, db_id))
                    matched_set.add(db_id)
                    match_found = True
                    break
            
            # 2. 영어명 매칭
            if not match_found and wiki_en and db_eng:
                clean_wiki_en = wiki_en.lower().replace(" ", "").replace("-", "")
                clean_db_eng = db_eng.lower().replace(" ", "").replace("-", "")
                if len(clean_wiki_en) >= 3:
                    if clean_wiki_en == clean_db_eng:
                        match_results.append((ko_name, namu_link_url, db_id))
                        matched_set.add(db_id)
                        match_found = True
                        break
                    
    update_korean_names_bulk(DB_PATH, match_results)
    print(f"[완료] 총 매칭 성공 배우: {len(match_results)}명 / 미매칭 DB 배우: {len(db_actresses) - len(match_results)}명")

if __name__ == "__main__":
    main()
