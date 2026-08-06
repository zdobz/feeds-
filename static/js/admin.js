/**
 * admin.js - D:\feeds 관리자 CRUD 비즈니스 로직 모듈
 *
 * 담당 기능:
 *   [배우] 배우 모달 열기/닫기, 목록 조회(/api/actress/list),
 *            저장(/api/actress/save), 청소(/api/actress/cleanup),
 *            행 이동/수정/삭제/신규추가 (tempActressList 배열 조작)
 *   [태그] 태그 모달 열기/닫기, 목록 조회(/api/tags/list),
 *            저장(/api/tags/save), 청소(/api/tags/cleanup = rebuild_tags.py)
 *   [공통] ESC 키로 모달 닫기 (태그/배우 모달 공통 적용)
 */

/** HTML 특수문자 이스케이프 — input value 속성 주입 시 구문 깨짐(XSS) 방지 */
function escHtml(str) {
    if (!str) return '';
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ============================================================
// [배우 관리] Actress CRUD
// ============================================================

/** 배우 수집기(actress_collection_bulk.py)를 백그라운드로 실행 (POST /api/actress/sync) */
function triggerActressSync() {
    fetch('/api/actress/sync', { method: 'POST' })
        .then(() => console.log('[sync] 배우 수집기 실행 요청 완료 — 백그라운드 동작 중'))
        .catch(err => console.error('[sync] 실행 요청 실패:', err));
}

/** tempActressList: 모달 내에서 임시 조작 중인 배우 리스트 ([저장] 시에만 DB에 반영됨) */
let tempActressList = [];

/** 배우 모달을 열고 최신 목록을 API로부터 불러옵니다. */
function openActressModal() {
    document.getElementById('actress-modal').style.display = 'flex';
    fetchActressList();
}

/** 배우 모달을 닫고 임시 리스트를 리셋합니다. */
function closeActressModal() {
    document.getElementById('actress-modal').style.display = 'none';
    tempActressList = [];
}

function format3Digit(val) {
    const num = parseInt(val) || 0;
    return String(num).padStart(3, '0');
}

/** /api/actress/list 호출 후 tempActressList에 저장하고 2열 테이블을 렌더링합니다. */
function fetchActressList() {
    fetch('/api/actress/list')
        .then(res => res.json())
        .then(data => {
            tempActressList = data;
            renderActressTable();
        })
        .catch(err => {
            console.error(err);
            alert("배우 목록을 조회하는 도중 오류가 발생했습니다.");
        });
}

/**
 * tempActressList의 현재 상태를 좌/우 2열 DOM으로 렌더링합니다.
 * 순서 번호는 000 3자리 포맷으로 관리됩니다.
 */
function renderActressTable() {
    const activeContainer = document.getElementById('actress-active-list');
    const retiredContainer = document.getElementById('actress-retired-list');
    if (!activeContainer || !retiredContainer) return;

    activeContainer.innerHTML = '';
    retiredContainer.innerHTML = '';

    tempActressList.forEach((item, index) => {
        if (!item.sort_order && item.sort_order !== 0) {
            item.sort_order = index + 1;
        }
        const sortStr = format3Digit(item.sort_order);
        
        let isRetired = false;
        if (item.is_retired !== undefined && item.is_retired !== null) {
            isRetired = (item.is_retired === 1);
        } else if (item.max_pub) {
            isRetired = (item.max_pub < '2025-06-01');
        } else {
            isRetired = true;
        }
        item.is_retired = isRetired ? 1 : 0;

        const row = document.createElement('div');
        row.className = 'actress-item-row';
        
        const switchBtn = !isRetired
            ? `<button type="button" class="btn-base btn-switch-group" onclick="toggleActressStatus(${item.id}, 1)" title="은퇴/휴면 배우 그룹으로 이동">구</button>`
            : `<button type="button" class="btn-base btn-switch-group btn-switch-active" onclick="toggleActressStatus(${item.id}, 0)" title="신규/활발 배우 그룹으로 이동">신</button>`;

        row.innerHTML = `
            ${switchBtn}
            <input type="text" class="admin-input-order" value="${sortStr}" onchange="updateActressOrder(${item.id}, this)">
            <input type="text" class="admin-input-title" value="${escHtml(item.title)}" onchange="updateActressField(${item.id}, 'title', this.value)">
            <input type="text" class="admin-input-eng" value="${escHtml(item.english_name || '')}" onchange="updateActressField(${item.id}, 'english_name', this.value)" placeholder="영문명">
            <button type="button" class="btn-base btn-row-del" onclick="deleteActressRow(${item.id})">✕</button>
        `;

        if (!isRetired) {
            activeContainer.appendChild(row);
        } else {
            retiredContainer.appendChild(row);
        }
    });
}

function toggleActressStatus(id, newRetiredState) {
    const idx = tempActressList.findIndex(item => item.id === id);
    if (idx !== -1) {
        tempActressList[idx].is_retired = newRetiredState;
        renderActressTable();
    }
}

function updateActressOrder(id, inputEl) {
    const val = parseInt(inputEl.value) || 0;
    inputEl.value = format3Digit(val);
    const idx = tempActressList.findIndex(item => item.id === id);
    if (idx !== -1) {
        tempActressList[idx].sort_order = val;
    }
}

/** tempActressList의 지정된 id 항목의 필드를 실시간 수정합니다. */
function updateActressField(id, field, value) {
    const idx = tempActressList.findIndex(item => item.id === id);
    if (idx !== -1) {
        tempActressList[idx][field] = value.trim();
    }
}

/** 신규 추가 폼의 입력값을 검증한 후 tempActressList에 새 항목을 추가하고 테이블을 다시 불러옵니다.
 * @validates {ID}: 숫자 형식 + 중복 제외, {title}: 비어있으면 거부
 */
function addActressRow() {
    const idInput = document.getElementById('new-actress-id');
    const titleInput = document.getElementById('new-actress-title');
    const engInput = document.getElementById('new-actress-eng');
    
    const fid = parseInt(idInput.value);
    const title = titleInput.value.trim();
    const eng = engInput.value.trim();
    
    if (isNaN(fid)) {
        alert("유효한 배우 ID 숫자를 입력하십시오.");
        return;
    }
    if (!title) {
        alert("한글 이름을 입력하십시오.");
        return;
    }
    if (tempActressList.some(item => item.id === fid)) {
        alert("이미 리스트에 존재하는 배우 ID입니다.");
        return;
    }
    
    tempActressList.push({ id: fid, title: title, english_name: eng });
    idInput.value = '';
    titleInput.value = '';
    engInput.value = '';
    
    renderActressTable();
}

/** 해당 id 항목을 tempActressList에서 제거하고 테이블을 재렌더링합니다. */
function deleteActressRow(id) {
    tempActressList = tempActressList.filter(item => item.id !== id);
    renderActressTable();
}

/** 현재 tempActressList를 POST /api/actress/save로 서버에 저장합니다. (DB sort_order 갱신) */
function saveActressChanges() {
    tempActressList.sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
    fetch('/api/actress/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(tempActressList)
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            closeActressModal();
            location.reload();
        } else {
            alert("배우 데이터 저장 오류: " + data.message);
        }
    })
    .catch(err => {
        console.error(err);
        alert("서버 저장 처리 중 통신 오류가 발생했습니다.");
    });
}

