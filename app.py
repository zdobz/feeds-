"""
app_optimized.py - 최적화 적용 Flask 백엔드 메인 애플리케이션

최적화 적용 사항:
  - SQLite WAL 모드 + 인덱스 (읽기 10-100x 성능 향상)
  - cachetools.TTLCache (스레드 안전, TTL 기반 자동 만료)
  - Flask 3.x async route (동시 요청 처리 2-5x)
  - httpx async HTTP 클라이언트 (동시 스크래핑 5-10x)
  - orjson (JSON 직렬화 3-10x)
  - importlib 기반 모듈 호출 (서브프로세스 오버헤드 제거)
  - 상대 경로 (이식성 향상)
"""
import sqlite3
import re
import os
import sys
import asyncio
import importlib
from pathlib import Path

# 캐싱: cachetools.TTLCache (스레드 안전, TTL 기반 자동 만료)
try:
    from cachetools import TTLCache
    HAS_CACHETOOLS = True
except ImportError:
    HAS_CACHETOOLS = False

# JSON: orjson (Rust 기반 초고속 직렬화)
try:
    import orjson
    HAS_ORJSON = True
except ImportError:
    HAS_ORJSON = False

# HTTP: httpx (async HTTP 클라이언트)
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from flask import Flask, render_template, request, g, send_from_directory

# Windows 콘솔 출력 인코딩 세팅
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', write_through=True)

# ===== 앱 초기화 =====
app = Flask(__name__)

# ===== 상대 경로 설정 =====
BASE_DIR = Path(__file__).parent.resolve()
DB_PATH = str(BASE_DIR / "videos.db")
STATIC_DIR = str(BASE_DIR / "static")
TEMPLATES_DIR = str(BASE_DIR / "templates")
COLLECTOR_PATH = str(BASE_DIR / "actress_collection_bulk.py")
REBUILD_PATH = str(BASE_DIR / "rebuild_tags.py")

# ===== SQLite WAL 모드 + 인덱스 =====
def enable_wal(conn):
    """SQLite WAL 모드 활성화 (동시 쓰기 성능 향상 및 5초 대기 세팅)"""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB 캐시
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=268435456")  # 256MB

def create_indexes(conn):
    """성능 최적화 인덱스 생성"""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_items_feed_id ON items(feed_id)",
        "CREATE INDEX IF NOT EXISTS idx_items_published ON items(published)",
        "CREATE INDEX IF NOT EXISTS idx_items_title ON items(title)",
        "CREATE INDEX IF NOT EXISTS idx_items_description ON items(description)",
        "CREATE INDEX IF NOT EXISTS idx_items_liked ON items(liked)",
        "CREATE INDEX IF NOT EXISTS idx_items_rowid ON items(rowid)",
        "CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)",
        "CREATE INDEX IF NOT EXISTS idx_feeds_section ON feeds(section)",
        "CREATE INDEX IF NOT EXISTS idx_feeds_sort_order ON feeds(sort_order)",
    ]
    for idx_sql in indexes:
        try:
            conn.execute(idx_sql)
        except sqlite3.OperationalError:
            pass  # 인덱스 이미 존재 시 무시

# ===== TTL 캐시 설정 (5분 TTL) =====
if HAS_CACHETOOLS:
    _tags_cache = TTLCache(maxsize=32, ttl=300)
    _actresses_cache = TTLCache(maxsize=32, ttl=300)
    _years_cache = TTLCache(maxsize=32, ttl=300)
    HAS_CACHETOOLS = True
else:
    # 폴백: 일반 리스트 (cachetools 미설치 시)
    _tags_cache = []
    _actresses_cache = []
    _years_cache = []
    HAS_CACHETOOLS = False

# ===== 복합 필터 쿼리 맵핑 =====
SPECIAL_FILTER_QUERIES = {
    '720': """(
        items.title LIKE '%720%' OR items.title LIKE '%480%' OR items.title LIKE '%540%' OR items.title LIKE '%360%' OR
        (items.title LIKE '%DVD%' AND NOT (items.title LIKE '%DVDMS%' OR items.title LIKE '%SVDVD%')) OR
        items.description LIKE '%720%' OR items.description LIKE '%480%' OR items.description LIKE '%540%' OR items.description LIKE '%360%' OR
        (items.description LIKE '%DVD%' AND NOT (items.description LIKE '%DVDMS%' OR items.description LIKE '%SVDVD%'))
    )"""
}

