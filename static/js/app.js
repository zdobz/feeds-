/**
 * app.js - D:\feeds 메인 UI 로직 모듈
 *
 * 담당 기능:
 *   [1] 카드 치수/페이지 고도 자동 계산 (ResizeObserver 동적 업데이트)
 *   [2] 새 영상 알림 배지 (localStorage 기반 신규 rowid/개수 비교)
 *   [3] 좋아요 토글 (UI 즉각 반영 + /api/video/like POST 비동기)
 *   [4] 스크롤/페이지 이동 (마우스 휠, 키보드 단축키)
 *   [5] 필터 함수 (태그/나이대/키구간/좋아요/필터제거)
 *   [6] 드롭다운 셀렉트박스 (배우/연도)
 *   [7] 정렬 체크박스 (출시일순 vs DB 수집순)
 */
(function() {
    const mainContent = document.querySelector('.main-content');
    const pageNums = document.querySelectorAll('.page-num');
    const displayPageLabel = document.getElementById('current-display-page');

    let cardHeight = 244;
    let rowGap = 15;
    let rowHeight = 259;
    let pageHeight = 777;

    function updateDimensions() {
        const gridEl = document.querySelector('.grid');
        const cardEl = document.querySelector('.card');
        if (cardEl && gridEl) {
            cardHeight = cardEl.offsetHeight || 244;
            rowGap = parseInt(window.getComputedStyle(gridEl).gap) || 15;
            rowHeight = cardHeight + rowGap;
            pageHeight = rowHeight * 3;
        } else {
            rowHeight = 259;
            pageHeight = 777;
        }
    }

    updateDimensions();

    if (window.ResizeObserver) {
        const resizeObserver = new ResizeObserver(() => {
            updateDimensions();
        });
        const gridElement = document.querySelector('.grid');
        if (gridElement) {
            resizeObserver.observe(gridElement);
        }
    }
    window.addEventListener('resize', updateDimensions);

    // --- [1] 카드 치수/페이지 고도 계산 ---
    // window.appConfig: Jinja2 템플릿에서 <script> 블록으로 주입된 서버 변수
    const config = window.appConfig || {};
    const startPage = config.startPage || window.startPage || 1;
    const endPage = config.endPage || window.endPage || 1;
    let currentPage = config.currentPage || window.currentPage || 1;
    const maxPage = config.maxPage || window.maxPage || 1;

    const currentMaxRowid = config.currentMaxRowid || window.currentMaxRowid || 0;
    const currentTotalCount = config.currentTotalCount || window.currentTotalCount || 0;
    const activeNewSince = config.activeNewSince || window.activeNewSince || 0;

    let lastMaxRowid = localStorage.getItem('last_max_rowid');
    let lastTotalCount = localStorage.getItem('last_total_count');

    // 초기 로드 시 데이터가 없을 때만 캐시 적재 (자동 읽음 처리 로직 제거, X 버튼 클릭 시에만 읽음 처리)
    if (!lastMaxRowid || !lastTotalCount) {
        localStorage.setItem('last_max_rowid', currentMaxRowid);
        localStorage.setItem('last_total_count', currentTotalCount);
        lastMaxRowid = currentMaxRowid;
        lastTotalCount = currentTotalCount;
    }

    // --- [2] 새 영상 알림 배지 ---
    // diffCount > 0이고 new_since 필터가 비활성일 때만 배지 노출
    const diffCount = currentTotalCount - parseInt(lastTotalCount);
    if (diffCount > 0 && activeNewSince === 0) {
        const pageInfoEl = document.querySelector('.page-info');
        if (pageInfoEl) {
            const badgeHtml = ` <span id="new-diff-badge" class="badge-new-video"><span class="btn-view" onclick="event.stopPropagation(); viewNewVideos(${lastMaxRowid})" title="새로 추가된 영상 보기">+${diffCount} New</span><span class="btn-close" onclick="event.stopPropagation(); closeNewBadgeDirectly()" title="알림 끄기">✕</span></span>`;
            pageInfoEl.innerHTML += badgeHtml;
        }
    }

    window.viewNewVideos = function(sinceRowid) {
        location.href = "/?new_since=" + sinceRowid;
    };

    window.closeNewBadgeDirectly = function() {
        localStorage.setItem('last_max_rowid', currentMaxRowid);
        localStorage.setItem('last_total_count', currentTotalCount);
        const badge = document.getElementById('new-diff-badge');
        if (badge) badge.remove();
    };

    // --- [3] 좋아요 토글 ---
    // UI 즉각 전환(클래스 스왑) 후 백엔드 비동기 저장. 네트워크 실패 시 console.error만 기록.
    const toggleLikeState = function(itemId, element) {
        const isCurrentlyLiked = element.classList.contains('liked');
        const newLikedState = isCurrentlyLiked ? 0 : 1;

        // UI 즉각 시각 피드백 제공 (리액티브 전환)
        if (newLikedState === 1) {
            element.classList.remove('not-liked');
            element.classList.add('liked');
        } else {
            element.classList.remove('liked');
            element.classList.add('not-liked');
        }

        // 상단 좋아요 카운트 숫자 비동기 동적 증감
        const countEl = document.getElementById('liked-count-display');
        if (countEl) {
            let currentCount = parseInt(countEl.innerText) || 0;
            currentCount = newLikedState === 1 ? currentCount + 1 : Math.max(0, currentCount - 1);
            countEl.innerText = currentCount;
        }

        // 백엔드로 좋아요 상태 비동기 저장 통신
        fetch('/api/video/like', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: itemId, liked: newLikedState })
        })
        .then(res => res.json())
        .then(data => {
            if (data.status !== 'success') {
                console.error("좋아요 동기화 실패:", data.message);
            }
        })
        .catch(err => console.error("좋아요 통신 에러:", err));
    };

    // [기능 추가] 모든 좋아요 버튼에 클릭 이벤트 동적 바인딩 (스코프 및 로딩 순서 간섭 원천 방지)
    document.querySelectorAll('.like-btn').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation(); // 혹시 모를 a 태그 전파 완전히 차단

            const cardEl = this.closest('.card');
            if (cardEl) {
                const itemId = cardEl.getAttribute('data-id');
                toggleLikeState(itemId, this);
            }
        });
    });

    // [기능 추가] 상단 좋아요 칩 클릭 시 좋아요 모아보기 필터 토글
    window.toggleLikeFilter = function() {
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('liked') === '1') {
            urlParams.delete('liked');
        } else {
            urlParams.set('liked', '1');
        }
        urlParams.delete('page');
        window.location.href = "/?" + urlParams.toString();
    };

    window.clearNewFilter = function() {
        localStorage.setItem('last_max_rowid', currentMaxRowid);
        localStorage.setItem('last_total_count', currentTotalCount);
        location.href = "/";
    };

    /**
     * getLiveParamStr - 현재 URL의 모든 필터 파라미터를 문자열로 반환
     * 페이지 이동 시(scrollToPage, scrollPageOffset 등) 필터 상태 유지를 위해 사용
     * [버그 수정] tag / liked / age_range / height_range 파라미터 누락 수정
     *   이전 버전에서는 이 4개가 빠져 필터 적용 중 사이드바 페이지 번호 클릭 시 필터가 초기화됐음
     */
    function getLiveParamStr() {
        const urlParams    = new URLSearchParams(window.location.search);
        const feedId       = urlParams.get('feed_id')       || '';
        const year         = urlParams.get('year')           || '';
        const q            = urlParams.get('q')              || '';
        const newSince     = urlParams.get('new_since')      || '';
        const sort         = urlParams.get('sort')           || '';
        const tag          = urlParams.get('tag')            || '';  // [수정] 태그 필터 유지
        const liked        = urlParams.get('liked')          || '';  // [수정] 좋아요 필터 유지
        const ageRange     = urlParams.get('age_range')      || '';  // [수정] 나이대 필터 유지
        const heightRange  = urlParams.get('height_range')   || '';  // [수정] 키 구간 필터 유지

        let param = "&feed_id=" + encodeURIComponent(feedId)
                  + "&q="       + encodeURIComponent(q)
                  + "&year="    + encodeURIComponent(year);
        if (newSince)    param += "&new_since="    + encodeURIComponent(newSince);
        if (sort)        param += "&sort="         + encodeURIComponent(sort);
        if (tag)         param += "&tag="          + encodeURIComponent(tag);
        if (liked)       param += "&liked="        + encodeURIComponent(liked);
        if (ageRange)    param += "&age_range="    + encodeURIComponent(ageRange);
        if (heightRange) param += "&height_range=" + encodeURIComponent(heightRange);
        return param;
    }

    // --- [4] 스크롤/페이지 이동 ---
    let isAutoScrolling = false;  // 자동 스크롤 중 플래그 (휠/스크롤 이벤트 충돌 방지)
    let isWheeling = false;       // 마우스 휠 처리 중 플래그 (중복 발화 방지)

    function initScrollPosition() {
        isAutoScrolling = true;
        const targetScrollTop = (currentPage - startPage) * pageHeight;
        if(mainContent) {
            mainContent.scrollTop = targetScrollTop;
        }
        setTimeout(() => { isAutoScrolling = false; }, 100);
    }

    if (mainContent) {
        setTimeout(initScrollPosition, 50);
    }

    window.scrollToPage = function(p) {
        isAutoScrolling = true;
        const targetScrollTop = (p - startPage) * pageHeight;
        if(mainContent) {
            mainContent.scrollTo({
                top: targetScrollTop,
                behavior: 'smooth'
            });
        }
        currentPage = p;
        if (displayPageLabel) displayPageLabel.innerText = p;

        pageNums.forEach(btn => {
            if (parseInt(btn.getAttribute('data-page')) === p) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });

        history.replaceState(null, null, "/?page=" + p + getLiveParamStr());
        setTimeout(() => { isAutoScrolling = false; }, 400);
    };

    // 네비게이션 F 버튼 클릭 시 모든 필터를 클리어하고 처음 홈("/")으로 즉시 이동
    window.scrollToFirst = function() {
        location.href = "/";
    };
    window.scrollToEnd = function() {
        if (currentPage < maxPage) {
            location.href = "/?page=" + maxPage + getLiveParamStr();
        } else {
            window.scrollToPage(maxPage);
        }
    };

    window.scrollPageOffset = function(offset) {
        const targetPage = currentPage + offset;
        if (targetPage >= startPage && targetPage <= endPage) {
            window.scrollToPage(targetPage);
        } else {
            if (targetPage >= 1 && targetPage <= maxPage) {
                location.href = "/?page=" + targetPage + getLiveParamStr();
            }
        }
    };

    if (mainContent) {
        mainContent.addEventListener('wheel', function(e) {
            e.preventDefault();
            if (isAutoScrolling || isWheeling) return;
            isWheeling = true;

            const direction = e.deltaY > 0 ? 1 : -1;
            const currentScroll = mainContent.scrollTop;
            let targetScroll = currentScroll + direction * rowHeight;
            targetScroll = Math.round(targetScroll / rowHeight) * rowHeight;

            const maxScroll = mainContent.scrollHeight - mainContent.clientHeight;
            if (targetScroll < 0) targetScroll = 0;
            if (targetScroll > maxScroll) targetScroll = maxScroll;

            mainContent.scrollTo({
                top: targetScroll,
                behavior: 'smooth'
            });

            setTimeout(() => { isWheeling = false; }, 200);
        }, { passive: false });
    }

    let scrollTimeout;
    if (mainContent) {
        mainContent.addEventListener('scroll', function() {
            if (isAutoScrolling) return;

            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(function() {
                const scrollTop = mainContent.scrollTop;
                const activePageNum = Math.round(scrollTop / pageHeight) + startPage;

                if (activePageNum >= startPage && activePageNum <= endPage) {
                    pageNums.forEach(btn => {
                        if (parseInt(btn.getAttribute('data-page')) === activePageNum) {
                            btn.classList.add('active');
                            if (currentPage !== activePageNum) {
                                currentPage = activePageNum;
                                if (displayPageLabel) displayPageLabel.innerText = activePageNum;
                                history.replaceState(null, null, "/?page=" + activePageNum + getLiveParamStr());
                            }
                        } else {
                            btn.classList.remove('active');
                        }
                    });
                }
            }, 100);
        });
    }

    // --- [5] 키보드 단축키 ---
    // Alt+좌: 홈(/), Alt+우: 마지막 페이지, 좌/우: 20페이지 이동, 상/하: 1페이지 스크롤
    window.addEventListener('keydown', function(e) {
        // 1. 입력 필드(텍스트박스, 셀렉트박스, 텍스트영역)에 포커스가 가 있을 때는 단축키 차단
        if (document.activeElement.tagName === 'INPUT' ||
            document.activeElement.tagName === 'SELECT' ||
            document.activeElement.tagName === 'TEXTAREA') {
            return;
        }

        // 2. 모달 편집창(배우 모달 등)이 열려 있을 때도 백그라운드 단축키 오작동 방지
        const actressModal = document.getElementById('actress-modal');
        if (actressModal && actressModal.style.display === 'flex') {
            return;
        }
        const tagsModal = document.getElementById('tags-modal');
        if (tagsModal && tagsModal.style.display === 'flex') {
            return;
        }

        // 3. 자동 스크롤 또는 마우스 휠 동작 중일 때 차단
        if (isAutoScrolling || isWheeling) return;

        // Alt + 왼쪽 방향키: 모든 필터를 클리어하고 처음 홈("/")으로 즉시 이동 (Home 키 역할)
        if (e.altKey && e.key === 'ArrowLeft') {
            e.preventDefault();
            location.href = "/";
            return;
        }

        // Alt + 오른쪽 방향키: 마지막 페이지로 즉시 이동
        if (e.altKey && e.key === 'ArrowRight') {
            e.preventDefault();
            if (currentPage < maxPage) {
                location.href = "/?page=" + maxPage + getLiveParamStr();
            }
            return;
        }

        // 그냥 오른쪽 방향키: 20페이지 앞으로 이동
        if (e.key === 'ArrowRight') {
            e.preventDefault();
            const targetPage = Math.min(maxPage, currentPage + 20);
            if (targetPage !== currentPage) {
                location.href = "/?page=" + targetPage + getLiveParamStr();
            }
        }
        // 그냥 왼쪽 방향키: 20페이지 뒤로 이동
        else if (e.key === 'ArrowLeft') {
            e.preventDefault();
            const targetPage = Math.max(1, currentPage - 20);
            if (targetPage !== currentPage) {
                location.href = "/?page=" + targetPage + getLiveParamStr();
            }
        }
        else if (e.key === 'ArrowDown') {
            e.preventDefault();
            const targetPage = currentPage + 1;
            if (targetPage <= endPage) {
                window.scrollToPage(targetPage);
            } else if (targetPage <= maxPage) {
                location.href = "/?page=" + targetPage + getLiveParamStr();
            }
        }
        else if (e.key === 'ArrowUp') {
            e.preventDefault();
            const targetPage = currentPage - 1;
            if (targetPage >= startPage) {
                window.scrollToPage(targetPage);
            } else if (targetPage >= 1) {
                location.href = "/?page=" + targetPage + getLiveParamStr();
            }
        }
    });

    // --- [5-1] 필터 함수 ---
    // 태그 칩 클릭: 일반 검색어 q와 혼선 방지를 위해 전용 tag 파라미터 경로로 이동
    window.searchTag = function(tag) {
        const urlParams = new URLSearchParams(window.location.search);
        urlParams.set('tag', tag);
        urlParams.delete('q');     // 일반 검색어 초기화
        urlParams.delete('page');  // 페이지 초기화
        window.location.href = "/?" + urlParams.toString();
    };

    // [기능 추가] 프로필(나이대/키) 서랍 토글 전용 함수
    window.toggleProfileDrawer = function() {
        const drawer = document.getElementById('profile-drawer');
        if (!drawer) return;
        drawer.classList.toggle('open');
    };

    // [기능 추가] 나이대 필터링 검색 이동
    window.searchAgeRange = function(ageRange) {
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('age_range') === ageRange) {
            urlParams.delete('age_range');
        } else {
            urlParams.set('age_range', ageRange);
        }
        urlParams.delete('page');
        window.location.href = "/?" + urlParams.toString();
    };

    // [기능 추가] 키 구간 필터링 검색 이동
    window.searchHeightRange = function(heightRange) {
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('height_range') === heightRange) {
            urlParams.delete('height_range');
        } else {
            urlParams.set('height_range', heightRange);
        }
        urlParams.delete('page');
        window.location.href = "/?" + urlParams.toString();
    };

    window.toggleTagDrawer = function() {
        const drawer = document.getElementById('tag-drawer');
        const btn = document.querySelector('.tag-more-btn');
        if (drawer.classList.contains('open')) {
            drawer.classList.remove('open');
            btn.innerText = '+';
        } else {
            drawer.classList.add('open');
            btn.innerText = '-';
        }
    };

    // --- [6] 드롭다운 셀렉트박스 (배우/연도) ---
    const selectBox = document.getElementById('custom-select-box');
    const triggerBtn = document.getElementById('select-trigger-btn');
    const optionsList = document.getElementById('select-options-list');
    const feedIdInput = document.getElementById('feed-id-input');
    const filterForm = document.getElementById('filter-form');

    const yearSelectBox = document.getElementById('year-select-box');
    const yearTriggerBtn = document.getElementById('year-trigger-btn');
    const yearOptionsList = document.getElementById('year-options-list');
    const yearInput = document.getElementById('year-input');

    const urlParams = new URLSearchParams(window.location.search);
    if (feedIdInput) feedIdInput.value = urlParams.get('feed_id') || '';
    if (yearInput) yearInput.value = urlParams.get('year') || '';
    const searchInput = document.getElementById('search-input');
    if (searchInput) searchInput.value = urlParams.get('q') || '';

    if (triggerBtn && optionsList) {
        triggerBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            if (yearOptionsList) yearOptionsList.classList.remove('open');
            optionsList.classList.toggle('open');
        });

        document.querySelectorAll('.option-item').forEach(item => {
            item.addEventListener('click', function() {
                const val = this.getAttribute('data-value');
                feedIdInput.value = val;
                optionsList.classList.remove('open');
                if (filterForm) filterForm.submit();
            });
        });

        document.addEventListener('click', function(e) {
            if (!selectBox.contains(e.target)) {
                optionsList.classList.remove('open');
            }
            if (yearSelectBox && !yearSelectBox.contains(e.target)) {
                yearOptionsList.classList.remove('open');
            }
        });
    }

    if (yearTriggerBtn && yearOptionsList) {
        yearTriggerBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            optionsList.classList.remove('open');
            yearOptionsList.classList.toggle('open');
        });

        document.querySelectorAll('.year-option-item').forEach(item => {
            item.addEventListener('click', function() {
                const val = this.getAttribute('data-value');
                yearInput.value = val;
                yearOptionsList.classList.remove('open');
                if (filterForm) filterForm.submit();
            });
        });
    }

    window.removeFilter = function(type) {
        if (type === 'feed_id') {
            const feedInput = document.getElementById('feed-id-input');
            if (feedInput) feedInput.value = '';
        } else if (type === 'year') {
            const yearInput = document.getElementById('year-input');
            if (yearInput) yearInput.value = '';
        } else if (type === 'q') {
            const searchInput = document.getElementById('search-input');
            if (searchInput) searchInput.value = '';
        } else if (type === 'tag') {
            // [리팩토링] 태그 필터 칩의 X 버튼 클릭 시 URL 파라미터에서 tag 제거 후 재이동
            const urlParams = new URLSearchParams(window.location.search);
            urlParams.delete('tag');
            urlParams.delete('page');
            window.location.href = "/?" + urlParams.toString();
            return;
        } else if (type === 'liked') {
            // [기능 추가] Liked 필터 칩의 X 버튼 클릭 시 liked 파라미터 삭제 후 재이동
            const urlParams = new URLSearchParams(window.location.search);
            urlParams.delete('liked');
            urlParams.delete('page');
            window.location.href = "/?" + urlParams.toString();
            return;
        } else if (type === 'age_range') {
            // [기능 추가] 나이대 필터 칩의 X 버튼 클릭 시 파라미터 삭제 후 재이동
            const urlParams = new URLSearchParams(window.location.search);
            urlParams.delete('age_range');
            urlParams.delete('page');
            window.location.href = "/?" + urlParams.toString();
            return;
        } else if (type === 'height_range') {
            // [기능 추가] 키 구간 필터 칩의 X 버튼 클릭 시 파라미터 삭제 후 재이동
            const urlParams = new URLSearchParams(window.location.search);
            urlParams.delete('height_range');
            urlParams.delete('page');
            window.location.href = "/?" + urlParams.toString();
            return;
        }
        if (filterForm) filterForm.submit();
    };

    // 정렬 체크박스 토글 제어 (출시순 vs DB 수집일순)
    // --- [7] 정렬 체크박스 (출시일순 vs DB 수집순) ---
    const sortCheckbox = document.getElementById('sort-toggle-checkbox');
    const sortInput = document.getElementById('sort-input');
    if (sortCheckbox && sortInput) {
        // 주소창(URL) 상태에 맞게 체크박스 상태 강제 동기화
        const urlParams = new URLSearchParams(window.location.search);
        sortCheckbox.checked = urlParams.get('sort') === 'created';

        sortCheckbox.addEventListener('change', function() {
            sortInput.value = this.checked ? 'created' : 'published';
            const params = new URLSearchParams(window.location.search);
            params.set('sort', sortInput.value);
            params.set('page', '1'); // 정렬이 바뀌면 무조건 1페이지로 복귀
            location.href = "/?" + params.toString();
        });
    }
})();
