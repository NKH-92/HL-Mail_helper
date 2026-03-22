(function () {
    if (window.__mailaiUiPatchApplied) {
        return;
    }
    window.__mailaiUiPatchApplied = true;

    const state = {
        currentPage: "",
        currentMailTemplates: [],
        currentSendRegistrations: [],
        addressBookContacts: [],
        flashTimer: null,
        autosendSaveLocked: false,
        dashboardMailTab: "category_1",
        dashboardMailView: "list",
        classifiedMails: [],
        dashboardMailCategoryCounts: { category_1: 0, category_2: 0, category_3: 0 },
        selectedMailId: 0,
        dashboardSection: null,
        syncStatus: {},
        syncProgress: {},
        syncPollTimer: null,
        syncPollInFlight: false,
        sidebarMode: "auto",
        sidebarCollapsed: false,
        themeMode: "light",
        bootstrapRequested: false,
        clientStateVersion: 0,
    };

    const SIDEBAR_AUTO_COLLAPSE_BREAKPOINT = 1200;
    const SYNC_POLL_INTERVAL_MS = 800;
    const THEME_STORAGE_KEY = "mailai-theme-mode";

    const HEADER_TITLES = {
        dashboard: "메일 분류",
        archive: "보관함",
        completed: "완료",
        autosend: "자동발송",
        logs: "로그",
        settings: "설정",
        help: "도움말",
    };

    const PAGE_ID_BY_LABEL = {
        "메일 정리": "dashboard",
        "메일 분류": "dashboard",
        "대시보드": "dashboard",
        "보관함": "archive",
        "완료": "completed",
        "메일 자동발송": "autosend",
        "템플릿 자동발송": "autosend",
        "로그": "logs",
        "설정": "settings",
        "도움말": "help",
    };

    const PAGE_LABEL_BY_ID = {
        dashboard: "메일 정리",
        archive: "보관함",
        completed: "완료",
        autosend: "메일 자동발송",
        logs: "로그",
        settings: "설정",
        help: "도움말",
    };
    const DASHBOARD_PAGE_IDS = ["dashboard", "archive", "completed"];
    const DEFAULT_DASHBOARD_SECTIONS = {
        dashboard: {
            bucket_key: "classified",
            page_title: "메일 분류",
            hero_title: "메일 분류",
            hero_subtitle: "수신 메일을 내가 할 일, 검토 필요, 참고용으로 자동 분류하고 클릭 시 핵심 요약 화면으로 전환해 확인할 수 있습니다.",
            list_title: "메일 분류 목록",
            empty_list_message: "현재 탭에 표시할 메일이 없습니다.",
            detail_kicker: "Mail Summary",
            detail_title: "선택한 메일의 핵심 내용만 빠르게 확인합니다.",
            allow_archive_action: true,
            allow_complete_action: true,
            allow_restore_action: false,
        },
        archive: {
            bucket_key: "archived",
            page_title: "보관함",
            hero_title: "보관함",
            hero_subtitle: "보관한 메일을 분류별로 오래 유지하고, 동기화 기간과 무관하게 다시 확인할 수 있습니다.",
            list_title: "보관 메일 목록",
            empty_list_message: "현재 탭에 표시할 보관 메일이 없습니다.",
            detail_kicker: "Archive",
            detail_title: "보관된 메일의 핵심 내용을 다시 확인합니다.",
            allow_archive_action: false,
            allow_complete_action: true,
            allow_restore_action: true,
        },
        completed: {
            bucket_key: "completed",
            page_title: "완료",
            hero_title: "완료",
            hero_subtitle: "완료 처리한 메일을 동기화 보관 기간 안에서만 임시로 유지하고 다시 확인할 수 있습니다.",
            list_title: "완료 메일 목록",
            empty_list_message: "현재 탭에 표시할 완료 메일이 없습니다.",
            detail_kicker: "Completed",
            detail_title: "완료 처리한 메일의 핵심 내용을 다시 확인합니다.",
            allow_archive_action: false,
            allow_complete_action: false,
            allow_restore_action: true,
        },
    };

    const FOLLOW_UP_LABELS = {
        overdue: "기한 지남",
        deadline_soon: "마감 임박",
        approval_pending: "승인 대기",
        reply_needed: "답장 필요",
        no_reply_3d: "3일째 미회신",
        waiting_for_reply: "회신 대기",
        action_needed: "조치 필요",
        review_needed: "검토 필요",
        tracked: "추적 중",
    };

    const PRIORITY_REASON_LABELS = {
        "Overdue deadline": "기한 지남",
        "Due today": "오늘 마감",
        "Deadline soon": "마감 임박",
        "Reply needed": "답장 필요",
        "Approval pending": "승인 대기",
        "No reply for 3 days": "3일째 미회신",
        "High priority": "중요도 높음",
        "Open action item": "열린 액션",
        "Recent thread": "최근 대화",
    };

    const STOCK_THREAD_COPY = {
        Filtered: "필터 적용 중",
        Tracked: "추적 중",
        Review: "검토",
        Score: "점수",
        "Overall summary": "종합 요약",
        "Changed since last": "최근 변경점",
        "Current conclusion": "현재 결론",
        "Why this thread is prioritized": "우선순위 사유",
        "My open actions": "내 액션",
        "Thread actions": "스레드 액션",
        Timeline: "메일 타임라인",
        "No threads match this view.": "현재 필터에 맞는 스레드가 없습니다.",
        "No thread is visible in the current filter.": "현재 필터에서 표시할 스레드가 없습니다.",
        "Select a thread to inspect its next action.": "스레드를 선택하면 다음 액션과 대화 흐름을 확인할 수 있습니다.",
        "No direct action item is open.": "현재 열려 있는 개인 액션이 없습니다.",
        "No shared thread action is open.": "현재 열려 있는 스레드 액션이 없습니다.",
        "Timeline unavailable.": "메일 타임라인이 없습니다.",
        "First mail in this thread.": "이 스레드의 첫 메일입니다.",
        "Approval or decision is still pending.": "승인 또는 의사결정이 아직 남아 있습니다.",
        "Send a follow-up because there has been no reply for 3 days.": "3일째 회신이 없어 후속 메일 발송이 필요합니다.",
        "Monitor the thread until the other side replies.": "상대가 회신할 때까지 스레드를 추적합니다.",
        "AI confidence is low or the owner is unclear.": "AI 신뢰도가 낮거나 담당자가 불명확합니다.",
        "Untitled thread": "제목 없는 스레드",
    };

    function getDashboardPageId(pageId) {
        return DASHBOARD_PAGE_IDS.includes(String(pageId || "")) ? String(pageId) : "dashboard";
    }

    function resolveDashboardSection(section, pageId) {
        const normalizedPageId = getDashboardPageId(pageId);
        const fallback = DEFAULT_DASHBOARD_SECTIONS[normalizedPageId] || DEFAULT_DASHBOARD_SECTIONS.dashboard;
        if (!section || typeof section !== "object") {
            return { ...fallback };
        }
        return {
            ...fallback,
            ...section,
            allow_archive_action: Boolean(section.allow_archive_action ?? fallback.allow_archive_action),
            allow_complete_action: Boolean(section.allow_complete_action ?? fallback.allow_complete_action),
            allow_restore_action: Boolean(section.allow_restore_action ?? fallback.allow_restore_action),
            collection_tabs: Array.isArray(section.collection_tabs) ? section.collection_tabs : [],
        };
    }

    function isPywebviewRuntime() {
        return Boolean(window.pywebview && window.pywebview.api && typeof window.pywebview.api.dispatch === "function");
    }

    function appendClientActionId(message) {
        if (!message || typeof message !== "object" || !message.action || message.client_action_id) {
            return message;
        }

        return {
            ...message,
            client_action_id: `action-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
        };
    }

    function getClientStateSnapshot() {
        return {
            page: state.currentPage || "",
            dashboard_mail_tab: state.dashboardMailTab,
            dashboard_mail_view: state.dashboardMailView,
            selected_mail_id: state.selectedMailId,
        };
    }

    function nextClientStateVersion() {
        state.clientStateVersion = Number(state.clientStateVersion || 0) + 1;
        return state.clientStateVersion;
    }

    const UiBridge = {
        setComponentReady() {
            if (isPywebviewRuntime()) {
                return;
            }
            window.parent.postMessage(
                { isStreamlitMessage: true, type: "streamlit:componentReady", apiVersion: 1 },
                "*",
            );
        },
        async dispatch(message) {
            const safeMessage = appendClientActionId(message);
            const clientStateVersion = nextClientStateVersion();
            const payload = {
                ...(safeMessage || {}),
                client_state: getClientStateSnapshot(),
                client_state_version: clientStateVersion,
            };

            if (isPywebviewRuntime()) {
                try {
                    const nextState = await window.pywebview.api.dispatch(payload);
                    if (nextState) {
                        handleRender(nextState);
                    }
                    return nextState || null;
                } catch (error) {
                    const messageText = error && error.message ? error.message : "Unexpected error";
                    showFlash(messageText);
                    if (payload.action === "sync_mail") {
                        state.syncProgress = {};
                        renderSyncButton();
                        stopSyncPolling();
                    }
                    setAutosendSaveLocked(false);
                    const button = getElement("save-settings-btn");
                    if (button) {
                        button.innerText = "Save settings";
                        button.classList.remove("opacity-50");
                    }
                    return null;
                }
            }

            window.parent.postMessage(
                { isStreamlitMessage: true, type: "streamlit:setComponentValue", value: payload, dataType: "json" },
                "*",
            );
            return null;
        },
        async bootstrap() {
            if (
                state.bootstrapRequested ||
                !isPywebviewRuntime() ||
                typeof window.pywebview.api.bootstrap !== "function"
            ) {
                return;
            }
            state.bootstrapRequested = true;
            try {
                const initialState = await window.pywebview.api.bootstrap();
                if (initialState) {
                    handleRender(initialState);
                }
            } catch (error) {
                state.bootstrapRequested = false;
                showFlash(error && error.message ? error.message : "Failed to load the desktop UI.");
            }
        },
    };

    function escapeHtml(value) {
        return String(value || "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    function normalizeText(value) {
        return String(value || "").toLowerCase().replace(/\s+/g, "");
    }

    function pad2(value) {
        return String(value).padStart(2, "0");
    }

    function getLocalDateTimeValue() {
        const now = new Date();
        return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}T${pad2(now.getHours())}:${pad2(now.getMinutes())}`;
    }

    function getLocalTimeValue() {
        const now = new Date();
        return `${pad2(now.getHours())}:${pad2(now.getMinutes())}`;
    }

    function toDateTimeLocalInputValue(value) {
        const raw = String(value || "").trim();
        if (!raw) return getLocalDateTimeValue();
        const normalized = raw.replace("T", " ").replaceAll("/", "-").replaceAll(".", "-");
        const match = normalized.match(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})(?::\d{2})?$/);
        if (match) {
            return `${match[1]}T${match[2]}`;
        }
        if (raw.includes("T")) {
            return raw.slice(0, 16);
        }
        return raw.replace(" ", "T").slice(0, 16);
    }

    function fromDateTimeLocalInputValue(value) {
        const raw = String(value || "").trim();
        if (!raw) return "";
        return raw.replace("T", " ").slice(0, 16);
    }

    function toTimeInputValue(value) {
        const raw = String(value || "").trim();
        if (!raw) return getLocalTimeValue();
        const match = raw.match(/^(\d{2}:\d{2})(?::\d{2})?$/);
        return match ? match[1] : raw.slice(0, 5);
    }

    function getElement(id) {
        return document.getElementById(id);
    }

    function parseUiDate(value) {
        const raw = String(value || "").trim();
        if (!raw) return null;

        const normalized = raw.replaceAll("/", "-").replace("T", " ");
        let match = normalized.match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (match) {
            return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
        }

        match = normalized.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})(?::(\d{2}))?$/);
        if (match) {
            return new Date(
                Number(match[1]),
                Number(match[2]) - 1,
                Number(match[3]),
                Number(match[4]),
                Number(match[5]),
                Number(match[6] || 0),
            );
        }

        const parsed = new Date(raw);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    }

    function uiDateHasTime(value) {
        return /(?:T|\s)\d{2}:\d{2}/.test(String(value || "").trim());
    }

    function formatReadableDate(value, options) {
        const settings = options || {};
        const raw = String(value || "").trim();
        if (!raw) return "-";

        const parsed = parseUiDate(raw);
        if (!parsed) return raw;

        const includeTime = settings.includeTime ?? uiDateHasTime(raw);
        const weekdays = ["일", "월", "화", "수", "목", "금", "토"];
        const now = new Date();
        const currentDay = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const targetDay = new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
        const diffDays = Math.round((targetDay.getTime() - currentDay.getTime()) / 86400000);
        const timeText = `${pad2(parsed.getHours())}:${pad2(parsed.getMinutes())}`;

        if (diffDays === 0) return includeTime ? `오늘 ${timeText}` : "오늘";
        if (diffDays === 1) return includeTime ? `내일 ${timeText}` : "내일";
        if (diffDays === -1) return includeTime ? `어제 ${timeText}` : "어제";

        const weekday = weekdays[parsed.getDay()];
        const dateText = parsed.getFullYear() === now.getFullYear()
            ? `${parsed.getMonth() + 1}월 ${parsed.getDate()}일 (${weekday})`
            : `${parsed.getFullYear()}년 ${parsed.getMonth() + 1}월 ${parsed.getDate()}일 (${weekday})`;

        return includeTime ? `${dateText} ${timeText}` : dateText;
    }

    function formatDueCountdown(dueDateStr) {
        if (!dueDateStr) return "";
        const due = parseUiDate(dueDateStr);
        if (!due) return "";
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const target = new Date(due.getFullYear(), due.getMonth(), due.getDate());
        const diffDays = Math.round((target.getTime() - today.getTime()) / 86400000);

        if (diffDays < 0) return `D+${Math.abs(diffDays)}`;
        if (diffDays === 0) return "D-Day";
        return `D-${diffDays}`;
    }

    function formatDdayChipClass(dueDateStr) {
        if (!dueDateStr) return "";
        const due = parseUiDate(dueDateStr);
        if (!due) return "dashboard-dday-normal";
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const target = new Date(due.getFullYear(), due.getMonth(), due.getDate());
        const diffDays = Math.round((target.getTime() - today.getTime()) / 86400000);

        if (diffDays < 0) return "dashboard-dday-overdue";
        if (diffDays === 0) return "dashboard-dday-today";
        if (diffDays <= 3) return "dashboard-dday-soon";
        return "dashboard-dday-normal";
    }

    function formatDdayChip(dueDateStr) {
        if (!dueDateStr) return "";
        const label = formatDueCountdown(dueDateStr);
        if (!label) return "";
        const chipClass = formatDdayChipClass(dueDateStr);
        return `<span class="dashboard-dday-chip ${chipClass}">${escapeHtml(label)}</span>`;
    }

    function collapsibleSection(id, title, count, content, defaultOpen) {
        const openAttr = defaultOpen ? "open" : "";
        const countLabel = typeof count === "number" ? `${count}개` : escapeHtml(String(count || ""));
        return `
            <details class="dashboard-collapsible" ${openAttr} id="${id}">
                <summary class="dashboard-collapsible-header">
                    <h6 class="panel-title">${escapeHtml(title)}</h6>
                    <div class="dashboard-collapsible-right">
                        <span class="dashboard-section-count">${countLabel}</span>
                        <span class="dashboard-collapsible-chevron">▸</span>
                    </div>
                </summary>
                <div class="dashboard-collapsible-body">
                    ${content}
                </div>
            </details>
        `;
    }

    function normalizePathKey(value) {
        return String(value || "").trim().replaceAll("/", "\\").toLowerCase();
    }

    function isAbsoluteAttachmentPath(value) {
        return /^[A-Za-z]:[\\/]/.test(value) || /^\\\\/.test(value);
    }

    function parseAttachmentValues(rawValue) {
        return String(rawValue || "")
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean);
    }

    function getAttachmentValues() {
        return parseAttachmentValues(getElement("tpl_att")?.value || "");
    }

    function fileNameFromPath(value) {
        const raw = String(value || "").trim();
        if (!raw) return "";
        const segments = raw.split(/[\\/]/);
        return segments[segments.length - 1] || raw;
    }

    function renderAttachmentList(values) {
        const list = getElement("tpl_attachment_list");
        const dropzone = getElement("tpl_attachment_dropzone");
        const items = Array.isArray(values) ? values : getAttachmentValues();

        if (list) {
            list.innerHTML = items.length
                ? items
                    .map(
                        (path) => `
                    <span class="inline-flex max-w-full items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 shadow-sm">
                        <span class="truncate max-w-[220px]" title="${escapeHtml(path)}">${escapeHtml(fileNameFromPath(path))}</span>
                    </span>
                `,
                    )
                    .join("")
                : '<span class="text-xs text-slate-400">첨부된 파일이 없습니다.</span>';
        }

        if (dropzone) {
            dropzone.classList.toggle("border-primary", items.length > 0);
            dropzone.classList.toggle("bg-primary/5", items.length > 0);
        }
    }

    function setAttachmentValues(values) {
        const input = getElement("tpl_att");
        if (!input) return;

        const uniqueValues = [];
        const seen = new Set();
        (Array.isArray(values) ? values : [])
            .map((value) => String(value || "").trim())
            .filter(Boolean)
            .forEach((value) => {
                const key = normalizePathKey(value);
                if (seen.has(key)) return;
                seen.add(key);
                uniqueValues.push(value);
            });

        input.value = uniqueValues.join(", ");
        renderAttachmentList(uniqueValues);
    }

    function appendAttachmentValues(values) {
        setAttachmentValues([...getAttachmentValues(), ...(Array.isArray(values) ? values : [])]);
    }

    function clearAttachmentFiles() {
        setAttachmentValues([]);
    }

    function parseFileUriToPath(rawValue) {
        const value = String(rawValue || "").trim();
        if (!value.toLowerCase().startsWith("file://")) {
            return "";
        }

        try {
            const url = new URL(value);
            const decodedPath = decodeURIComponent(url.pathname || "");
            if (url.hostname) {
                return `\\\\${url.hostname}${decodedPath.replaceAll("/", "\\")}`;
            }
            if (/^\/[A-Za-z]:\//.test(decodedPath)) {
                return decodedPath.slice(1).replaceAll("/", "\\");
            }
            return decodedPath.replaceAll("/", "\\");
        } catch (error) {
            return "";
        }
    }

    function extractDroppedAttachmentPaths(dataTransfer) {
        if (!dataTransfer) return [];

        const fromFiles = Array.from(dataTransfer.files || [])
            .map((file) =>
                String(file?.path || file?.fullPath || file?.pywebviewFullPath || file?.webkitRelativePath || "").trim(),
            )
            .filter((value) => value && isAbsoluteAttachmentPath(value));

        const uriList = String(dataTransfer.getData("text/uri-list") || "")
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter(Boolean)
            .map((line) => parseFileUriToPath(line))
            .filter(Boolean);

        const plainText = String(dataTransfer.getData("text/plain") || "")
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter((line) => isAbsoluteAttachmentPath(line));

        const uniqueValues = [];
        const seen = new Set();
        [...fromFiles, ...uriList, ...plainText].forEach((value) => {
            const key = normalizePathKey(value);
            if (seen.has(key)) return;
            seen.add(key);
            uniqueValues.push(value);
        });
        return uniqueValues;
    }

    function wireAttachmentInput() {
        const input = getElement("tpl_att");
        if (!input || input.dataset.bound === "true") return;
        input.dataset.bound = "true";
        input.addEventListener("input", () => {
            renderAttachmentList(parseAttachmentValues(input.value));
        });
    }

    function wireAttachmentDropzone() {
        const dropzone = getElement("tpl_attachment_dropzone");
        if (!dropzone || dropzone.dataset.bound === "true") return;
        dropzone.dataset.bound = "true";

        const setDragState = (active) => {
            dropzone.classList.toggle("border-primary", active);
            dropzone.classList.toggle("bg-primary/5", active);
            dropzone.classList.toggle("text-primary", active);
        };

        ["dragenter", "dragover"].forEach((eventName) => {
            dropzone.addEventListener(eventName, (event) => {
                event.preventDefault();
                setDragState(true);
            });
        });

        ["dragleave", "dragend"].forEach((eventName) => {
            dropzone.addEventListener(eventName, () => {
                setDragState(false);
            });
        });

        dropzone.addEventListener("drop", (event) => {
            event.preventDefault();
            setDragState(false);
            const droppedPaths = extractDroppedAttachmentPaths(event.dataTransfer);
            if (!droppedPaths.length) {
                showFlash("드래그한 파일 경로를 읽지 못했습니다. 파일 선택 버튼을 사용해 주세요.");
                return;
            }
            appendAttachmentValues(droppedPaths);
            showFlash(`${droppedPaths.length}개 파일을 첨부 목록에 추가했습니다.`);
        });
    }

    async function pickAttachmentFiles() {
        if (!isPywebviewRuntime() || typeof window.pywebview.api.pick_attachment_files !== "function") {
            showFlash("현재 환경에서는 파일 선택창을 열 수 없습니다. 경로를 직접 입력해 주세요.");
            return;
        }

        try {
            const selectedPaths = await window.pywebview.api.pick_attachment_files();
            if (!Array.isArray(selectedPaths) || !selectedPaths.length) {
                return;
            }
            appendAttachmentValues(selectedPaths);
            showFlash(`${selectedPaths.length}개 파일을 첨부 목록에 추가했습니다.`);
        } catch (error) {
            showFlash(error && error.message ? error.message : "첨부파일 선택에 실패했습니다.");
        }
    }

    function getRecipientInput(inputId) {
        return getElement(inputId);
    }

    function getSuggestionPanel(inputId) {
        return getElement(`${inputId}_suggestions`);
    }

    function findContactByEmail(email) {
        const normalized = String(email || "").trim().toLowerCase();
        if (!normalized) return null;
        return (
            state.addressBookContacts.find(
                (contact) => String(contact.email || "").trim().toLowerCase() === normalized,
            ) || null
        );
    }

    function applyAddressBookProfile(force = false) {
        const emailInput = getElement("set_email");
        const nameInput = getElement("set_name");
        const deptInput = getElement("set_dept");
        const titleInput = getElement("set_title");
        if (!emailInput || !nameInput || !deptInput || !titleInput) return;

        const contact = findContactByEmail(emailInput.value);
        if (!contact) return;

        if (force || !nameInput.value.trim()) nameInput.value = contact.name || "";
        if (force || !deptInput.value.trim()) deptInput.value = contact.department || "";
        if (force || !titleInput.value.trim()) titleInput.value = contact.title || "";
    }

    function currentRecipientToken(input) {
        const value = input.value || "";
        const cursor = input.selectionStart ?? value.length;
        let start = cursor;
        while (start > 0 && !",;\n".includes(value[start - 1])) start -= 1;
        let end = cursor;
        while (end < value.length && !",;\n".includes(value[end])) end += 1;
        return {
            start: start,
            end: end,
            query: value.slice(start, cursor).trim(),
        };
    }

    function findRecipientSuggestions(query) {
        const normalized = normalizeText(query);
        if (!normalized) return [];
        return state.addressBookContacts
            .filter((contact) => {
                const searchText = String(contact.search || "").toLowerCase();
                return (
                    searchText.includes(String(query || "").toLowerCase()) ||
                    normalizeText(contact.label || "").includes(normalized)
                );
            })
            .slice(0, 8);
    }

    function hideRecipientSuggestions(inputId) {
        const panel = getSuggestionPanel(inputId);
        if (!panel) return;
        panel.classList.add("hidden");
        panel.innerHTML = "";
    }

    function renderRecipientSuggestions(inputId) {
        const input = getRecipientInput(inputId);
        const panel = getSuggestionPanel(inputId);
        if (!input || !panel) return;

        const token = currentRecipientToken(input);
        const suggestions = findRecipientSuggestions(token.query);
        if (!suggestions.length) {
            hideRecipientSuggestions(inputId);
            return;
        }

        panel.innerHTML = suggestions
            .map(
                (contact) => `
                <button
                    type="button"
                    data-label="${encodeURIComponent(contact.label || "")}"
                    class="recipient-suggestion-item w-full text-left px-3 py-2 border-b last:border-b-0 border-slate-100 transition-colors"
                    onclick="window.selectRecipientSuggestion('${inputId}', this.dataset.label)"
                >
                    <div class="text-sm font-semibold text-slate-800">${escapeHtml(contact.name || contact.email)}</div>
                    <div class="text-xs text-slate-500">${escapeHtml(contact.department || "")}${contact.department && contact.title ? " / " : ""}${escapeHtml(contact.title || "")}</div>
                    <div class="text-[11px] text-primary mt-0.5">${escapeHtml(contact.email || "")}</div>
                </button>
            `,
            )
            .join("");
        panel.classList.remove("hidden");
    }

    function selectRecipientSuggestion(inputId, encodedLabel) {
        const input = getRecipientInput(inputId);
        if (!input) return;

        const label = decodeURIComponent(encodedLabel || "");
        const currentValue = input.value || "";
        const token = currentRecipientToken(input);
        const prefix = currentValue.slice(0, token.start).replace(/[,\s;]*$/, "");
        const suffix = currentValue.slice(token.end).replace(/^[,\s;]*/, "");
        const values = [];
        if (prefix) values.push(prefix);
        values.push(label);
        if (suffix) values.push(suffix);
        input.value = values.join(", ");
        hideRecipientSuggestions(inputId);
        input.focus();
    }

    function wireRecipientAutocomplete(inputId) {
        const input = getRecipientInput(inputId);
        if (!input || input.dataset.addressBookBound === "true") return;

        input.dataset.addressBookBound = "true";
        input.addEventListener("input", () => renderRecipientSuggestions(inputId));
        input.addEventListener("focus", () => renderRecipientSuggestions(inputId));
        input.addEventListener("keydown", (event) => {
            if (event.key === "Escape") hideRecipientSuggestions(inputId);
        });
        input.addEventListener("blur", () => {
            window.setTimeout(() => hideRecipientSuggestions(inputId), 120);
        });
    }

    function renderMailboxOptions(mailboxes) {
        const input = getElement("set_mailbox");
        if (!input) return;

        let datalist = getElement("mailbox-options");
        if (!datalist) {
            datalist = document.createElement("datalist");
            datalist.id = "mailbox-options";
            document.body.appendChild(datalist);
        }

        const options = Array.isArray(mailboxes) ? mailboxes : [];
        datalist.innerHTML = options.map((mailbox) => `<option value="${escapeHtml(mailbox)}"></option>`).join("");
        if (options.length > 0) input.setAttribute("list", "mailbox-options");
        else input.removeAttribute("list");
    }

    function setAutosendSaveLocked(locked) {
        state.autosendSaveLocked = Boolean(locked);
        ["tpl_new_btn", "tpl_save_mail_btn", "tpl_save_registration_btn"].forEach((buttonId) => {
            const button = getElement(buttonId);
            if (!button) return;
            button.disabled = state.autosendSaveLocked;
            button.classList.toggle("opacity-60", state.autosendSaveLocked);
            button.classList.toggle("cursor-not-allowed", state.autosendSaveLocked);
        });
    }

    function setSidebarCollapsed(collapsed) {
        const sidebar = getElement("sidebar");
        const icon = getElement("sidebar-icon");
        if (!sidebar || !icon) return;
        state.sidebarCollapsed = Boolean(collapsed);
        sidebar.classList.toggle("collapsed", state.sidebarCollapsed);
        icon.innerText = state.sidebarCollapsed ? ">" : "<";
    }

    function syncResponsiveSidebar() {
        if (state.sidebarMode !== "auto") {
            return;
        }
        setSidebarCollapsed(window.innerWidth < SIDEBAR_AUTO_COLLAPSE_BREAKPOINT);
    }

    function toggleSidebar() {
        state.sidebarMode = "manual";
        setSidebarCollapsed(!state.sidebarCollapsed);
    }

    function resolveStoredThemeMode() {
        try {
            const storedMode = window.localStorage.getItem(THEME_STORAGE_KEY);
            return storedMode === "dark" ? "dark" : "light";
        } catch (error) {
            return "light";
        }
    }

    function renderThemeToggle() {
        const lightButton = getElement("theme-light-btn");
        const darkButton = getElement("theme-dark-btn");
        if (!lightButton || !darkButton) return;

        const isDark = state.themeMode === "dark";
        lightButton.classList.toggle("is-active", !isDark);
        darkButton.classList.toggle("is-active", isDark);
        lightButton.setAttribute("aria-pressed", String(!isDark));
        darkButton.setAttribute("aria-pressed", String(isDark));
    }

    function applyThemeMode(mode, options) {
        const persist = options?.persist !== false;
        const nextMode = mode === "dark" ? "dark" : "light";
        state.themeMode = nextMode;

        const root = document.documentElement;
        if (root) {
            root.classList.toggle("dark", nextMode === "dark");
            root.classList.toggle("light", nextMode !== "dark");
            root.style.colorScheme = nextMode;
        }

        if (persist) {
            try {
                window.localStorage.setItem(THEME_STORAGE_KEY, nextMode);
            } catch (error) {
                // Ignore storage failures and keep the in-memory theme.
            }
        }

        renderThemeToggle();
    }

    function setThemeMode(mode) {
        applyThemeMode(mode, { persist: true });
    }

    function isDashboardPageActive() {
        return resolvePageId({ page: state.currentPage || PAGE_LABEL_BY_ID.dashboard }) === "dashboard";
    }

    function isManualSyncRunning() {
        return Boolean(state.syncProgress && state.syncProgress.running);
    }

    function getSyncStageLabel(progress) {
        const stage = String(progress && progress.stage ? progress.stage : "").trim().toLowerCase();
        if (stage === "syncing") return "메일 수집 중";
        if (stage === "analyzing") return "AI 분석 중";
        if (stage === "complete") return "동기화 완료";
        if (stage === "error") return "동기화 오류";
        return "대기 중";
    }

    function stopSyncPolling() {
        if (state.syncPollTimer) {
            window.clearTimeout(state.syncPollTimer);
            state.syncPollTimer = null;
        }
        state.syncPollInFlight = false;
    }

    function queueNextSyncPoll() {
        if (state.syncPollTimer || !isManualSyncRunning() || !isDashboardPageActive()) {
            return;
        }
        state.syncPollTimer = window.setTimeout(async () => {
            state.syncPollTimer = null;
            await pollDashboardWhileSyncing();
        }, SYNC_POLL_INTERVAL_MS);
    }

    async function pollDashboardWhileSyncing() {
        if (!isManualSyncRunning() || !isDashboardPageActive() || state.syncPollInFlight) {
            return;
        }

        state.syncPollInFlight = true;
        try {
            await UiBridge.dispatch({ action: "refresh_dashboard", page: state.currentPage || PAGE_LABEL_BY_ID.dashboard });
        } finally {
            state.syncPollInFlight = false;
            if (isManualSyncRunning() && isDashboardPageActive()) {
                queueNextSyncPoll();
            }
        }
    }

    function syncDashboardPolling() {
        if (isManualSyncRunning() && isDashboardPageActive()) {
            queueNextSyncPoll();
            return;
        }
        stopSyncPolling();
    }

    function renderSyncButton() {
        const button = getElement("sync-btn");
        const icon = getElement("sync-icon");
        const text = getElement("sync-text");
        const running = isManualSyncRunning();
        const label = running ? getSyncStageLabel(state.syncProgress) : "동기화";

        if (button) {
            button.disabled = running;
            button.classList.toggle("opacity-70", running);
            button.classList.toggle("cursor-not-allowed", running);
        }
        if (icon) {
            icon.classList.toggle("animate-spin", running);
        }
        if (text) {
            text.innerText = label;
        }
    }

    function captureDashboardScrollPositions() {
        return {
            threadList: getElement("priority-thread-list")?.scrollTop || 0,
            mailSummary: getElement("priority-thread-detail-view")?.scrollTop || 0,
        };
    }

    function restoreDashboardScrollPositions(positions) {
        const nextPositions = positions || {};
        const threadList = getElement("priority-thread-list");
        const mailSummary = getElement("priority-thread-detail-view");

        if (threadList) threadList.scrollTop = Number(nextPositions.threadList || 0);
        if (mailSummary) mailSummary.scrollTop = Number(nextPositions.mailSummary || 0);
    }

    function syncMail() {
        if (isManualSyncRunning()) {
            return;
        }

        state.syncProgress = {
            ...(state.syncProgress && typeof state.syncProgress === "object" ? state.syncProgress : {}),
            running: true,
            stage: "syncing",
            message: "메일 수집 중",
            error: "",
        };
        renderSyncButton();
        syncDashboardPolling();
        UiBridge.dispatch({ action: "sync_mail" });
    }

    function navigate(page) {
        state.currentPage = page;
        UiBridge.dispatch({ action: "navigate", page: page });
    }

    function resolvePageId(args) {
        const explicitPageId = String(args.page_id || "").trim();
        if (explicitPageId && HEADER_TITLES[explicitPageId]) {
            return explicitPageId;
        }

        const pageLabel = String(args.page || state.currentPage || "").trim();
        return PAGE_ID_BY_LABEL[pageLabel] || "dashboard";
    }

    function toggleTask(taskId, checked) {
        UiBridge.dispatch({ action: "toggle_task", payload: { task_id: taskId, checked: checked } });
    }


    function requestDashboardRefresh() {
        return UiBridge.dispatch({ action: "refresh_dashboard", page: state.currentPage || PAGE_LABEL_BY_ID.dashboard });
    }


    function setDashboardMailTab(tabKey) {
        const normalized = ["category_1", "category_2", "category_3"].includes(String(tabKey || ""))
            ? String(tabKey)
            : "category_1";
        if (normalized === state.dashboardMailTab) {
            return;
        }
        state.dashboardMailTab = normalized;
        state.dashboardMailView = "list";
        requestDashboardRefresh();
    }

    function selectDashboardMail(mailId) {
        const nextMailId = Number(mailId || 0);
        if (!nextMailId) {
            return;
        }
        state.selectedMailId = nextMailId;
        state.dashboardMailView = "detail";
        UiBridge.dispatch({
            action: "select_mail",
            payload: { mail_id: nextMailId },
            page: state.currentPage || PAGE_LABEL_BY_ID.dashboard,
        });
    }

    function dispatchDashboardMailCollectionAction(action, mailId) {
        const nextMailId = Number(mailId || 0);
        if (!nextMailId) {
            return;
        }
        if (Number(state.selectedMailId || 0) === nextMailId) {
            state.dashboardMailView = "list";
        }
        UiBridge.dispatch({
            action,
            payload: { mail_id: nextMailId },
            page: state.currentPage || PAGE_LABEL_BY_ID.dashboard,
        });
    }

    function handleDashboardMailCollectionAction(event, action, mailId) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        if (action === "restore") {
            dispatchDashboardMailCollectionAction("restore_mail", mailId);
            return;
        }
        if (action === "archive") {
            dispatchDashboardMailCollectionAction("archive_mail", mailId);
            return;
        }
        if (action === "complete") {
            dispatchDashboardMailCollectionAction("complete_mail", mailId);
        }
    }

    function showDashboardMailList() {
        if (state.dashboardMailView === "list") {
            return;
        }
        state.dashboardMailView = "list";
        requestDashboardRefresh();
    }

    function showFlash(message) {
        const flash = getElement("flash-msg");
        const text = getElement("flash-text");
        if (!flash || !text) return;

        if (!message) {
            flash.style.display = "none";
            return;
        }

        text.innerText = message;
        flash.style.display = "flex";
        if (state.flashTimer) window.clearTimeout(state.flashTimer);
        state.flashTimer = window.setTimeout(() => {
            flash.style.display = "none";
        }, 5000);
    }

    function clearSecret(type) {
        UiBridge.dispatch({ action: "clear_secret", payload: { type: type } });
    }

    function isHanlimAiProvider(provider) {
        const normalized = String(provider || "").trim().toLowerCase();
        return normalized === "hanlim_openai" || normalized === "hanlim_google_compat";
    }

    function getAiProviderLabel(provider) {
        return isHanlimAiProvider(provider) ? "사내 AI 허브" : "Google AI";
    }

    function handleAiProviderChange() {
        const provider = getElement("set_ai_provider")?.value || "gemini";
        const isHanlim = isHanlimAiProvider(provider);
        const geminiWrap = getElement("set_gemini_api_wrap");
        const hanlimWrap = getElement("set_hanlim_api_wrap");
        const baseUrlInput = getElement("set_ai_base_url");
        const modelSelect = getElement("set_model");

        if (geminiWrap) geminiWrap.classList.toggle("hidden", isHanlim);
        if (hanlimWrap) hanlimWrap.classList.toggle("hidden", !isHanlim);

        if (baseUrlInput) {
            baseUrlInput.disabled = !isHanlim;
            if (isHanlim && !String(baseUrlInput.value || "").trim()) {
                baseUrlInput.value = "https://ai.hanliminve.com/llm_hub/api/v1";
            }
            if (!isHanlim) {
                baseUrlInput.value = "";
            }
        }

        if (modelSelect) {
            const currentModel = String(modelSelect.value || "").trim();
            if (isHanlim && currentModel !== "hanlimAI") {
                modelSelect.value = "hanlimAI";
            }
            if (!isHanlim && currentModel === "hanlimAI") {
                modelSelect.value = "gemini-2.5-flash";
            }
        }
    }

    function buildSettingsPayload() {
        return {
            config: {
                user_email: getElement("set_email")?.value || "",
                user_display_name: getElement("set_name")?.value || "",
                user_department: getElement("set_dept")?.value || "",
                user_job_title: getElement("set_title")?.value || "",
                mailbox: getElement("set_mailbox")?.value || "",
                sync_days: getElement("set_days")?.value || "",
                sync_batch_size: getElement("set_batch")?.value || "",
                sync_scan_limit: getElement("set_scan_limit")?.value || "",
                sync_interval_minutes: getElement("set_interval")?.value || "",
                preview_max_chars: getElement("set_preview_chars")?.value || "",
                gemini_timeout_seconds: getElement("set_timeout")?.value || "",
                store_raw_body: getElement("set_store_raw_body")?.checked ?? false,
                ai_provider: getElement("set_ai_provider")?.value || "gemini",
                ai_base_url: getElement("set_ai_base_url")?.value || "",
                gemini_model: getElement("set_model")?.value || "",
            },
            password: getElement("set_pwd")?.value || "",
            api_key: getElement("set_api")?.value || "",
            hanlim_api_key: getElement("set_hanlim_api")?.value || "",
        };
    }

    function testMailbox() {
        UiBridge.dispatch({ action: "mailbox_test", payload: buildSettingsPayload() });
    }

    function saveSettings() {
        const button = getElement("save-settings-btn");
        if (button) {
            button.innerText = "저장 중...";
            button.classList.add("opacity-50");
        }

        UiBridge.dispatch({
            action: "save_settings",
            payload: buildSettingsPayload(),
        });
    }

    function schedCmd(cmd) {
        UiBridge.dispatch({ action: "scheduler_cmd", payload: { cmd: cmd } });
    }

    function buildFormPayload() {
        return {
            name: getElement("tpl_name")?.value || "",
            subject: getElement("tpl_subject")?.value || "",
            to_raw: getElement("tpl_to")?.value || "",
            cc_raw: getElement("tpl_cc")?.value || "",
            repeat_type: getElement("tpl_repeat")?.value || "none",
            first_send_at: fromDateTimeLocalInputValue(getElement("tpl_first")?.value || ""),
            send_time: toTimeInputValue(getElement("tpl_time")?.value || ""),
            attachment_raw: getElement("tpl_att")?.value || "",
            body: getElement("tpl_body")?.value || "",
            enabled: getElement("tpl_enabled")?.checked ?? true,
        };
    }

    function addMonthsToDate(value, count) {
        const next = new Date(value.getTime());
        const targetMonth = next.getMonth() + count;
        next.setDate(1);
        next.setMonth(targetMonth);
        const daysInMonth = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate();
        next.setDate(Math.min(value.getDate(), daysInMonth));
        return next;
    }

    function computeAutosendNextRun(payload) {
        const repeatType = String(payload.repeat_type || "none").trim().toLowerCase();
        const first = parseUiDate(payload.first_send_at || "");
        const timeText = String(payload.send_time || "").trim();
        if (!first || !timeText) return null;

        const timeMatch = timeText.match(/^(\d{2}):(\d{2})/);
        if (!timeMatch) return null;

        const nextRun = new Date(first.getTime());
        nextRun.setSeconds(0, 0);
        nextRun.setHours(Number(timeMatch[1]), Number(timeMatch[2]), 0, 0);

        const now = new Date();
        if (repeatType === "none") {
            return nextRun >= now ? nextRun : now;
        }

        while (nextRun < now) {
            if (repeatType === "daily") {
                nextRun.setDate(nextRun.getDate() + 1);
                continue;
            }
            if (repeatType === "weekly") {
                nextRun.setDate(nextRun.getDate() + 7);
                continue;
            }
            if (repeatType === "monthly") {
                const advanced = addMonthsToDate(nextRun, 1);
                nextRun.setTime(advanced.getTime());
                continue;
            }
            return null;
        }

        return nextRun;
    }

    function validateAutosendPayload(payload, options) {
        const settings = options || {};
        const requireSchedule = Boolean(settings.requireSchedule);
        const errors = [];
        if (requireSchedule && !String(payload.name || "").trim()) {
            errors.push("등록 이름을 입력해 주세요.");
        }
        if (!String(payload.to_raw || "").trim()) {
            errors.push("받는 사람(To)을 한 명 이상 입력해 주세요.");
        }
        if (requireSchedule) {
            if (!String(payload.first_send_at || "").trim()) {
                errors.push("첫 발송 일시를 입력해 주세요.");
            }
            if (!String(payload.send_time || "").trim()) {
                errors.push("발송 시간을 입력해 주세요.");
            }
            if (
                String(payload.first_send_at || "").trim() &&
                String(payload.send_time || "").trim() &&
                !computeAutosendNextRun(payload)
            ) {
                errors.push("다음 실행 시각을 계산할 수 있도록 일시를 다시 확인해 주세요.");
            }
        }
        return errors;
    }

    function renderAutosendAssist() {
        const preview = getElement("tpl_next_run_preview");
        const hint = getElement("tpl_validation_hint");
        if (!preview || !hint) return;

        const payload = buildFormPayload();
        const hasMeaningfulInput = Boolean(
            String(payload.name || "").trim()
            || String(payload.subject || "").trim()
            || String(payload.to_raw || "").trim()
            || String(payload.body || "").trim(),
        );
        if (!hasMeaningfulInput) {
            preview.innerText = "받는 사람과 발송 일정을 입력하면 다음 실행 시각을 바로 보여줍니다.";
            hint.innerText = "테스트 발송은 현재 입력된 수신자와 본문으로 즉시 실행됩니다.";
            return;
        }

        const errors = validateAutosendPayload(payload, { requireSchedule: true });
        if (errors.length > 0) {
            preview.innerText = "저장 전 확인이 필요합니다.";
            hint.innerText = errors.join(" ");
            return;
        }

        const nextRun = computeAutosendNextRun(payload);
        const nextRunText = nextRun
            ? `${nextRun.getFullYear()}-${pad2(nextRun.getMonth() + 1)}-${pad2(nextRun.getDate())} ${pad2(nextRun.getHours())}:${pad2(nextRun.getMinutes())}`
            : "";
        preview.innerText = nextRun
            ? `다음 실행: ${formatReadableDate(nextRunText, { includeTime: true })}`
            : "다음 실행을 계산할 수 없습니다.";
        hint.innerText = String(payload.repeat_type || "none") === "none"
            ? "1회 발송은 저장 후 첫 실행 시점에 한 번만 예약됩니다."
            : "저장 후 스케줄러가 이 규칙을 기준으로 다음 발송을 예약합니다.";
    }

    function wireAutosendAssist() {
        const fieldIds = ["tpl_name", "tpl_subject", "tpl_to", "tpl_cc", "tpl_repeat", "tpl_first", "tpl_time", "tpl_body", "tpl_enabled"];
        fieldIds.forEach((id) => {
            const element = getElement(id);
            if (!element || element.dataset.assistBound === "true") return;
            element.dataset.assistBound = "true";
            element.addEventListener("input", renderAutosendAssist);
            element.addEventListener("change", renderAutosendAssist);
        });
    }

    function fillTemplateForm(item, sourceType) {
        if (!item) return;

        const templateIdInput = getElement("tpl_template_id");
        const registrationIdInput = getElement("tpl_registration_id");
        const legacyIdInput = getElement("tpl_id");

        if (templateIdInput) templateIdInput.value = sourceType === "template" ? String(item.id || "") : "";
        if (registrationIdInput) registrationIdInput.value = sourceType === "registration" ? String(item.id || "") : "";
        if (legacyIdInput) legacyIdInput.value = sourceType === "registration" ? String(item.id || "") : "";

        getElement("tpl_header_title").innerText = item.name || "메일 작성";
        getElement("tpl_name").value = item.name || "";
        getElement("tpl_subject").value = item.subject || "";
        getElement("tpl_to").value = item.to_raw || "";
        getElement("tpl_cc").value = item.cc_raw || "";
        getElement("tpl_repeat").value = item.repeat_type || "none";
        getElement("tpl_first").value = toDateTimeLocalInputValue(item.first_send_at);
        getElement("tpl_time").value = toTimeInputValue(item.send_time);
        setAttachmentValues(parseAttachmentValues(item.attachment_raw || ""));
        getElement("tpl_body").value = item.body || "";
        getElement("tpl_enabled").checked = item.enabled ?? true;
        renderAutosendAssist();
    }

    function resetTemplateForm() {
        const templateIdInput = getElement("tpl_template_id");
        const registrationIdInput = getElement("tpl_registration_id");
        const legacyIdInput = getElement("tpl_id");

        if (templateIdInput) templateIdInput.value = "";
        if (registrationIdInput) registrationIdInput.value = "";
        if (legacyIdInput) legacyIdInput.value = "";

        getElement("tpl_header_title").innerText = "메일 작성";
        getElement("tpl_name").value = "";
        getElement("tpl_subject").value = "";
        getElement("tpl_to").value = "";
        getElement("tpl_cc").value = "";
        getElement("tpl_repeat").value = "none";
        getElement("tpl_first").value = getLocalDateTimeValue();
        getElement("tpl_time").value = getLocalTimeValue();
        clearAttachmentFiles();
        getElement("tpl_body").value = "";
        getElement("tpl_enabled").checked = true;
        setAutosendSaveLocked(false);
        renderAutosendAssist();
    }

    function loadMailTemplate(id) {
        const item = state.currentMailTemplates.find((entry) => String(entry.id) === String(id));
        fillTemplateForm(item, "template");
    }

    function editSendRegistration(id) {
        const item = state.currentSendRegistrations.find((entry) => String(entry.id) === String(id));
        fillTemplateForm(item, "registration");
    }

    function newMailTemplate() {
        resetTemplateForm();
    }

    function newSendRegistration() {
        resetTemplateForm();
    }

    function saveMailTemplate() {
        if (state.autosendSaveLocked) return;
        setAutosendSaveLocked(true);
        const payload = buildFormPayload();
        payload.id = getElement("tpl_template_id")?.value || "";
        UiBridge.dispatch({ action: "save_mail_template", payload: payload });
    }

    function saveSendRegistration() {
        if (state.autosendSaveLocked) return;
        const payload = buildFormPayload();
        const validationErrors = validateAutosendPayload(payload, { requireSchedule: true });
        if (validationErrors.length > 0) {
            showFlash(validationErrors.join(" "));
            setAutosendSaveLocked(false);
            return;
        }
        setAutosendSaveLocked(true);
        payload.id = getElement("tpl_registration_id")?.value || getElement("tpl_id")?.value || "";
        UiBridge.dispatch({ action: "save_send_registration", payload: payload });
    }

    function testSendRegistration() {
        if (state.autosendSaveLocked) return;
        const payload = buildFormPayload();
        const validationErrors = validateAutosendPayload(payload, { requireSchedule: false });
        if (validationErrors.length > 0) {
            showFlash(validationErrors.join(" "));
            return;
        }
        UiBridge.dispatch({ action: "test_send_registration", payload: payload });
    }

    function deleteMailTemplate(id) {
        if (!window.confirm("템플릿을 삭제하시겠습니까?")) return;
        UiBridge.dispatch({ action: "delete_mail_template", payload: { id: id } });
    }

    function deleteSendRegistration(id) {
        if (!window.confirm("발송등록을 삭제하시겠습니까?")) return;
        UiBridge.dispatch({ action: "delete_send_registration", payload: { id: id } });
    }


    function renderUser(user) {
        const email = getElement("user-email");
        const name = getElement("user-name");
        const avatar = getElement("user-avatar");
        if (!user) return;

        if (email) email.innerText = user.email || "알 수 없음";
        if (name) name.innerText = user.name || "사용자";
        if (avatar) {
            const seed = String(user.name || user.email || "US").trim();
            avatar.innerText = seed.substring(0, 2).toUpperCase();
        }
    }




    function localizeThreadCopy(value) {
        const text = String(value || "").trim();
        if (!text) return "";
        if (STOCK_THREAD_COPY[text]) return STOCK_THREAD_COPY[text];

        const deadlineMoved = text.match(/^Deadline moved to (.+)\.$/);
        if (deadlineMoved) return `마감이 ${deadlineMoved[1]}로 변경되었습니다.`;

        const newAttachment = text.match(/^New attachment: (.+)\.$/);
        if (newAttachment) return `새 첨부파일: ${newAttachment[1]}`;

        const newReply = text.match(/^New reply from (.+)\.$/);
        if (newReply) return `${newReply[1]}의 새 답장이 도착했습니다.`;

        const dueAt = text.match(/^Due at (.+)\.$/);
        if (dueAt) return `기한: ${dueAt[1]}`;

        const replyTo = text.match(/^Reply to (.+)\.$/);
        if (replyTo) return `${replyTo[1]}에게 답장이 필요합니다.`;

        const lastOutbound = text.match(/^Last outbound mail was (.+)\.$/);
        if (lastOutbound) return `마지막 발신 시각: ${lastOutbound[1]}`;

        const waitingSince = text.match(/^Waiting since (.+)\.$/);
        if (waitingSince) return `${waitingSince[1]}부터 회신을 기다리는 중입니다.`;

        return text;
    }

    function localizeFollowUpLabel(status, fallbackLabel) {
        const normalizedStatus = String(status || "").trim().toLowerCase();
        if (FOLLOW_UP_LABELS[normalizedStatus]) {
            return FOLLOW_UP_LABELS[normalizedStatus];
        }

        const localizedFallback = localizeThreadCopy(fallbackLabel);
        return localizedFallback || FOLLOW_UP_LABELS.tracked;
    }

    function localizePriorityReason(reason) {
        const text = String(reason || "").trim();
        if (!text) return "";
        return PRIORITY_REASON_LABELS[text] || localizeThreadCopy(text);
    }

    function localizeTimelineDirection(direction) {
        return direction === "outbound" ? "내 발송" : "수신";
    }

    function matchesDashboardThreadFilter(thread, filterKey) {
        switch (filterKey) {
            case "today":
                return ["overdue", "deadline_soon"].includes(thread.follow_up_status);
            case "reply":
                return thread.follow_up_status === "reply_needed";
            case "approval":
                return thread.follow_up_status === "approval_pending";
            case "waiting":
                return ["waiting_for_reply", "no_reply_3d"].includes(thread.follow_up_status);
            case "review":
                return thread.follow_up_status === "review_needed" || Boolean(thread.needs_review);
            default:
                return true;
        }
    }









    function getVisibleDashboardThreads() {
        return state.dashboardThreads;
    }








    function followUpToneClass(status) {
        switch (status) {
            case "overdue":
                return "dashboard-tone dashboard-tone-overdue";
            case "deadline_soon":
                return "dashboard-tone dashboard-tone-deadline_soon";
            case "approval_pending":
                return "dashboard-tone dashboard-tone-approval_pending";
            case "reply_needed":
                return "dashboard-tone dashboard-tone-reply_needed";
            case "no_reply_3d":
                return "dashboard-tone dashboard-tone-no_reply_3d";
            case "waiting_for_reply":
                return "dashboard-tone dashboard-tone-waiting_for_reply";
            case "review_needed":
                return "dashboard-tone dashboard-tone-review_needed";
            case "action_needed":
                return "dashboard-tone dashboard-tone-action_needed";
            default:
                return "dashboard-tone dashboard-tone-neutral";
        }
    }

    function priorityToneClass(priority) {
        switch (String(priority || "").toLowerCase()) {
            case "high":
                return "dashboard-priority-pill dashboard-priority-high";
            case "medium":
                return "dashboard-priority-pill dashboard-priority-medium";
            case "low":
                return "dashboard-priority-pill dashboard-priority-low";
            default:
                return "dashboard-priority-pill dashboard-priority-low";
        }
    }

    function timelineDirectionClass(direction) {
        return direction === "outbound"
            ? "dashboard-direction-pill dashboard-direction-outbound"
            : "dashboard-direction-pill dashboard-direction-inbound";
    }


    function switchTaskTab(tabName) {
        state.dashboardTaskTab = tabName === "completed" ? "completed" : "open";

        const openButton = getElement("task-tab-open");
        const completedButton = getElement("task-tab-completed");
        const openContainer = getElement("task-container");
        const completedContainer = getElement("completed-task-container");

        if (openButton) {
            openButton.className = state.dashboardTaskTab === "open" ? "dashboard-tab-btn is-active" : "dashboard-tab-btn";
        }
        if (completedButton) {
            completedButton.className = state.dashboardTaskTab === "completed"
                ? "dashboard-tab-btn is-active"
                : "dashboard-tab-btn";
        }
        if (openContainer) openContainer.classList.toggle("hidden", state.dashboardTaskTab !== "open");
        if (completedContainer) completedContainer.classList.toggle("hidden", state.dashboardTaskTab !== "completed");
    }

    function renderDashboardOverview(tasks, completedTasks, selectedThread) {
        const container = getElement("dashboard-overview-grid");
        if (!container) return;

        const threads = state.dashboardThreads;
        const urgentCount = threads.filter((thread) => matchesDashboardThreadFilter(thread, "today")).length;
        const responseCount = threads.filter((thread) =>
            ["reply_needed", "approval_pending"].includes(String(thread.follow_up_status || "").toLowerCase()),
        ).length;
        const waitingCount = threads.filter((thread) =>
            ["waiting_for_reply", "no_reply_3d"].includes(String(thread.follow_up_status || "").toLowerCase()),
        ).length;
        const openTaskCount = Array.isArray(tasks) ? tasks.length : 0;
        const doneTaskCount = Array.isArray(completedTasks) ? completedTasks.length : 0;
        const focusThread = selectedThread || threads[0] || null;

        const focusStatusMarkup = focusThread
            ? `<span class="${followUpToneClass(focusThread.follow_up_status)}">${escapeHtml(localizeFollowUpLabel(focusThread.follow_up_status, focusThread.follow_up_label))}</span>`
            : '<span class="dashboard-tone dashboard-tone-neutral">대기 중</span>';
        const focusTitle = focusThread
            ? escapeHtml(focusThread.subject || localizeThreadCopy("Untitled thread"))
            : "우선순위 스레드가 없습니다.";
        const focusBody = focusThread
            ? escapeHtml(localizeThreadCopy(focusThread.current_conclusion || focusThread.latest_summary || focusThread.follow_up_detail || "-"))
            : "메일 동기화 후 AI 브리프가 채워집니다.";
        const focusReason = focusThread
            ? escapeHtml(
                localizePriorityReason(
                    Array.isArray(focusThread.priority_reasons) && focusThread.priority_reasons.length > 0
                        ? focusThread.priority_reasons[0]
                        : "Recent thread",
                ),
            )
            : "현재 큐가 비어 있습니다.";
        const focusDue = focusThread && focusThread.due_date
            ? `<span>기한 ${escapeHtml(focusThread.due_date)}</span>`
            : "";
        const focusMeta = focusThread
            ? `
                <div class="dashboard-focus-meta">
                    <span>${escapeHtml(focusThread.latest_sender || "-")}</span>
                    <span>${escapeHtml(focusThread.latest_received_at || "-")}</span>
                    <span>메일 ${Number(focusThread.mail_count || 0)}통</span>
                    <span>열린 액션 ${Number((focusThread.my_actions || []).length)}개</span>
                    ${focusDue}
                </div>
            `
            : '<div class="dashboard-focus-meta"><span>지금은 표시할 포커스 스레드가 없습니다.</span></div>';

        container.innerHTML = `
            <article class="dashboard-focus-card">
                <div class="dashboard-focus-header">
                    <div>
                        <p class="dashboard-section-title dashboard-section-title-primary">현재 포커스</p>
                        <h4 class="dashboard-focus-title">${focusTitle}</h4>
                    </div>
                    <div class="flex items-center gap-2">
                        ${focusThread && focusThread.due_date ? formatDdayChip(focusThread.due_date) : ""}
                        ${focusStatusMarkup}
                    </div>
                </div>
                <p class="dashboard-focus-body">${focusBody}</p>
                <p class="dashboard-kpi-foot">우선순위 사유: ${focusReason}</p>
                ${focusMeta}
            </article>
            <article class="dashboard-kpi-card is-highlight">
                <p class="dashboard-kpi-label">전체 스레드</p>
                <p class="dashboard-kpi-value">${Number(threads.length || 0)}</p>
                <p class="dashboard-kpi-foot">AI 선별 대상</p>
            </article>
            <article class="dashboard-kpi-card">
                <p class="dashboard-kpi-label dashboard-kpi-label-danger">긴급</p>
                <p class="dashboard-kpi-value">${Number(urgentCount || 0)}</p>
                <div class="dashboard-kpi-bar"><div class="dashboard-kpi-bar-fill dashboard-kpi-bar-danger" style="width:${threads.length ? Math.round(urgentCount / threads.length * 100) : 0}%"></div></div>
                <p class="dashboard-kpi-foot">전체의 ${threads.length ? Math.round(urgentCount / threads.length * 100) : 0}%</p>
            </article>
            <article class="dashboard-kpi-card">
                <p class="dashboard-kpi-label dashboard-kpi-label-warning">응답/승인</p>
                <p class="dashboard-kpi-value">${Number(responseCount || 0)}</p>
                <div class="dashboard-kpi-bar"><div class="dashboard-kpi-bar-fill dashboard-kpi-bar-warning" style="width:${threads.length ? Math.round(responseCount / threads.length * 100) : 0}%"></div></div>
                <p class="dashboard-kpi-foot">전체의 ${threads.length ? Math.round(responseCount / threads.length * 100) : 0}%</p>
            </article>
            <article class="dashboard-kpi-card">
                <p class="dashboard-kpi-label dashboard-kpi-label-success">내 액션</p>
                <p class="dashboard-kpi-value">${Number(openTaskCount || 0)}</p>
                <div class="dashboard-kpi-bar"><div class="dashboard-kpi-bar-fill dashboard-kpi-bar-success" style="width:${(openTaskCount + doneTaskCount) ? Math.round(doneTaskCount / (openTaskCount + doneTaskCount) * 100) : 0}%"></div></div>
                <p class="dashboard-kpi-foot">완료율 ${(openTaskCount + doneTaskCount) ? Math.round(doneTaskCount / (openTaskCount + doneTaskCount) * 100) : 0}% · 회신 대기 ${Number(waitingCount || 0)}개</p>
            </article>
        `;
    }






    function renderSettingsHealthCards(args) {
        const grid = getElement("settings-health-grid");
        if (!grid) return;

        const cardConfig = args.config || {};
        const cardSecrets = args.secrets || {};
        const activeProvider = cardConfig.ai_provider || "gemini";
        const mailProfileReady = Boolean(cardConfig.user_email && cardConfig.mailbox);
        const passwordReady = Boolean(cardSecrets.password);
        const providerKeyReady = isHanlimAiProvider(activeProvider)
            ? Boolean(cardSecrets.hanlim_api_key)
            : Boolean(cardSecrets.api_key);
        const cardsData = [
            {
                title: "메일 연결",
                tone: mailProfileReady && passwordReady ? "emerald" : "amber",
                status: mailProfileReady && passwordReady ? "준비됨" : "입력 필요",
                detail: mailProfileReady
                    ? `${escapeHtml(cardConfig.user_email || "-")} / ${escapeHtml(cardConfig.mailbox || "INBOX")}`
                    : "메일 주소와 메일함을 확인해 주세요.",
            },
            {
                title: "AI 연결",
                tone: providerKeyReady ? "emerald" : "amber",
                status: providerKeyReady ? "준비됨" : "입력 필요",
                detail: providerKeyReady
                    ? `${escapeHtml(getAiProviderLabel(activeProvider))} / ${escapeHtml(cardConfig.gemini_model || "gemini-2.5-flash")} / 제한 ${Number(cardConfig.gemini_timeout_seconds || 60)}초`
                    : `${escapeHtml(getAiProviderLabel(activeProvider))} 키를 저장하면 자동 분석이 활성화됩니다.`,
            },
            {
                title: "동기화 규칙",
                tone: "slate",
                status: `${Number(cardConfig.sync_interval_minutes || 60)}분 주기`,
                detail: `최근 ${Number(cardConfig.sync_days || 30)}일 / 배치 ${Number(cardConfig.sync_batch_size || 50)}건 / 스캔 ${Number(cardConfig.sync_scan_limit || 200)}건`,
            },
        ];

        grid.innerHTML = cardsData
            .map((card) => {
                const toneClass = card.tone === "emerald"
                    ? "border-emerald-200 bg-emerald-50"
                    : card.tone === "amber"
                        ? "border-amber-200 bg-amber-50"
                        : "border-slate-200 bg-white";
                const statusClass = card.tone === "emerald"
                    ? "text-emerald-700"
                    : card.tone === "amber"
                        ? "text-amber-700"
                        : "text-slate-700";
                return `
                    <div class="rounded-xl border ${toneClass} px-4 py-4 shadow-sm">
                        <div class="flex items-center justify-between gap-3">
                            <span class="text-xs font-bold text-slate-500">${escapeHtml(card.title)}</span>
                            <span class="text-xs font-bold ${statusClass}">${escapeHtml(card.status)}</span>
                        </div>
                        <p class="mt-2 text-sm text-slate-700">${card.detail}</p>
                    </div>
                `;
            })
            .join("");
        return;

        const config = args.config || {};
        const secrets = args.secrets || {};
        const hasMailProfile = Boolean(config.user_email && config.mailbox);
        const hasPassword = Boolean(secrets.password);
        const hasApiKey = Boolean(secrets.api_key);

        const cards = [
            {
                title: "메일 연결",
                tone: hasMailProfile && hasPassword ? "emerald" : "amber",
                status: hasMailProfile && hasPassword ? "준비됨" : "입력 필요",
                detail: hasMailProfile
                    ? `${escapeHtml(config.user_email || "-")} / ${escapeHtml(config.mailbox || "INBOX")}`
                    : "메일 주소와 메일함을 확인해 주세요.",
            },
            {
                title: "AI 연결",
                tone: hasApiKey ? "emerald" : "amber",
                status: hasApiKey ? "준비됨" : "입력 필요",
                detail: hasApiKey
                    ? `${escapeHtml(config.gemini_model || "gemini-2.5-flash")} / 제한 ${Number(config.gemini_timeout_seconds || 60)}초`
                    : "Google AI 키를 저장하면 자동 분석이 활성화됩니다.",
            },
            {
                title: "동기화 규칙",
                tone: "slate",
                status: `${Number(config.sync_interval_minutes || 60)}분 주기`,
                detail: `최근 ${Number(config.sync_days || 30)}일 / 배치 ${Number(config.sync_batch_size || 50)}건 / 스캔 ${Number(config.sync_scan_limit || 200)}건`,
            },
        ];

        grid.innerHTML = cards
            .map((card) => {
                const toneClass = card.tone === "emerald"
                    ? "border-emerald-200 bg-emerald-50"
                    : card.tone === "amber"
                        ? "border-amber-200 bg-amber-50"
                        : "border-slate-200 bg-white";
                const statusClass = card.tone === "emerald"
                    ? "text-emerald-700"
                    : card.tone === "amber"
                        ? "text-amber-700"
                        : "text-slate-700";
                return `
                    <div class="rounded-xl border ${toneClass} px-4 py-4 shadow-sm">
                        <div class="flex items-center justify-between gap-3">
                            <span class="text-xs font-bold text-slate-500">${escapeHtml(card.title)}</span>
                            <span class="text-xs font-bold ${statusClass}">${escapeHtml(card.status)}</span>
                        </div>
                        <p class="mt-2 text-sm text-slate-700">${card.detail}</p>
                    </div>
                `;
            })
            .join("");
    }

    function renderSettings(args) {
        getElement("view-settings").classList.add("active");

        const button = getElement("save-settings-btn");
        if (button) {
            button.innerText = "설정 저장";
            button.classList.remove("opacity-50");
        }

        const config = args.config || {};
        if (button) {
            button.innerText = "설정 저장";
            button.classList.remove("opacity-50");
        }
        getElement("set_email").value = config.user_email || "";
        getElement("set_name").value = config.user_display_name || "";
        getElement("set_dept").value = config.user_department || "";
        getElement("set_title").value = config.user_job_title || "";
        getElement("set_mailbox").value = config.mailbox || "INBOX";
        getElement("set_days").value = config.sync_days ?? 30;
        getElement("set_batch").value = config.sync_batch_size ?? 100;
        getElement("set_scan_limit").value = config.sync_scan_limit ?? 200;
        getElement("set_interval").value = config.sync_interval_minutes ?? 60;
        getElement("set_preview_chars").value = config.preview_max_chars ?? 4000;
        getElement("set_timeout").value = config.gemini_timeout_seconds ?? 60;
        getElement("set_store_raw_body").checked = Boolean(config.store_raw_body);
        getElement("set_ai_provider").value = config.ai_provider || "gemini";
        getElement("set_ai_base_url").value = config.ai_base_url || "";
        getElement("set_model").value = config.gemini_model || "gemini-2.5-flash";

        const storedPassword = Boolean(args.secrets && args.secrets.password);
        const storedApiKey = Boolean(args.secrets && args.secrets.api_key);
        const storedHanlimApiKey = Boolean(args.secrets && args.secrets.hanlim_api_key);
        getElement("set_pwd").placeholder = storedPassword ? "(저장됨 - 변경 시에만 입력)" : "비밀번호 입력";
        getElement("set_api").placeholder = storedApiKey ? "(저장됨 - 변경 시에만 입력)" : "Google AI 키 입력";
        getElement("set_hanlim_api").placeholder = storedHanlimApiKey ? "(저장됨 - 변경 시에만 입력)" : "사내 AI 키 입력";
        handleAiProviderChange();

        renderMailboxOptions(args.mailboxes || []);
        renderSettingsHealthCards(args);
        applyAddressBookProfile(true);
        return;

        getElement("set_email").value = config.user_email || "";
        getElement("set_name").value = config.user_display_name || "";
        getElement("set_dept").value = config.user_department || "";
        getElement("set_title").value = config.user_job_title || "";
        getElement("set_mailbox").value = config.mailbox || "INBOX";
        getElement("set_days").value = config.sync_days ?? 30;
        getElement("set_batch").value = config.sync_batch_size ?? 100;
        getElement("set_scan_limit").value = config.sync_scan_limit ?? 200;
        getElement("set_interval").value = config.sync_interval_minutes ?? 60;
        getElement("set_preview_chars").value = config.preview_max_chars ?? 4000;
        getElement("set_timeout").value = config.gemini_timeout_seconds ?? 60;
        getElement("set_store_raw_body").checked = Boolean(config.store_raw_body);
        getElement("set_model").value = config.gemini_model || "gemini-2.5-flash";

        const hasPassword = Boolean(args.secrets && args.secrets.password);
        const hasApiKey = Boolean(args.secrets && args.secrets.api_key);
        getElement("set_pwd").placeholder = hasPassword ? "(저장됨 - 변경 시에만 입력)" : "비밀번호 입력";
        getElement("set_api").placeholder = hasApiKey ? "(저장됨 - 변경 시에만 입력)" : "API 키 입력";

        renderMailboxOptions(args.mailboxes || []);
        renderSettingsHealthCards(args);
        applyAddressBookProfile(true);
    }

    function renderHelp() {
        getElement("view-help").classList.add("active");
    }


    function renderSchedulerStatus(args) {
        const status = getElement("sched-status");
        if (!status) return;

        if (args.scheduler_started && String(args.scheduler_state) === "1") {
            status.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span> 실행 중';
            status.className = "px-3 py-1.5 bg-emerald-50 text-emerald-600 rounded-lg text-xs font-bold flex items-center gap-1 border border-emerald-200";
        } else {
            status.innerHTML = '<span class="material-symbols-outlined text-[14px]">||</span> 일시중지';
            status.className = "px-3 py-1.5 bg-slate-100 text-slate-600 rounded-lg text-xs font-bold flex items-center gap-1 border";
        }
    }

    function renderAutosend(args) {
        getElement("view-autosend").classList.add("active");

        state.currentMailTemplates = Array.isArray(args.mail_templates) ? args.mail_templates : [];
        state.currentSendRegistrations = Array.isArray(args.send_registrations) ? args.send_registrations : [];

        setAutosendSaveLocked(false);
        renderSchedulerStatus(args);
        renderAutoSendLists(state.currentMailTemplates, state.currentSendRegistrations);

        const selectedTemplateId = String(
            args.selected_mail_template_id || getElement("tpl_template_id")?.value || "",
        );
        const selectedRegistrationId = String(
            args.selected_send_registration_id || getElement("tpl_registration_id")?.value || "",
        );
        let selectionMissing = false;

        if (selectedRegistrationId) {
            const registration = state.currentSendRegistrations.find(
                (entry) => String(entry.id) === String(selectedRegistrationId),
            );
            if (registration) {
                fillTemplateForm(registration, "registration");
                selectionMissing = false;
            }
            if (!registration) {
                selectionMissing = true;
            }
        }

        if (selectedTemplateId) {
            const template = state.currentMailTemplates.find(
                (entry) => String(entry.id) === String(selectedTemplateId),
            );
            if (template) {
                fillTemplateForm(template, "template");
                selectionMissing = false;
            }
            if (!template) {
                selectionMissing = true;
            }
        }

        if (selectionMissing || !getElement("tpl_name")?.value) {
            resetTemplateForm();
        }

        wireAttachmentInput();
        wireAttachmentDropzone();
        wireAutosendAssist();
        renderAttachmentList();
        renderAutosendAssist();
    }








    function renderAutoSendLists(mailTemplates, registrations) {
        const list = getElement("template-list-container");
        if (!list) return;

        const templateCards =
            mailTemplates.length > 0
                ? mailTemplates
                    .map(
                        (template) => `
                        <div class="p-3 border rounded-lg hover:border-primary cursor-pointer transition flex flex-col gap-1 bg-white" onclick="window.loadMailTemplate(${template.id})">
                            <div class="flex justify-between items-start gap-2">
                                <span class="font-bold text-sm truncate flex-1">${escapeHtml(template.name || "템플릿")}</span>
                                <button type="button" onclick="event.stopPropagation(); window.deleteMailTemplate(${template.id})" class="text-slate-400 hover:text-red-500 text-[10px]">&times;</button>
                            </div>
                            <div class="text-xs text-slate-500 truncate">${escapeHtml(template.subject || "")}</div>
                            <div class="text-[10px] text-slate-400 mt-1">수신 ${Number(template.recipients || 0)}명</div>
                            <div class="text-[10px] text-slate-400">${escapeHtml(formatReadableDate(template.first_send_at || "", { includeTime: true }))}</div>
                        </div>
                    `,
                    )
                    .join("")
                : '<div class="text-slate-400 text-sm py-4 text-center">저장된 템플릿이 없습니다.</div>';

        const registrationCards =
            registrations.length > 0
                ? registrations
                    .map(
                        (registration) => `
                        <div class="p-3 border rounded-lg hover:border-primary cursor-pointer transition flex flex-col gap-1 ${registration.enabled ? "bg-white" : "bg-slate-50 opacity-60"}" onclick="window.editSendRegistration(${registration.id})">
                            <div class="flex justify-between items-start gap-2">
                                <span class="font-bold text-sm truncate flex-1">${escapeHtml(registration.name || "발송등록")}</span>
                                <button type="button" onclick="event.stopPropagation(); window.deleteSendRegistration(${registration.id})" class="text-slate-400 hover:text-red-500 text-[10px]">&times;</button>
                            </div>
                            <div class="text-xs text-slate-500 truncate">${escapeHtml(registration.subject || "")}</div>
                            <div class="text-[10px] text-slate-400 mt-1">수신 ${Number(registration.recipients || 0)}명</div>
                            <div class="text-[10px] text-slate-500 mt-1">다음 실행 ${escapeHtml(formatReadableDate(registration.next_run || "", { includeTime: true }))}</div>
                        </div>
                    `,
                    )
                    .join("")
                : '<div class="text-slate-400 text-sm py-4 text-center">등록된 발송 항목이 없습니다.</div>';

        list.innerHTML = `
            <section class="space-y-2">
                <div class="flex items-center">
                    <h5 class="text-sm font-bold text-slate-800">템플릿 목록</h5>
                </div>
                <div class="space-y-2">${templateCards}</div>
            </section>
            <section class="space-y-2 mt-5 pt-5 border-t border-slate-200">
                <div class="flex items-center">
                    <h5 class="text-sm font-bold text-slate-800">발송등록 목록</h5>
                </div>
                <div class="space-y-2">${registrationCards}</div>
            </section>
        `;
    }

    function renderLogs(args) {
        getElement("view-logs").classList.add("active");

        const sendLogs = Array.isArray(args.send_logs) ? args.send_logs : [];
        const history = getElement("logs-history-container");
        if (history) {
            if (sendLogs.length === 0) {
                history.innerHTML = '<div class="text-sm text-slate-400 py-4 text-center">기록이 없습니다.</div>';
            } else {
                history.innerHTML = sendLogs
                    .map(
                        (log) => `
                        <div class="p-3 bg-slate-50 border rounded-lg text-sm">
                            <div class="flex justify-between font-bold gap-3">
                                <span class="truncate">${escapeHtml(log.subject || "-")}</span>
                                <span class="${log.result === "success" ? "text-emerald-600" : "text-red-600"} text-xs">${escapeHtml(String(log.result || "").toUpperCase())}</span>
                            </div>
                            <div class="text-[11px] text-slate-500 mt-1">${escapeHtml(formatReadableDate(log.sent_at || "", { includeTime: true }))} / 수신: ${Number(log.recipients || 0)}명</div>
                            ${log.error ? `<div class="mt-2 text-xs text-red-600 bg-red-50 p-2 rounded break-all">${escapeHtml(log.error)}</div>` : ""}
                        </div>
                    `,
                    )
                    .join("");
            }
        }

        const runtime = getElement("logs-runtime-area");
        if (runtime) {
            runtime.value = args.app_logs || "로그 파일이 비어 있습니다.";
            runtime.scrollTop = runtime.scrollHeight;
        }
    }

    function renderDashboardSyncStatusCardsLegacy(status) {
        const grid = getElement("dashboard-sync-status-grid");
        if (!grid) return;

        const safeStatus = status && typeof status === "object" ? status : {};
        const warningText = String(safeStatus.current_warning || "").trim();
        const pendingCount = Number(safeStatus.pending_analysis_count || 0);
        const failedCount = Number(safeStatus.failed_analysis_count || 0);
        const backfillActive = Boolean(safeStatus.backfill_active);
        const schedulerState = String(safeStatus.scheduler_state || "");
        const schedulerLabel = schedulerState === "2"
            ? "일시중지"
            : backfillActive
                ? "초기 백필 진행 중"
                : "증분 동기화 중";

        const cards = [
            {
                title: "마지막 동기화",
                value: safeStatus.last_sync_at
                    ? formatReadableDate(safeStatus.last_sync_at, { includeTime: true })
                    : "아직 없음",
                detail: safeStatus.last_result_summary || "실행 이력이 없으면 앱 시작 10초 뒤 자동 동기화가 예약됩니다.",
                tone: "slate",
            },
            {
                title: "다음 자동 동기화",
                value: safeStatus.next_run_at
                    ? formatReadableDate(safeStatus.next_run_at, { includeTime: true })
                    : "예약 없음",
                detail: `${Number(safeStatus.interval_minutes || 60)}분 주기`,
                tone: "blue",
            },
            {
                title: "동기화 범위",
                value: escapeHtml(String(safeStatus.mailbox || "INBOX")),
                detail: `최근 ${Number(safeStatus.sync_days || 30)}일 기준`,
                tone: "slate",
            },
            {
                title: "현재 상태",
                value: schedulerLabel,
                detail: warningText
                    ? warningText
                    : `분석 대기 ${pendingCount}건 / 분석 실패 ${failedCount}건`,
                tone: warningText ? "amber" : backfillActive ? "blue" : "emerald",
            },
        ];

        grid.innerHTML = cards
            .map((card) => {
                const toneClass = card.tone === "emerald"
                    ? "border-emerald-200 bg-emerald-50"
                    : card.tone === "amber"
                        ? "border-amber-200 bg-amber-50"
                        : card.tone === "blue"
                            ? "border-blue-200 bg-blue-50"
                            : "border-slate-200 bg-white";
                const valueClass = card.tone === "emerald"
                    ? "text-emerald-800"
                    : card.tone === "amber"
                        ? "text-amber-800"
                        : card.tone === "blue"
                            ? "text-blue-800"
                            : "text-slate-800";
                return `
                    <div class="rounded-xl border ${toneClass} px-4 py-4 shadow-sm">
                        <div class="text-xs font-bold text-slate-500">${escapeHtml(card.title)}</div>
                        <div class="mt-2 text-base font-bold ${valueClass}">${card.value}</div>
                        <div class="mt-2 text-xs leading-5 text-slate-600">${escapeHtml(card.detail)}</div>
                    </div>
                `;
            })
            .join("");
    }

    function renderDashboardSyncStatusStrip(status) {
        const grid = getElement("dashboard-sync-status-grid");
        if (!grid) return;

        const safeStatus = status && typeof status === "object" ? status : {};
        const syncProgress = state.syncProgress && typeof state.syncProgress === "object" ? state.syncProgress : {};
        const running = Boolean(syncProgress.running);
        const warningText = String(safeStatus.current_warning || "").trim();
        const lastResultSummary = String(safeStatus.last_result_summary || "").trim();
        const pendingCount = Number(safeStatus.pending_analysis_count || 0);
        const failedCount = Number(safeStatus.failed_analysis_count || 0);
        const backfillActive = Boolean(safeStatus.backfill_active);
        const schedulerState = String(safeStatus.scheduler_state || "");
        const schedulerLabel = schedulerState === "2"
            ? "일시중지"
            : backfillActive
                ? "백필 진행 중"
                : "자동 동기화";

        const compactText = (value, fallback) => {
            const text = String(value || "").trim();
            if (!text) return fallback;
            return text.length > 44 ? `${text.slice(0, 44)}...` : text;
        };

        const progressDetail = running
            ? `저장 ${Number(syncProgress.saved_count || 0)}건 · 분석 ${Number(syncProgress.analysis_completed || 0)}/${Number(syncProgress.analysis_total || 0)}건`
            : warningText
                ? compactText(warningText, "경고 없음")
                : `대기 ${pendingCount}건 · 실패 ${failedCount}건`;

        const cards = [
            {
                title: "마지막",
                value: safeStatus.last_sync_at
                    ? formatReadableDate(safeStatus.last_sync_at, { includeTime: true })
                    : "기록 없음",
                detail: compactText(lastResultSummary, "최근 실행 결과 없음"),
                tone: "slate",
            },
            {
                title: "다음",
                value: safeStatus.next_run_at
                    ? formatReadableDate(safeStatus.next_run_at, { includeTime: true })
                    : "예약 없음",
                detail: `${Number(safeStatus.interval_minutes || 60)}분 주기`,
                tone: "blue",
            },
            {
                title: "범위",
                value: `최근 ${Number(safeStatus.sync_days || 30)}일`,
                detail: compactText(String(safeStatus.mailbox || "INBOX"), "INBOX"),
                tone: "slate",
            },
            {
                title: running ? "진행" : "상태",
                value: running ? getSyncStageLabel(syncProgress) : schedulerLabel,
                detail: progressDetail,
                tone: running
                    ? "blue"
                    : warningText
                        ? "amber"
                        : backfillActive
                            ? "blue"
                            : "emerald",
            },
        ];

        grid.innerHTML = cards
            .map((card) => `
                <div class="dashboard-sync-pill" data-tone="${escapeHtml(card.tone)}">
                    <span class="dashboard-sync-pill-accent" aria-hidden="true"></span>
                    <div class="dashboard-sync-pill-body">
                        <div class="dashboard-sync-pill-label">${escapeHtml(card.title)}</div>
                        <div class="dashboard-sync-pill-value">${escapeHtml(card.value)}</div>
                        <div class="dashboard-sync-pill-detail" title="${escapeHtml(card.detail)}">${escapeHtml(card.detail)}</div>
                    </div>
                </div>
            `)
            .join("");
    }
    function renderDashboardTaskPanels(tasks, completedTasks) {
        const taskContainer = getElement("task-container");
        if (taskContainer) {
            if (tasks.length === 0) {
                taskContainer.innerHTML = '<div class="dashboard-empty-state">열린 할 일이 없습니다.</div>';
            } else {
                taskContainer.innerHTML = tasks
                    .map((task) => {
                        const dueMarkup = task.due_date
                            ? `<p class="dashboard-task-meta mt-1">기한 ${escapeHtml(formatReadableDate(task.due_date, { includeTime: false }))}</p>`
                            : "";
                        return `
                            <div class="dashboard-task-card ${task.is_urgent ? "is-urgent" : ""}">
                                <div class="dashboard-task-check">
                                    <input type="checkbox" onchange="window.toggleTask(${task.id}, this.checked)" class="w-5 h-5 rounded text-primary" />
                                </div>
                                <div class="dashboard-task-copy">
                                    <div class="dashboard-task-head">
                                        <p class="dashboard-task-title">${escapeHtml(task.action_text || "-")}</p>
                                    </div>
                                    <p class="dashboard-task-meta mt-1">출처 ${escapeHtml(task.source || "-")}</p>
                                    ${dueMarkup}
                                </div>
                            </div>
                        `;
                    })
                    .join("");
            }
        }

        const completedTaskContainer = getElement("completed-task-container");
        if (completedTaskContainer) {
            if (completedTasks.length === 0) {
                completedTaskContainer.innerHTML = '<div class="dashboard-empty-state">완료된 할 일이 없습니다.</div>';
            } else {
                completedTaskContainer.innerHTML = completedTasks
                    .map((task) => {
                        const dueMarkup = task.due_date
                            ? `<p class="dashboard-task-meta mt-1">기한 ${escapeHtml(formatReadableDate(task.due_date, { includeTime: false }))}</p>`
                            : "";
                        const noteMarkup = task.note
                            ? `<p class="dashboard-task-note line-clamp-2">${escapeHtml(task.note)}</p>`
                            : "";
                        return `
                            <div class="dashboard-task-card is-completed">
                                <div class="dashboard-task-check">
                                    <input type="checkbox" checked onchange="window.toggleTask(${task.id}, this.checked)" class="w-5 h-5 rounded text-primary" />
                                </div>
                                <div class="dashboard-task-copy">
                                    <div class="dashboard-task-head">
                                        <p class="dashboard-task-title line-through text-slate-500">${escapeHtml(task.action_text || "-")}</p>
                                    </div>
                                    <p class="dashboard-task-meta mt-1">완료 ${escapeHtml(formatReadableDate(task.completed_at || "", { includeTime: true }))}</p>
                                    <p class="dashboard-task-meta mt-1">출처 ${escapeHtml(task.source || "-")}</p>
                                    ${dueMarkup}
                                    ${noteMarkup}
                                </div>
                            </div>
                        `;
                    })
                    .join("");
            }
        }

        const openCount = getElement("task-tab-open-count");
        const completedCount = getElement("task-tab-completed-count");
        if (openCount) openCount.innerText = String(tasks.length);
        if (completedCount) completedCount.innerText = String(completedTasks.length);
    }

    const DASHBOARD_THREAD_NOISE_PATTERNS = [
        /^\uc774 \uc2a4\ub808\ub4dc\uc758 \uccab \uba54\uc77c\uc785\ub2c8\ub2e4\.?$/,
        /^\uc0c1\ub300\uac00 \ud68c\uc2e0\ud560 \ub54c\uae4c\uc9c0 \uc2a4\ub808\ub4dc\ub97c \ucd94\uc801\ud569\ub2c8\ub2e4\.?$/,
    ];
    const DASHBOARD_THREAD_META_ONLY_PATTERNS = [
        /^\uae30\ud55c:\s*/,
        /^\ub9c8\uc9c0\ub9c9 \ubc1c\uc2e0 \uc2dc\uac01:\s*/,
    ];
    const DASHBOARD_INLINE_DATETIME_PATTERN = /\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?/g;

    function normalizeDashboardThreadText(value) {
        return String(value || "")
            .replace(/\s+/g, " ")
            .trim();
    }

    function humanizeDashboardThreadDates(value) {
        return String(value || "").replace(DASHBOARD_INLINE_DATETIME_PATTERN, (match) =>
            formatReadableDate(match, { includeTime: /(?:T|\s)\d{2}:\d{2}/.test(match) }),
        );
    }

    function cleanDashboardThreadText(value, subjectText) {
        const text = normalizeDashboardThreadText(humanizeDashboardThreadDates(localizeThreadCopy(value)));
        if (!text) return "";
        if (subjectText && text === normalizeDashboardThreadText(subjectText)) return "";
        if (DASHBOARD_THREAD_NOISE_PATTERNS.some((pattern) => pattern.test(text))) return "";
        if (DASHBOARD_THREAD_META_ONLY_PATTERNS.some((pattern) => pattern.test(text))) return "";
        return text;
    }

    function sameDashboardThreadText(left, right) {
        return normalizeDashboardThreadText(left) === normalizeDashboardThreadText(right);
    }

    function getDashboardThreadActionPreview(thread, subjectText) {
        const actions = Array.isArray(thread.my_actions) ? thread.my_actions : [];
        for (const action of actions) {
            const text = cleanDashboardThreadText(action.text || action.action_text || "", subjectText);
            if (text) return text;
        }
        return "";
    }

    function pickDashboardThreadLead(thread, subjectText, actionPreview) {
        const candidates = [
            thread.current_conclusion,
            thread.latest_summary,
            actionPreview,
            thread.follow_up_detail,
            thread.changed_since_last,
        ];

        for (const candidate of candidates) {
            const text = cleanDashboardThreadText(candidate, subjectText);
            if (text) return text;
        }

        return "";
    }

    function pickDashboardThreadCaption(thread, subjectText, primaryText, actionPreview) {
        if (actionPreview && !sameDashboardThreadText(actionPreview, primaryText)) {
            return {
                label: "\ub2e4\uc74c \uc561\uc158",
                text: actionPreview,
            };
        }

        const candidates = [
            thread.follow_up_detail,
            thread.changed_since_last,
            thread.latest_summary,
            thread.current_conclusion,
        ];

        for (const candidate of candidates) {
            const text = cleanDashboardThreadText(candidate, subjectText);
            if (!text || sameDashboardThreadText(text, primaryText)) continue;
            return { label: "", text };
        }

        return null;
    }


    function getDashboardMailTabOptions() {
        const counts = state.dashboardMailCategoryCounts || {};
        return [
            { key: "category_1", label: "내가해야할일", count: Number(counts.category_1 || 0) },
            { key: "category_2", label: "내가검토할일", count: Number(counts.category_2 || 0) },
            { key: "category_3", label: "단순 참고용", count: Number(counts.category_3 || 0) },
        ];
    }

    function resolveDashboardMailTabWithCounts(tabKey) {
        const options = getDashboardMailTabOptions();
        const normalized = options.some((option) => option.key === String(tabKey || ""))
            ? String(tabKey)
            : "category_1";
        const activeOption = options.find((option) => option.key === normalized);
        if (activeOption && Number(activeOption.count || 0) > 0) {
            return normalized;
        }
        const fallbackOption = options.find((option) => Number(option.count || 0) > 0);
        return fallbackOption ? fallbackOption.key : normalized;
    }

    function getFilteredDashboardMails() {
        return state.classifiedMails.filter((mail) => `category_${Number(mail.final_category || 3)}` === state.dashboardMailTab);
    }

    function getSelectedDashboardMail() {
        const filteredMails = getFilteredDashboardMails();
        const selectedMail = filteredMails.find((mail) => Number(mail.id || 0) === Number(state.selectedMailId || 0));
        return selectedMail || filteredMails[0] || null;
    }

    function renderDashboardPageCopy() {
        const title = getElement("dashboard-view-title");
        const subtitle = getElement("dashboard-view-subtitle");
        const listTitle = getElement("priority-list-title");
        const section = state.dashboardSection || DEFAULT_DASHBOARD_SECTIONS.dashboard;

        if (title) title.innerText = section.hero_title || section.page_title || "메일 분류";
        if (subtitle) subtitle.innerText = section.hero_subtitle || "";
        if (listTitle) listTitle.innerText = section.list_title || "메일 분류 목록";
    }

    function buildDashboardMailActionButtonsMarkup(mail, options) {
        const settings = options || {};
        const allowArchive = Boolean(settings.allowArchive);
        const allowComplete = Boolean(settings.allowComplete);
        const allowRestore = Boolean(settings.allowRestore);
        const mailId = Number(mail && mail.id ? mail.id : 0);
        if (!mailId || (!allowArchive && !allowComplete && !allowRestore)) {
            return "";
        }

        const buttons = [];
        if (allowRestore) {
            buttons.push(`
                <button
                    type="button"
                    class="dashboard-mail-action-btn is-restore"
                    onclick="window.handleDashboardMailCollectionAction(event, 'restore', ${mailId})">
                    복구
                </button>
            `);
        }
        if (allowArchive) {
            buttons.push(`
                <button
                    type="button"
                    class="dashboard-mail-action-btn is-archive"
                    onclick="window.handleDashboardMailCollectionAction(event, 'archive', ${mailId})">
                    보관
                </button>
            `);
        }
        if (allowComplete) {
            buttons.push(`
                <button
                    type="button"
                    class="dashboard-mail-action-btn is-complete"
                    onclick="window.handleDashboardMailCollectionAction(event, 'complete', ${mailId})">
                    완료
                </button>
            `);
        }
        return buttons.join("");
    }

    function renderDashboardThreadPane() {
        const paginationShell = getElement("priority-pagination-shell");
        if (paginationShell) {
            paginationShell.classList.add("hidden");
        }
    }

    function renderDashboardListDetailMode() {
        const listSection = getElement("priority-thread-list-section");
        const detailSection = getElement("priority-thread-detail-section");
        const isDetail = state.dashboardMailView === "detail";

        if (listSection) {
            listSection.classList.toggle("hidden", isDetail);
        }
        if (detailSection) {
            detailSection.classList.toggle("hidden", !isDetail);
        }
    }

    function renderDashboardFilterBar() {
        const container = getElement("priority-filter-container");
        const shell = getElement("priority-filter-shell");
        if (!container) return;

        container.innerHTML = getDashboardMailTabOptions()
            .map((tab) => {
                const isActive = state.dashboardMailTab === tab.key;
                const buttonClass = isActive ? "dashboard-filter-chip is-active" : "dashboard-filter-chip";
                return `
                    <button type="button" onclick="window.setDashboardMailTab('${tab.key}')" class="${buttonClass}" data-category="${escapeHtml(tab.key)}">
                        <span>${escapeHtml(tab.label)}</span>
                        <span class="dashboard-filter-count">${Number(tab.count || 0)}</span>
                    </button>
                `;
            })
            .join("");

        if (shell) {
            shell.classList.remove("hidden");
        }
    }

    function buildDashboardMailListMeta(mail) {
        const meta = [];
        if (mail.sender) {
            meta.push(escapeHtml(mail.sender));
        }
        if (mail.received_at) {
            meta.push(escapeHtml(formatReadableDate(mail.received_at, { includeTime: true })));
        }
        if (mail.recipient_role && mail.recipient_role !== "NONE") {
            meta.push(escapeHtml(localizeDashboardRecipientRole(mail.recipient_role)));
        }
        return meta.join(' <span class="dashboard-thread-separator">&middot;</span> ');
    }

    function localizeDashboardRecipientRole(value) {
        const normalized = String(value || "").trim().toUpperCase();
        if (normalized === "TO") return "직접 수신";
        if (normalized === "CC") return "참조 수신";
        if (!normalized || normalized === "NONE") return "";
        return `수신 ${normalized}`;
    }

    function getDashboardMailCategoryClass(mail) {
        const category = Number(mail && mail.final_category ? mail.final_category : 3);
        if (category === 1) return "category-1";
        if (category === 2) return "category-2";
        return "category-3";
    }

    function buildDashboardSummaryLines(mail) {
        const lines = [];
        const seen = new Set();

        function appendLine(value) {
            const text = String(value || "").replace(/\s+/g, " ").trim();
            if (!text) return;
            const key = text.toLowerCase();
            if (seen.has(key)) return;
            seen.add(key);
            lines.push(text);
        }

        appendLine(mail.summary);
        if (Array.isArray(mail.summary_long)) {
            mail.summary_long.forEach((item) => appendLine(item));
        }

        if (!lines.length) {
            String(mail.preview || "")
                .split(/\r?\n+/)
                .map((item) => item.trim())
                .filter(Boolean)
                .slice(0, 4)
                .forEach((item) => appendLine(item));
        }

        return lines.slice(0, 5);
    }

    function renderDashboardThreadList() {
        const container = getElement("priority-thread-list");
        const countText = getElement("priority-count-text");
        if (!container) return;

        const mails = getFilteredDashboardMails();
        const section = state.dashboardSection || DEFAULT_DASHBOARD_SECTIONS.dashboard;
        if (countText) {
            countText.innerText = `${mails.length}개 메일`;
        }

        if (mails.length === 0) {
            container.innerHTML = `<div class="dashboard-empty-state">${escapeHtml(section.empty_list_message || "현재 탭에 표시할 메일이 없습니다.")}</div>`;
            return;
        }

        container.innerHTML = mails
            .map((mail) => {
                const isSelected = Number(mail.id || 0) === Number(state.selectedMailId || 0) && state.dashboardMailView === "detail";
                const categoryClass = getDashboardMailCategoryClass(mail);
                const buttonClass = isSelected
                    ? `dashboard-mail-card ${categoryClass} is-selected`
                    : `dashboard-mail-card ${categoryClass}`;
                const actionButtons = buildDashboardMailActionButtonsMarkup(mail, {
                    allowArchive: section.allow_archive_action,
                    allowComplete: section.allow_complete_action,
                    allowRestore: section.allow_restore_action,
                });
                return `
                    <div
                        class="${buttonClass}"
                        role="button"
                        tabindex="0"
                        onclick="window.selectDashboardMail(${Number(mail.id || 0)})"
                        onkeydown="window.handleDashboardMailCardKey(event, ${Number(mail.id || 0)})">
                        <div class="dashboard-mail-card-main">
                            <div class="dashboard-mail-card-copy">
                                <div class="dashboard-mail-meta">${buildDashboardMailListMeta(mail)}</div>
                                <div class="dashboard-mail-subject">${escapeHtml(mail.subject || "(제목 없음)")}</div>
                                <p class="dashboard-mail-summary">${escapeHtml(mail.summary || mail.preview || "-")}</p>
                            </div>
                            ${actionButtons ? `<div class="dashboard-mail-card-actions">${actionButtons}</div>` : ""}
                        </div>
                    </div>
                `;
            })
            .join("");
    }

    function handleDashboardMailCardKey(event, mailId) {
        if (!event) return;
        if (event.key !== "Enter" && event.key !== " ") {
            return;
        }
        event.preventDefault();
        selectDashboardMail(mailId);
    }

    function renderDashboardMailDetailHeader(mail) {
        const container = getElement("priority-thread-detail-toolbar");
        if (!container) return;
        const section = state.dashboardSection || DEFAULT_DASHBOARD_SECTIONS.dashboard;

        if (!mail) {
            container.innerHTML = `
                <button type="button" class="dashboard-mail-back-button" onclick="window.showDashboardMailList()">
                    <span aria-hidden="true">←</span>
                    <span>뒤로가기</span>
                </button>
            `;
            return;
        }

        const actionButtons = buildDashboardMailActionButtonsMarkup(mail, {
            allowArchive: section.allow_archive_action,
            allowComplete: section.allow_complete_action,
            allowRestore: section.allow_restore_action,
        });
        container.innerHTML = `
            <div class="dashboard-mail-detail-toolbar-main">
                <div class="dashboard-mail-detail-toolbar-leading">
                    <button type="button" class="dashboard-mail-back-button" onclick="window.showDashboardMailList()">
                        <span aria-hidden="true">←</span>
                        <span>뒤로가기</span>
                    </button>
                    <div class="dashboard-mail-detail-toolbar-copy">
                        <div class="dashboard-mail-detail-toolbar-kicker">${escapeHtml(section.detail_kicker || "Mail Summary")}</div>
                        <div class="dashboard-mail-detail-toolbar-title">${escapeHtml(section.detail_title || "선택한 메일의 핵심 내용만 빠르게 확인합니다.")}</div>
                    </div>
                </div>
                ${actionButtons ? `<div class="dashboard-mail-detail-toolbar-actions">${actionButtons}</div>` : ""}
            </div>
        `;
    }

    function renderDashboardMailSummary() {
        const container = getElement("dashboard-mail-summary-container");
        if (!container) return;

        const mail = getSelectedDashboardMail();
        renderDashboardMailDetailHeader(mail);
        if (!mail) {
            container.innerHTML = '<div class="dashboard-empty-state">요약할 메일이 없습니다.</div>';
            return;
        }

        const attachments = Array.isArray(mail.attachments) ? mail.attachments.filter(Boolean) : [];
        const summaryLines = buildDashboardSummaryLines(mail);
        const detailLead = summaryLines[0] || mail.summary || mail.preview || "-";
        const detailCategoryClass = getDashboardMailCategoryClass(mail);
        const metaBits = [];
        if (mail.sender || mail.sender_email) {
            metaBits.push(`보낸 사람 ${escapeHtml(mail.sender || mail.sender_email || "-")}`);
        }
        if (mail.received_at) {
            metaBits.push(escapeHtml(formatReadableDate(mail.received_at || "", { includeTime: true })));
        }
        if (mail.recipient_role && mail.recipient_role !== "NONE") {
            metaBits.push(escapeHtml(localizeDashboardRecipientRole(mail.recipient_role)));
        }
        const dueMarkup = mail.due_date
            ? `<div class="dashboard-mail-detail-due">기한 ${escapeHtml(formatReadableDate(mail.due_date, { includeTime: true }))}</div>`
            : "";

        container.innerHTML = `
            <div class="dashboard-mail-detail-shell ${detailCategoryClass}">
                <div class="dashboard-mail-detail-hero">
                    <h5 class="dashboard-mail-detail-title">${escapeHtml(mail.subject || "(제목 없음)")}</h5>
                    <div class="dashboard-mail-detail-meta">
                        ${metaBits.map((item) => `<span>${item}</span>`).join("")}
                    </div>
                    ${dueMarkup}
                </div>

                <div class="dashboard-mail-detail-card">
                    <div class="dashboard-mail-detail-card-title">한 줄 요약</div>
                    <p class="dashboard-mail-detail-body">${escapeHtml(detailLead)}</p>
                </div>

                ${summaryLines.length > 1 ? `
                    <div class="dashboard-mail-detail-card">
                        <div class="dashboard-mail-detail-card-title">핵심 내용</div>
                        <div class="dashboard-mail-detail-list">
                            ${summaryLines.slice(1).map((item, index) => `
                                <div class="dashboard-mail-detail-item">
                                    <span class="dashboard-mail-detail-item-index">${index + 1}</span>
                                    <p class="dashboard-mail-detail-item-text">${escapeHtml(item)}</p>
                                </div>
                            `).join("")}
                        </div>
                    </div>
                ` : ""}

                ${attachments.length > 0 ? `
                    <div class="dashboard-mail-detail-card">
                        <div class="dashboard-mail-detail-card-title">첨부파일</div>
                        <div class="dashboard-mail-detail-attachments">
                            ${attachments.map((item) => `<span class="dashboard-mail-detail-attachment">${escapeHtml(item)}</span>`).join("")}
                        </div>
                    </div>
                ` : ""}
            </div>
        `;
    }

    function renderDashboard(args) {
        getElement("view-dashboard").classList.add("active");
        const scrollPositions = captureDashboardScrollPositions();
        const pageId = getDashboardPageId(resolvePageId(args));

        state.syncStatus = args.sync_status && typeof args.sync_status === "object" ? args.sync_status : {};
        state.syncProgress = args.sync_progress && typeof args.sync_progress === "object"
            ? args.sync_progress
            : state.syncProgress;
        state.dashboardSection = resolveDashboardSection(args.dashboard_section, pageId);
        state.classifiedMails = Array.isArray(args.classified_mails) ? args.classified_mails : [];
        state.dashboardMailCategoryCounts = args.dashboard_mail_category_counts && typeof args.dashboard_mail_category_counts === "object"
            ? {
                category_1: Number(args.dashboard_mail_category_counts.category_1 || 0),
                category_2: Number(args.dashboard_mail_category_counts.category_2 || 0),
                category_3: Number(args.dashboard_mail_category_counts.category_3 || 0),
            }
            : { category_1: 0, category_2: 0, category_3: 0 };
        if (typeof args.dashboard_mail_tab === "string" && args.dashboard_mail_tab) {
            state.dashboardMailTab = args.dashboard_mail_tab;
        }
        state.dashboardMailTab = resolveDashboardMailTabWithCounts(state.dashboardMailTab);
        if (typeof args.dashboard_mail_view === "string" && args.dashboard_mail_view) {
            state.dashboardMailView = args.dashboard_mail_view === "detail" ? "detail" : "list";
        }

        const visibleMails = getFilteredDashboardMails();
        const requestedMailId = Number(args.selected_mail_id || 0);
        state.selectedMailId = requestedMailId || Number(state.selectedMailId || 0);
        if (!visibleMails.some((mail) => Number(mail.id || 0) === Number(state.selectedMailId || 0))) {
            state.selectedMailId = visibleMails.length > 0 ? Number(visibleMails[0].id || 0) : 0;
        }
        if (!visibleMails.length) {
            state.dashboardMailView = "list";
        } else if (state.dashboardMailView === "detail" && !state.selectedMailId) {
            state.dashboardMailView = "list";
        }

        renderSyncButton();
        renderDashboardPageCopy();
        renderDashboardSyncStatusStrip(state.syncStatus);
        renderDashboardFilterBar();
        renderDashboardThreadList();
        renderDashboardThreadPane();
        renderDashboardListDetailMode();
        renderDashboardMailSummary();
        restoreDashboardScrollPositions(scrollPositions);
        syncDashboardPolling();
    }

    function handleRender(args) {
        const safeArgs = args || {};
        const pageId = resolvePageId(safeArgs);
        state.currentPage = String(safeArgs.page || state.currentPage || PAGE_LABEL_BY_ID[pageId] || "");
        state.clientStateVersion = Math.max(
            Number(state.clientStateVersion || 0),
            Number(safeArgs.client_state_version || 0),
        );
        state.syncProgress = safeArgs.sync_progress && typeof safeArgs.sync_progress === "object"
            ? safeArgs.sync_progress
            : state.syncProgress;
        syncResponsiveSidebar();

        state.addressBookContacts = Array.isArray(safeArgs.address_book_contacts)
            ? safeArgs.address_book_contacts
            : [];

        wireRecipientAutocomplete("tpl_to");
        wireRecipientAutocomplete("tpl_cc");
        wireAttachmentInput();
        wireAttachmentDropzone();

        document.querySelectorAll(".view-section").forEach((element) => element.classList.remove("active"));
        document.querySelectorAll(".nav-item").forEach((element) => {
            element.classList.remove("bg-primary/10", "text-primary");
            element.classList.add("text-slate-600");
            element.removeAttribute("aria-current");
            if (String(element.getAttribute("data-page")) === state.currentPage) {
                element.classList.remove("text-slate-600");
                element.classList.add("bg-primary/10", "text-primary");
                element.setAttribute("aria-current", "page");
            }
        });

        const header = getElement("header-title");
        if (header) header.innerText = HEADER_TITLES[pageId] || "MailAI";

        renderUser(safeArgs.user || {});
        renderThemeToggle();
        showFlash(safeArgs.flash_msg);

        if (DASHBOARD_PAGE_IDS.includes(pageId)) {
            renderDashboard(safeArgs);
            return;
        }

        renderSyncButton();
        syncDashboardPolling();

        if (pageId === "settings") {
            renderSettings(safeArgs);
            return;
        }

        if (pageId === "help") {
            renderHelp();
            return;
        }

        if (pageId === "logs") {
            renderLogs(safeArgs);
            return;
        }

        if (pageId === "autosend") {
            renderAutosend(safeArgs);
        }
    }

    window.toggleSidebar = toggleSidebar;
    window.setThemeMode = setThemeMode;
    window.syncMail = syncMail;
    window.navigate = navigate;
    window.setDashboardMailTab = setDashboardMailTab;
    window.selectDashboardMail = selectDashboardMail;
    window.handleDashboardMailCollectionAction = handleDashboardMailCollectionAction;
    window.showDashboardMailList = showDashboardMailList;
    window.handleDashboardMailCardKey = handleDashboardMailCardKey;
    window.clearSecret = clearSecret;
    window.handleAiProviderChange = handleAiProviderChange;
    window.testMailbox = testMailbox;
    window.saveSettings = saveSettings;
    window.schedCmd = schedCmd;
    window.selectRecipientSuggestion = selectRecipientSuggestion;
    window.loadMailTemplate = loadMailTemplate;
    window.editSendRegistration = editSendRegistration;
    window.newMailTemplate = newMailTemplate;
    window.newSendRegistration = newSendRegistration;
    window.newTemplate = newMailTemplate;
    window.editTemplate = editSendRegistration;
    window.saveMailTemplate = saveMailTemplate;
    window.saveSendRegistration = saveSendRegistration;
    window.testSendRegistration = testSendRegistration;
    window.saveTemplate = saveSendRegistration;
    window.deleteMailTemplate = deleteMailTemplate;
    window.deleteSendRegistration = deleteSendRegistration;
    window.delTemplate = deleteSendRegistration;
    window.applyAddressBookProfile = applyAddressBookProfile;
    window.pickAttachmentFiles = pickAttachmentFiles;
    window.clearAttachmentFiles = clearAttachmentFiles;

    window.addEventListener("message", (event) => {
        if (!event.data || event.data.type !== "streamlit:render") return;
        handleRender(event.data.args || {});
    });

    window.addEventListener("pywebviewready", () => {
        UiBridge.bootstrap();
    });

    window.addEventListener("resize", syncResponsiveSidebar);
    window.addEventListener("beforeunload", stopSyncPolling);

    document.addEventListener("click", (event) => {
        const toPanel = getSuggestionPanel("tpl_to");
        const ccPanel = getSuggestionPanel("tpl_cc");
        if (toPanel && !toPanel.contains(event.target) && event.target !== getRecipientInput("tpl_to")) {
            hideRecipientSuggestions("tpl_to");
        }
        if (ccPanel && !ccPanel.contains(event.target) && event.target !== getRecipientInput("tpl_cc")) {
            hideRecipientSuggestions("tpl_cc");
        }
    });

    document.addEventListener(
        "wheel",
        (event) => {
            if (event.ctrlKey) {
                event.preventDefault();
            }
        },
        { passive: false },
    );

    document.addEventListener("keydown", (event) => {
        if (!event.ctrlKey) {
            return;
        }
        if (["+", "-", "=", "0"].includes(event.key)) {
            event.preventDefault();
        }
    });

    UiBridge.setComponentReady();
    applyThemeMode(resolveStoredThemeMode(), { persist: false });
    syncResponsiveSidebar();
})();