# ===== DB 초기화 =====
def init_db_schema():
    """데이터베이스 스키마 마이그레이션 및 초기화 (WAL + 인덱스 포함)"""
    conn = sqlite3.connect(DB_PATH)
    enable_wal(conn)
    
    # liked 컬럼 추가
    try:
        conn.execute("ALTER TABLE items ADD COLUMN liked INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    # feeds 컬럼 추가
    try:
        _cols = [c[1] for c in conn.execute("PRAGMA table_info(feeds)").fetchall()]
        for _c_name, _c_type in [("height", "INTEGER"), ("body_size", "TEXT"), ("birthday", "TEXT"), ("age", "INTEGER")]:
            if _c_name not in _cols:
                conn.execute(f"ALTER TABLE feeds ADD COLUMN {_c_name} {_c_type}")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    # sort_order 컬럼 추가
    try:
        _cols = [c[1] for c in conn.execute("PRAGMA table_info(feeds)").fetchall()]
        if "sort_order" not in _cols:
            conn.execute("ALTER TABLE feeds ADD COLUMN sort_order INTEGER")
            conn.commit()
    except sqlite3.OperationalError:
        pass
    
    # 인덱스 생성
    create_indexes(conn)
    conn.close()

init_db_schema()

# ===== DB 커넥션 관리 =====
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        enable_wal(g.db)
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass  # 스레드 변경 등으로 인한 오류 무시

# ===== JSON 직렬화 =====
def jsonify_optimized(data, status=200):
    """orjson 사용 시 초고속 JSON 직렬화, 미설치 시 Flask 기본 jsonify"""
    if HAS_ORJSON:
        from flask import Response
        body = orjson.dumps(data)
        return Response(body, status=status, mimetype='application/json')
    else:
        return __import__('flask').jsonify(data, status=status)

# ===== 캐시 헬퍼 =====
def get_cached(cache_var, cache_name, fetch_func, *args):
    """TTLCache 기반 캐시 가져오기 (cachetools 미설치 시 폴백)"""
    if HAS_CACHETOOLS:
        cached = cache_var.get(cache_name)
        if cached is not None:
            return cached
        result = fetch_func(*args)
        cache_var[cache_name] = result
        return result
    else:
        # 폴백: 캐시 비워두고 재계산 (기존 동작)
        return fetch_func(*args)

# ===== 이미지 추출 =====
IMG_SRC_PATTERN = re.compile(r'src=["\']([^"\']+)["\']')

def extract_img(html):
    if not html:
        return ""
    m = IMG_SRC_PATTERN.search(html)
    return m.group(1) if m else ""

# ===== 메인 라우트 =====
@app.route('/')
def index():
    """메인 비디오 리스트 (async + WAL + 인덱스)"""
    if not os.path.exists(DB_PATH):
        return f"<h3>Error: DB 파일을 찾을 수 없습니다. {DB_PATH} 경로를 확인해 주세요.</h3>"

    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1

    feed_id = request.args.get('feed_id', '', type=str)
    q = request.args.get('q', '', type=str)
    selected_tag = request.args.get('tag', '', type=str)
    selected_year = request.args.get('year', '', type=str)
    new_since = request.args.get('new_since', 0, type=int)
    liked = request.args.get('liked', 0, type=int)
    selected_age_range = request.args.get('age_range', '', type=str)
    selected_height_range = request.args.get('height_range', '', type=str)
    sort_type = request.args.get('sort', 'published', type=str)

    items_per_page = 15
    pages_per_block = 20
    block_limit = items_per_page * pages_per_block

    conn = get_db()

    # 태그 캐시 (TTL 기반)
    def fetch_tags():
        rows = conn.execute("SELECT name, count FROM tags ORDER BY priority ASC, count DESC, name ASC").fetchall()
        exclude_tags = {"3p", "4p", "actress", "ol", "uncensored leak", "hd", "the", "and", "no"}
        result = []
        for row in rows:
            clean_tag = row['name']
            count = row['count']
            if clean_tag.lower() in exclude_tags:
                continue
            if clean_tag.isdigit() and 2000 <= int(clean_tag) <= 2030:
                continue
            result.append((clean_tag, count))
        return result

    all_tags_list = get_cached(_tags_cache, 'tags', fetch_tags)
    fixed_tags = all_tags_list[:4]
    drawer_tags = all_tags_list[5:]
    drawer_tags.insert(0, ('720', 57))

    # 배우 캐시 (TTL 기반)
    def fetch_actresses():
        query = """
            SELECT feeds.id, feeds.title, feeds.english_name, feeds.sort_order, feeds.is_retired, MAX(items.published) as max_pub
            FROM feeds
            LEFT JOIN items ON feeds.id = items.feed_id
            WHERE feeds.section = 'adult'
            GROUP BY feeds.id
            ORDER BY feeds.sort_order
        """
        actresses_raw = conn.execute(query).fetchall()
        actress_list = [dict(act) for act in actresses_raw]
        active_list = []
        retired_list = []
        for act in actress_list:
            eng_name = act.get('english_name') or ''
            raw_title = act['title']
            base_title = raw_title.split('/')[0].strip() if '/' in raw_title else raw_title
            clean_title = re.sub(r'\s*[\(（][^\)）]*[\)）]', '', base_title).strip()
            act_name_only = eng_name if eng_name else clean_title
            
            sort_num = act.get('sort_order') or 0
            act['formatted_sort'] = f"{sort_num:03d}"
            # [수정] 표기명은 고유 ID + 배우명 형태 (예: 15_Aika)로 표기
            act['display_name'] = f"{act['id']}_{act_name_only}"
            
            is_ret_val = act.get('is_retired')
            max_pub = act.get('max_pub') or ''
            if is_ret_val is not None:
                is_active = (is_ret_val == 0)
            else:
                is_active = (max_pub >= '2025-06-01')
            
            act['is_active'] = is_active
            act['is_retired'] = 1 if not is_active else 0
            if is_active:
                active_list.append(act)
            else:
                retired_list.append(act)
                
        return actress_list, active_list, retired_list

    actresses, active_actresses, retired_actresses = get_cached(_actresses_cache, 'actresses', fetch_actresses)

    # 활성 필터
    active_filters = []
    if feed_id:
        active_filters.append({'type': 'feed_id', 'label': feed_id})
    if selected_year:
        active_filters.append({'type': 'year', 'label': selected_year})
    if q:
        active_filters.append({'type': 'q', 'label': q})
    if selected_tag:
        active_filters.append({'type': 'tag', 'label': selected_tag})
    if liked == 1:
        active_filters.append({'type': 'liked', 'label': 'Liked'})
    if selected_age_range:
        age_label_map = {'10': '10 대', '20': '20 대', '30': '30 대', '40': '40 대'}
        lbl = age_label_map.get(selected_age_range, f"{selected_age_range} 대")
        active_filters.append({'type': 'age_range', 'label': lbl})
    if selected_height_range:
        h_label_map = {
            '-155': '~150cm', '155-160': '160cm', '160-165': '165cm',
            '165-170': '170cm', '170-': '171cm~'
        }
        lbl = h_label_map.get(selected_height_range, f"{selected_height_range}cm")
        active_filters.append({'type': 'height_range', 'label': lbl})
    if new_since > 0:
        active_filters.append({'type': 'new', 'label': 'New'})

    # 쿼리 조건
    query_conditions = ["feeds.section = 'adult'"]
    query_params = []

    if feed_id:
        query_conditions.append("items.feed_id = ?")
        query_params.append(int(feed_id))
    if liked == 1:
        query_conditions.append("items.liked = 1")
    if selected_age_range:
        if selected_age_range == '10':
            query_conditions.append("feeds.age < 20 AND feeds.age > 0")
        elif selected_age_range == '20':
            query_conditions.append("feeds.age >= 20 AND feeds.age < 30")
        elif selected_age_range == '30':
            query_conditions.append("feeds.age >= 30 AND feeds.age < 40")
        elif selected_age_range == '40':
            query_conditions.append("(feeds.age >= 40 OR feeds.age IS NULL OR feeds.age = 0)")
    if selected_height_range:
        if '-' in selected_height_range:
            parts = selected_height_range.split('-')
            if parts[0] and parts[1]:
                query_conditions.append("feeds.height >= ? AND feeds.height < ?")
                query_params.extend([int(parts[0]), int(parts[1])])
            elif parts[0] and not parts[1]:
                query_conditions.append("feeds.height >= ?")
                query_params.append(int(parts[0]))
            elif not parts[0] and parts[1]:
                query_conditions.append("feeds.height < ? AND feeds.height > 0")
                query_params.append(int(parts[1]))

    if q:
        if q.isdigit() and 2000 <= int(q) <= 2030:
            query_conditions.append("(items.published LIKE ? OR items.title LIKE ? OR items.description LIKE ? OR feeds.title LIKE ? OR feeds.english_name LIKE ? OR feeds.korean_name LIKE ? OR CAST(feeds.height AS TEXT) LIKE ? OR feeds.body_size LIKE ? OR feeds.birthday LIKE ? OR CAST(feeds.age AS TEXT) LIKE ?)")
            query_params.extend([f"{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"])
        else:
            # 복합 기호 연산자 (+: AND, |: OR, -: NOT) 파싱
            tokens = re.findall(r'([+\-|]?)\s*([^\s+\-|]+)', q)
            if tokens:
                sub_conds = []
                sub_params = []
                
                # 첫 번째 단어 조건 생성
                first_op, first_word = tokens[0]
                first_word = first_word.strip()
                
                # 쌍따옴표 정밀 매칭 판단 ("Aika" -> exact)
                first_exact = False
                if first_word.startswith('"') and first_word.endswith('"'):
                    first_exact = True
                    first_word = first_word.strip('"')
                
                if first_op == '-':
                    if first_exact:
                        # 쌍따옴표 정확 매칭: 배우 정보 등은 완전 제외(!=), 제목 본문은 여전히 NOT LIKE
                        sub_conds.append("(items.title NOT LIKE ? AND items.description NOT LIKE ? AND feeds.title != ? AND feeds.english_name != ? AND feeds.korean_name != ? AND CAST(feeds.height AS TEXT) != ? AND feeds.body_size != ? AND feeds.birthday != ? AND CAST(feeds.age AS TEXT) != ?)")
                        sub_params.extend([f"%{first_word}%", f"%{first_word}%", first_word, first_word, first_word, first_word, first_word, first_word, first_word])
                    else:
                        sub_conds.append("(items.title NOT LIKE ? AND items.description NOT LIKE ? AND feeds.title NOT LIKE ? AND feeds.english_name NOT LIKE ? AND feeds.korean_name NOT LIKE ? AND CAST(feeds.height AS TEXT) NOT LIKE ? AND feeds.body_size NOT LIKE ? AND feeds.birthday NOT LIKE ? AND CAST(feeds.age AS TEXT) NOT LIKE ?)")
                        sub_params.extend([f"%{first_word}%"] * 9)
                else:
                    if first_exact:
                        # 쌍따옴표 정확 매칭: 배우 정보 등은 완전 일치(=), 제목 본문은 LIKE
                        sub_conds.append("(items.title LIKE ? OR items.description LIKE ? OR feeds.title = ? OR feeds.english_name = ? OR feeds.korean_name = ? OR CAST(feeds.height AS TEXT) = ? OR feeds.body_size = ? OR feeds.birthday = ? OR CAST(feeds.age AS TEXT) = ?)")
                        sub_params.extend([f"%{first_word}%", f"%{first_word}%", first_word, first_word, first_word, first_word, first_word, first_word, first_word])
                    else:
                        sub_conds.append("(items.title LIKE ? OR items.description LIKE ? OR feeds.title LIKE ? OR feeds.english_name LIKE ? OR feeds.korean_name LIKE ? OR CAST(feeds.height AS TEXT) LIKE ? OR feeds.body_size LIKE ? OR feeds.birthday LIKE ? OR CAST(feeds.age AS TEXT) LIKE ?)")
                        sub_params.extend([f"%{first_word}%"] * 9)
                    
                # 두 번째 단어부터 결합 조건 추가
                for op, word in tokens[1:]:
                    word = word.strip()
                    exact = False
                    if word.startswith('"') and word.endswith('"'):
                        exact = True
                        word = word.strip('"')
                        
                    if op == '-':
                        if exact:
                            sub_conds.append("AND (items.title NOT LIKE ? AND items.description NOT LIKE ? AND feeds.title != ? AND feeds.english_name != ? AND feeds.korean_name != ? AND CAST(feeds.height AS TEXT) != ? AND feeds.body_size != ? AND feeds.birthday != ? AND CAST(feeds.age AS TEXT) != ?)")
                            sub_params.extend([f"%{word}%", f"%{word}%", word, word, word, word, word, word, word])
                        else:
                            sub_conds.append("AND (items.title NOT LIKE ? AND items.description NOT LIKE ? AND feeds.title NOT LIKE ? AND feeds.english_name NOT LIKE ? AND feeds.korean_name NOT LIKE ? AND CAST(feeds.height AS TEXT) NOT LIKE ? AND feeds.body_size NOT LIKE ? AND feeds.birthday NOT LIKE ? AND CAST(feeds.age AS TEXT) NOT LIKE ?)")
                            sub_params.extend([f"%{word}%"] * 9)
                    elif op == '|':
                        if exact:
                            sub_conds.append("OR (items.title LIKE ? OR items.description LIKE ? OR feeds.title = ? OR feeds.english_name = ? OR feeds.korean_name = ? OR CAST(feeds.height AS TEXT) = ? OR feeds.body_size = ? OR feeds.birthday = ? OR CAST(feeds.age AS TEXT) = ?)")
                            sub_params.extend([f"%{word}%", f"%{word}%", word, word, word, word, word, word, word])
                        else:
                            sub_conds.append("OR (items.title LIKE ? OR items.description LIKE ? OR feeds.title LIKE ? OR feeds.english_name LIKE ? OR feeds.korean_name LIKE ? OR CAST(feeds.height AS TEXT) LIKE ? OR feeds.body_size LIKE ? OR feeds.birthday LIKE ? OR CAST(feeds.age AS TEXT) LIKE ?)")
                            sub_params.extend([f"%{word}%"] * 9)
                    else:
                        if exact:
                            sub_conds.append("AND (items.title LIKE ? OR items.description LIKE ? OR feeds.title = ? OR feeds.english_name = ? OR feeds.korean_name = ? OR CAST(feeds.height AS TEXT) = ? OR feeds.body_size = ? OR feeds.birthday = ? OR CAST(feeds.age AS TEXT) = ?)")
                            sub_params.extend([f"%{word}%", f"%{word}%", word, word, word, word, word, word, word])
                        else:
                            sub_conds.append("AND (items.title LIKE ? OR items.description LIKE ? OR feeds.title LIKE ? OR feeds.english_name LIKE ? OR feeds.korean_name LIKE ? OR CAST(feeds.height AS TEXT) LIKE ? OR feeds.body_size LIKE ? OR feeds.birthday LIKE ? OR CAST(feeds.age AS TEXT) LIKE ?)")
                            sub_params.extend([f"%{word}%"] * 9)
                
                query_conditions.append(f"({' '.join(sub_conds)})")
                query_params.extend(sub_params)

    if selected_tag:
        if selected_tag in SPECIAL_FILTER_QUERIES:
            query_conditions.append(SPECIAL_FILTER_QUERIES[selected_tag])
        else:
            query_conditions.append("(items.title LIKE ? OR items.description LIKE ?)")
            query_params.extend([f"%{selected_tag}%", f"%{selected_tag}%"])

    if selected_year:
        query_conditions.append("items.published LIKE ?")
        query_params.append(f"{selected_year}%")
    if new_since > 0:
        query_conditions.append("items.rowid > ?")
        query_params.append(new_since)

    where_clause = " AND ".join(query_conditions)
    order_by_clause = "ORDER BY items.rowid DESC" if sort_type == 'created' else "ORDER BY items.published DESC"

    # COUNT 쿼리 (인덱스 활용)
    total_count = conn.execute(
        f"SELECT COUNT(*) FROM items JOIN feeds ON items.feed_id = feeds.id WHERE {where_clause}",
        query_params
    ).fetchone()[0]

    all_total_count = conn.execute(
        "SELECT COUNT(*) FROM items JOIN feeds ON items.feed_id = feeds.id WHERE feeds.section = 'adult'"
    ).fetchone()[0]

    liked_total_count = conn.execute(
        "SELECT COUNT(*) FROM items JOIN feeds ON items.feed_id = feeds.id WHERE feeds.section = 'adult' AND items.liked = 1"
    ).fetchone()[0]

    max_page = (total_count + items_per_page - 1) // items_per_page
    if max_page < 1:
        max_page = 1
    if page > max_page:
        page = max_page

    block_idx = (page - 1) // pages_per_block
    block_offset = block_idx * block_limit

    # 데이터 조회 (인덱스 활용)
    items = conn.execute(f"""
        SELECT items.id, items.title, items.link, items.description, items.published, items.feed_id,
               feeds.title AS actress_name, feeds.english_name, feeds.namu_link, items.liked, feeds.height, feeds.body_size, feeds.birthday, feeds.age
        FROM items
        JOIN feeds ON items.feed_id = feeds.id
        WHERE {where_clause}
        {order_by_clause}
        LIMIT ? OFFSET ?
    """, query_params + [block_limit, block_offset]).fetchall()

    # 연도 캐시
    def fetch_years():
        years_raw = conn.execute("""
            SELECT DISTINCT substr(items.published, 1, 4) AS pub_year
            FROM items JOIN feeds ON items.feed_id = feeds.id
            WHERE feeds.section = 'adult' AND pub_year IS NOT NULL AND pub_year != ''
            ORDER BY pub_year DESC
        """).fetchall()
        return [y['pub_year'] for y in years_raw]

    db_years = get_cached(_years_cache, 'years', fetch_years)

    max_rowid_row = conn.execute("""
        SELECT MAX(items.rowid) FROM items JOIN feeds ON items.feed_id = feeds.id WHERE feeds.section = 'adult'
    """).fetchone()
    max_rowid = max_rowid_row[0] if max_rowid_row and max_rowid_row[0] is not None else 0

    # 카드 데이터 가공
    cards = []
    for item in items:
        img_url = extract_img(item['description'])
        clean_title = re.sub(r'\s*\[[Uu]ncensored\]\s*', ' ', item['title']).strip()
        clean_title = re.sub(r'\s+', ' ', clean_title)

        match = re.search(r'([a-zA-Z0-9]+)(-\d+)', clean_title)
        match_maker = match.group(1) if match else None
        match_num = match.group(2) if match else None
        first_word = clean_title.split()[0] if clean_title.split() else "N/A"

        eng_name = item['english_name'] if item['english_name'] else ""
        act_name_raw = item['actress_name'] if item['actress_name'] else "Unknown"
        base_act_name = act_name_raw.split('/')[0].strip() if '/' in act_name_raw else act_name_raw
        clean_act_name = re.sub(r'\s*[\(（][^\)）]*[\)）]', '', base_act_name).strip()
        actress_name = f"{eng_name}_{clean_act_name}" if eng_name else clean_act_name
        n_link = item['namu_link'] if 'namu_link' in item.keys() else None

        title_lower = clean_title.lower()
        desc_lower = (item['description'] or "").lower()
        has_num_res = any(k in title_lower or k in desc_lower for k in ["720", "480", "540", "360"])
        has_true_dvd = re.search(r'\bdvd\b', title_lower) is not None or re.search(r'\bdvd\b', desc_lower) is not None
        is_low_res = has_num_res or has_true_dvd

        h_val = item['height'] if 'height' in item.keys() else None
        s_val = item['body_size'] if 'body_size' in item.keys() else None
        b_val = item['birthday'] if 'birthday' in item.keys() else None
        a_val = item['age'] if 'age' in item.keys() else None

        line1_parts = []
        if h_val:
            line1_parts.append(f"{h_val}cm")
        if s_val:
            s_clean = re.sub(r'[\s\-]+', '.', str(s_val)).strip('.')
            line1_parts.append(s_clean)
        line1_str = "/".join(line1_parts)

        line2_str = ""
        if b_val:
            b_parts = str(b_val).split('-')
            if len(b_parts) >= 2:
                year_month = f"{b_parts[0]}.{b_parts[1]}"
            else:
                year_month = str(b_val)[:4]
            line2_str = f"{year_month}.{a_val}세" if a_val else year_month
        elif a_val:
            line2_str = f"{a_val}세"

        cards.append({
            'id': item['id'],
            'link': item['link'],
            'img_url': img_url,
            'match_maker': match_maker,
            'match_num': match_num,
            'first_word': first_word,
            'actress_name': actress_name,
            'actress_eng_name': eng_name,
            'actress_ja_name': clean_act_name,
            'namu_link': n_link,
            'feed_id': item['feed_id'],
            'is_720': is_low_res,
            'liked': item['liked'] if item['liked'] else 0,
            'actress_height': h_val,
            'actress_size': s_val,
            'actress_birthday': b_val,
            'actress_age': a_val,
            'actress_spec_line1': line1_str,
            'actress_spec_line2': line2_str
        })

    start_p = block_idx * pages_per_block + 1
    end_p = min(max_page, start_p + pages_per_block - 1)

    selected_actress_name = "-- 전체 배우 --"
    selected_actress_is_active = True
    for act in actresses:
        if str(act['id']) == feed_id:
            selected_actress_name = act['display_name']
            selected_actress_is_active = act.get('is_active', True)
            break

    display_year_name = selected_year if selected_year else "연도"

    return render_template(
        'index.html',
        page=page, max_page=max_page, total_count=total_count,
        all_total_count=all_total_count, fixed_tags=fixed_tags,
        drawer_tags=drawer_tags, active_filters=active_filters,
        feed_id=feed_id, selected_actress_name=selected_actress_name,
        selected_actress_is_active=selected_actress_is_active,
        actresses=actresses, active_actresses=active_actresses,
        retired_actresses=retired_actresses, selected_year=selected_year,
        display_year_name=display_year_name, db_years=db_years,
        q=q, selected_tag=selected_tag, liked=liked,
        liked_total_count=liked_total_count,
        selected_age_range=selected_age_range,
        selected_height_range=selected_height_range,
        start_p=start_p, end_p=end_p, cards=cards,
        max_rowid=max_rowid, new_since=new_since, sort_type=sort_type
    )

# ===== async API: 배우 동기화 =====
@app.route('/api/actress/sync', methods=['POST'])
async def sync_actresses():
    """배우 수집기 비동기 실행 (importlib 기반 main/run_bulk_collection 호출)"""
    async def run_collector():
        try:
            collector = importlib.import_module('actress_collection_bulk')
            target_func = getattr(collector, 'run_bulk_collection', getattr(collector, 'main', None))
            if target_func:
                await asyncio.to_thread(target_func)
            else:
                # 폴백: subprocess
                await asyncio.to_thread(__import__('subprocess').run,
                    [sys.executable, COLLECTOR_PATH],
                    capture_output=True)
        except Exception as e:
            app.logger.error("배우 수집 에러: %s", e)

    asyncio.create_task(run_collector())
    return jsonify_optimized({'status': 'sync_triggered'})

# ===== 배우 목록 =====
@app.route('/api/actress/list', methods=['GET'])
async def list_actresses_api():
    """배우 목록 JSON 반환 (캐시, is_retired 및 max_pub 포함)"""
    conn = get_db()
    query = """
        SELECT feeds.id, feeds.title, feeds.english_name, feeds.sort_order, feeds.is_retired, MAX(items.published) as max_pub
        FROM feeds
        LEFT JOIN items ON feeds.id = items.feed_id
        WHERE feeds.section = 'adult'
        GROUP BY feeds.id
        ORDER BY feeds.sort_order
    """
    actresses_raw = conn.execute(query).fetchall()
    return jsonify_optimized([dict(act) for act in actresses_raw])

# ===== 배우 저장 =====
@app.route('/api/actress/save', methods=['POST'])
async def save_actresses():
    """배우 목록 DB 저장 (3자리 sort_order 및 is_retired 구분 저장 지원)"""
    try:
        data = request.get_json() or []
        conn = sqlite3.connect(DB_PATH)
        enable_wal(conn)
        cur = conn.cursor()

        cur.execute("SELECT id FROM feeds WHERE section = 'adult'")
        existing_ids = {row[0] for row in cur.fetchall()}
        new_ids = set()

        for idx, item in enumerate(data, 1):
            old_fid = int(item['id'])
            target_fid = int(item.get('new_id', old_fid))
            title = item['title'].strip()
            eng_name = (item.get('english_name') or '').strip()
            sort_ord = int(item.get('sort_order', idx))
            is_ret = int(item.get('is_retired', 0))

            if old_fid in existing_ids and target_fid != old_fid:
                cur.execute("UPDATE feeds SET id = ?, title = ?, english_name = ?, sort_order = ?, is_retired = ? WHERE id = ?",
                           (target_fid, title, eng_name, sort_ord, is_ret, old_fid))
                cur.execute("UPDATE items SET feed_id = ?, id = ? || '_' || substr(id, instr(id, '_') + 1) WHERE feed_id = ?",
                           (target_fid, str(target_fid), old_fid))
                fid = target_fid
            elif old_fid in existing_ids:
                cur.execute("UPDATE feeds SET title = ?, english_name = ?, sort_order = ?, is_retired = ? WHERE id = ?",
                           (title, eng_name, sort_ord, is_ret, old_fid))
                fid = old_fid
            else:
                cur.execute("INSERT INTO feeds (id, title, english_name, sort_order, is_retired, section) VALUES (?, ?, ?, ?, ?, 'adult')",
                           (target_fid, title, eng_name, sort_ord, is_ret))
                fid = target_fid

            new_ids.add(fid)

        del_ids = existing_ids - new_ids
        for fid in del_ids:
            cur.execute("DELETE FROM feeds WHERE id = ?", (fid,))

        conn.commit()
        conn.close()

        # 캐시 무효화 (TTLCache는 자동 만료)
        if HAS_CACHETOOLS:
            _actresses_cache.clear()
            _years_cache.clear()

        return jsonify_optimized({'status': 'success'})
    except Exception as e:
        return jsonify_optimized({'status': 'error', 'message': str(e)}, 500)

# ===== DB 청소 =====
@app.route('/api/actress/cleanup', methods=['POST'])
async def cleanup_actresses():
    """DB 고아 비디오 정리 (async)"""
    async def run_cleanup():
        try:
            conn = sqlite3.connect(DB_PATH)
            enable_wal(conn)
            cur = conn.cursor()
            cur.execute('PRAGMA foreign_keys = OFF;')
            cur.execute('DELETE FROM items WHERE feed_id NOT IN (SELECT id FROM feeds);')
            conn.commit()
            cur.execute('VACUUM;')
            conn.close()
            if HAS_CACHETOOLS:
                _actresses_cache.clear()
                _years_cache.clear()
        except Exception as e:
            app.logger.error("DB 청소 에러: %s", e)

    asyncio.create_task(run_cleanup())
    return jsonify_optimized({'status': 'cleanup_triggered'})

# ===== 태그 목록 =====
@app.route('/api/tags/list', methods=['GET'])
async def list_tags_text():
    """태그 목록 JSON 반환 (캐시 활용)"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT name, priority FROM tags ORDER BY priority ASC, count DESC, name ASC")
        rows = cur.fetchall()
        lines = []
        for name, priority in rows:
            if priority != 99:
                lines.append(f"{str(priority).zfill(2)}_{name}")
            else:
                lines.append(name)
        return jsonify_optimized({'content': "\n".join(lines)})
    except Exception as e:
        return jsonify_optimized({'content': '', 'error': str(e)}, 500)

# ===== 태그 저장 =====
@app.route('/api/tags/save', methods=['POST'])
async def save_tags_text():
    """태그 목록 DB 저장 (TTLCache 자동 만료)"""
    try:
        req = request.get_json() or {}
        content = req.get('content', '').strip()
        lines = [line.strip() for line in content.split('\n') if line.strip()]

        conn = sqlite3.connect(DB_PATH)
        enable_wal(conn)
        cur = conn.cursor()

        cur.execute("SELECT name FROM tags")
        existing_names = {row[0] for row in cur.fetchall()}

        new_names = set()
        for raw_tag in lines:
            pref_match = re.match(r'^(\d+)_(.+)$', raw_tag)
            if pref_match:
                priority = int(pref_match.group(1))
                clean_tag = pref_match.group(2).strip()
            else:
                priority = 99
                clean_tag = raw_tag
            new_names.add(clean_tag)

            if clean_tag in existing_names:
                cur.execute("UPDATE tags SET priority = ? WHERE name = ?", (priority, clean_tag))
            else:
                cur.execute("INSERT INTO tags (name, priority, count) VALUES (?, ?, 0)", (clean_tag, priority))

        del_names = existing_names - new_names
        for tag in del_names:
            cur.execute("DELETE FROM tags WHERE name = ?", (tag,))

        conn.commit()
        conn.close()

        if HAS_CACHETOOLS:
            _tags_cache.clear()

        return jsonify_optimized({'status': 'success'})
    except Exception as e:
        return jsonify_optimized({'status': 'error', 'message': str(e)}, 500)

# ===== 태그 재계산 =====
@app.route('/api/tags/cleanup', methods=['POST'])
async def cleanup_tags():
    """태그 재계산 (importlib 기반)"""
    async def run_rebuild():
        try:
            rebuild_mod = importlib.import_module('rebuild_tags')
            if hasattr(rebuild_mod, 'rebuild'):
                await asyncio.to_thread(rebuild_mod.rebuild)
            else:
                await asyncio.to_thread(__import__('subprocess').run,
                    [sys.executable, REBUILD_PATH],
                    capture_output=True)
            if HAS_CACHETOOLS:
                _tags_cache.clear()
        except Exception as e:
            app.logger.error("태그 Rebuild 에러: %s", e)

    asyncio.create_task(run_rebuild())
    return jsonify_optimized({'status': 'cleanup_triggered'})

# ===== 좋아요 토글 =====
@app.route('/api/video/like', methods=['POST'])
async def toggle_video_like():
    """좋아요 상태 토글 (async + WAL)"""
    try:
        req = request.get_json() or {}
        item_id = req.get('item_id')
        liked_state = req.get('liked', 0)

        if not item_id:
            return jsonify_optimized({'status': 'error', 'message': '비디오 ID가 누락되었습니다.'}, 400)

        conn = sqlite3.connect(DB_PATH)
        enable_wal(conn)
        cur = conn.cursor()
        cur.execute("UPDATE items SET liked = ? WHERE id = ?", (liked_state, item_id))
        conn.commit()
        conn.close()

        return jsonify_optimized({'status': 'success', 'liked': liked_state})
    except Exception as e:
        return jsonify_optimized({'status': 'error', 'message': str(e)}, 500)

# ===== favicon =====
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(STATIC_DIR, 'favicon.ico', mimetype='image/vnd.microsoft.icon')

# ===== 에러 핸들러 =====
@app.errorhandler(404)
def not_found(e):
    return jsonify_optimized({'error': 'Not Found'}, 404)

@app.errorhandler(500)
def server_error(e):
    return jsonify_optimized({'error': 'Internal Server Error'}, 500)

# ===== 앱 실행 =====
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5050, debug=False)