/** 확인 후 POST /api/actress/cleanup 요청으로 DB feeds 테이블 기준으로 고아 비디오를 정리합니다.
 * 성공 시 1500ms 후 자동 reload (cleanup은 백그라운드 동작이므로 충분한 대기 필요)
 */
function cleanUpActresses() {
    if (confirm("정말로 데이터베이스 청소(DB feeds 테이블에 없는 배우와 연결 꼬인 영상 일괄 삭제)를 수행하시겠습니까?\n이 작업은 백그라운드에서 진행됩니다.")) {
        fetch('/api/actress/cleanup', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'cleanup_triggered') {
                closeActressModal();
                setTimeout(() => location.reload(), 1500);
            } else {
                alert("DB 청소 요청이 거부되었습니다.");
            }
        })
        .catch(err => {
            console.error(err);
            alert("DB 청소 서버 통신 오류가 발생했습니다.");
        });
    }
}


// ============================================================
// [태그 관리] Tags CRUD
// ============================================================

/** 태그 모달을 열고 DB tags 테이블 목록을 API로부터 불러옵니다. */
function openTagsModal() {
    document.getElementById('tags-modal').style.display = 'flex';
    fetchTagsList();
}

/** 태그 모달을 닫고 textarea를 리셋합니다. */
function closeTagsModal() {
    document.getElementById('tags-modal').style.display = 'none';
    const tagArea = document.getElementById('tags-textarea');
    if (tagArea) tagArea.value = '';
}

/** /api/tags/list 호출 후 textarea에 태그 목록을 채웁니다. */
function fetchTagsList() {
    fetch('/api/tags/list')
        .then(res => res.json())
        .then(data => {
            const tagArea = document.getElementById('tags-textarea');
            if (tagArea) tagArea.value = data.content;
        })
        .catch(err => {
            console.error(err);
            alert("태그 목록을 조회하는 중 오류가 발생했습니다.");
        });
}

/** textarea 내용을 POST /api/tags/save로 저장합니다. 성공 시 reload. */
function saveTagsChanges() {
    const textValue = document.getElementById('tags-textarea').value;
    fetch('/api/tags/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: textValue })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            closeTagsModal();
            location.reload();
        } else {
            alert("태그 저장 중 오류: " + data.message);
        }
    })
    .catch(err => {
        console.error(err);
        alert("서버 통신 중 에러가 발생했습니다.");
    });
}

/** 확인 후 POST /api/tags/cleanup 요청으로 rebuild_tags.py를 백그라운드 실행합니다.
 * 성공 시 1500ms 후 자동 reload (rebuild는 백그라운드 동작이므로 충분한 대기 필요)
 */
function cleanUpTags() {
    if (confirm("정말로 태그 검증 및 개수 재분석(rebuild_tags.py 실행)을 수행하시겠습니까?\n이 작업은 백그라운드에서 진행됩니다.")) {
        fetch('/api/tags/cleanup', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'cleanup_triggered') {
                closeTagsModal();
                setTimeout(() => location.reload(), 1500);
            } else {
                alert("태그 정리 요청이 거부되었습니다.");
            }
        })
        .catch(err => {
            console.error(err);
            alert("태그 정리 요청 처리 중 오류가 발생했습니다.");
        });
    }
}


// ============================================================
// [공통] 키보드 이벤트
// ============================================================

/** ESC 키 입력 시 현재 열린 모달(태그 또는 배우)을 자동으로 닫습니다. */
document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape' || event.keyCode === 27) {
        const actressModal = document.getElementById('actress-modal');
        if (actressModal && window.getComputedStyle(actressModal).display === 'flex') {
            closeActressModal();
        }
        const tagsModal = document.getElementById('tags-modal');
        if (tagsModal && window.getComputedStyle(tagsModal).display === 'flex') {
            closeTagsModal();
        }
    }
});
