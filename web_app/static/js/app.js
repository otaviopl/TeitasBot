/* app.js — Chat interface logic with sidebar conversations */
(function () {
    'use strict';

    // ---- Auth guard ----
    const token = localStorage.getItem('pa_token');
    if (!token) {
        window.location.href = '/';
        return;
    }

    // ---- DOM refs ----
    const messagesEl = document.getElementById('chat-messages');
    const typingEl = document.getElementById('typing-indicator');
    const inputEl = document.getElementById('chat-input');
    const sendBtn = document.getElementById('btn-send');
    const resetBtn = document.getElementById('btn-reset');
    const logoutBtn = document.getElementById('btn-logout');
    const logoutSidebarBtn = document.getElementById('btn-logout-sidebar');
    const attachBtn = document.getElementById('btn-attach');
    const fileInput = document.getElementById('file-input');
    const filePreview = document.getElementById('file-preview');
    const fileNameEl = document.getElementById('file-name');
    const removeFileBtn = document.getElementById('remove-file');
    const micBtn = document.getElementById('btn-mic');
    const toastEl = document.getElementById('toast');

    // Sidebar refs
    const sidebar = document.getElementById('sidebar');
    const sidebarOverlay = document.getElementById('sidebar-overlay');
    const hamburgerBtn = document.getElementById('btn-hamburger');
    const collapseSidebarBtn = document.getElementById('btn-collapse-sidebar');
    const expandSidebarBtn = document.getElementById('btn-expand-sidebar');
    const appLayout = document.querySelector('.app-layout');
    const newChatBtn = document.getElementById('btn-new-chat');
    const conversationListEl = document.getElementById('conversation-list');

    let pendingFile = null;
    let isSending = false;
    let activeConversationId = localStorage.getItem('pa_active_conversation') || null;

    // Notes refs
    const conversationListSection = document.getElementById('conversation-list');
    const notesListSection = document.getElementById('notes-list-section');
    const notesListEl = document.getElementById('notes-list');
    const newNoteBtn = document.getElementById('btn-new-note');
    const notesEditorEl = document.getElementById('notes-editor');
    const notesEmptyEl = document.getElementById('notes-empty');
    const chatEmptyEl = document.getElementById('chat-empty');
    const noteTitleDisplay = document.getElementById('note-title-display');
    const noteTagsEl = document.getElementById('note-tags');
    const noteSaveStatus = document.getElementById('note-save-status');
    const deleteNoteBtn = document.getElementById('btn-delete-note');
    const chatSection = document.querySelector('.chat-messages');
    const chatInputWrapper = document.querySelector('.chat-input-wrapper');
    const noteViewBodyEl = document.getElementById('note-view-body');
    const noteViewContentEl = document.getElementById('note-view-content');
    const notesEditorBodyEl = document.querySelector('.notes-editor-body');
    const editNoteBtn = document.getElementById('btn-edit-note');
    const doneEditingBtn = document.getElementById('btn-done-editing');

    // Search refs
    const searchNotesBtn = document.getElementById('btn-search-notes');
    const notesSearchEl = document.getElementById('notes-search');
    const notesSearchInput = document.getElementById('notes-search-input');
    const notesSearchClear = document.getElementById('notes-search-clear');
    const notesSearchAutocomplete = document.getElementById('notes-search-autocomplete');

    let activeTab = 'chat';
    let activeNoteId = null;
    let activeNoteData = null;
    let noteIsEditing = false;
    let activeNoteContentDirty = false;
    let easyMDE = null;
    let noteSaveTimer = null;
    let noteMetadataTimer = null;

    // Health refs
    const healthViewEl = document.getElementById('health-view');
    const healthDateLabel = document.getElementById('health-date-label');
    const healthPrevBtn = document.getElementById('health-prev-day');
    const healthNextBtn = document.getElementById('health-next-day');
    const healthLoadingEl = document.getElementById('health-loading');
    const healthContentEl = document.getElementById('health-content');
    const healthCaloriesConsumed = document.getElementById('health-calories-consumed');
    const healthCaloriesBurned = document.getElementById('health-calories-burned');
    const healthBalance = document.getElementById('health-balance');
    const healthProgressFill = document.getElementById('health-progress-fill');
    const healthMealsEl = document.getElementById('health-meals');
    const healthExercisesEl = document.getElementById('health-exercises');
    const healthWeeklyBars = document.getElementById('health-weekly-bars');
    let healthDate = new Date();
    let healthLoading = false;

    // Health goals state (loaded from API)
    var healthGoals = { calorie_goal: 2400, exercise_calorie_goal: 0, exercise_time_goal: 0 };

    // Health goals modal refs
    const healthGoalsOverlay = document.getElementById('health-goals-overlay');
    const healthGoalsForm = document.getElementById('health-goals-form');
    const goalCalorieGoalInput = document.getElementById('goal-calorie-goal');
    const goalExerciseCalorieGoalInput = document.getElementById('goal-exercise-calorie-goal');
    const goalExerciseTimeGoalInput = document.getElementById('goal-exercise-time-goal');

    // Finance refs
    const financeViewEl = document.getElementById('finance-view');
    const financeMonthLabel = document.getElementById('finance-month-label');
    const financePrevBtn = document.getElementById('finance-prev-month');
    const financeNextBtn = document.getElementById('finance-next-month');
    const financeLoadingEl = document.getElementById('finance-loading');
    const financeContentEl = document.getElementById('finance-content');
    const financeTotalExpenses = document.getElementById('finance-total-expenses');
    const financeTotalBills = document.getElementById('finance-total-bills');
    const financePending = document.getElementById('finance-pending');
    const financeProgressFill = document.getElementById('finance-progress-fill');
    const financeBillsEl = document.getElementById('finance-bills');
    const financeExpensesEl = document.getElementById('finance-expenses');
    let financeMonth = new Date();
    let financeLoading = false;

    // Tasks state
    let tasksData = [];
    let tasksMeta = { projects: [], tags: [] };
    let tasksShowDone = false;

    // Sidebar nav refs
    const sidebarNavChat = document.getElementById('sidebar-nav-chat');
    const sidebarNavNotes = document.getElementById('sidebar-nav-notes');
    const sidebarNavHealth = document.getElementById('sidebar-nav-health');
    const sidebarNavFinance = document.getElementById('sidebar-nav-finance');
    const sidebarNavTasks = document.getElementById('sidebar-nav-tasks');
    const tasksViewEl = document.getElementById('tasks-view');
    const sidebarHeaderEl = document.querySelector('.sidebar-header');

    let allUserTags = [];
    let activeTagFilter = null;
    let conversationMessageCount = 0;
    let conversationMessageLimit = 40;

    // ---- Markdown setup ----
    if (typeof marked !== 'undefined') {
        marked.setOptions({
            breaks: true,
            gfm: true,
            headerIds: false,
            mangle: false,
        });
    }

    function renderMarkdown(text) {
        if (typeof marked !== 'undefined') {
            return marked.parse(text);
        }
        return '<p>' + text.replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</p>';
    }

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // ---- API helpers ----
    function authHeaders(extra) {
        return { 'Authorization': 'Bearer ' + token, ...extra };
    }

    async function apiRequest(method, url, body, isFormData) {
        const headers = authHeaders(
            isFormData ? {} : (body !== undefined ? { 'Content-Type': 'application/json' } : {})
        );
        const opts = { method: method, headers: headers };
        if (body !== undefined) {
            opts.body = isFormData ? body : JSON.stringify(body);
        }
        const res = await fetch(url, opts);
        if (res.status === 401) {
            localStorage.removeItem('pa_token');
            localStorage.removeItem('pa_user');
            localStorage.removeItem('pa_active_conversation');
            window.location.href = '/';
            return null;
        }
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || 'Erro no servidor');
        }
        return res.json();
    }

    async function apiPost(url, body, isFormData) {
        return apiRequest('POST', url, body, isFormData);
    }

    async function apiGet(url) {
        return apiRequest('GET', url);
    }

    async function apiDelete(url) {
        return apiRequest('DELETE', url);
    }

    async function apiPatch(url, body) {
        return apiRequest('PATCH', url, body);
    }

    async function apiPut(url, body) {
        return apiRequest('PUT', url, body);
    }

    // ---- UI helpers ----
    var scrollContainer = document.querySelector('.chat-messages');
    function scrollToBottom() {
        requestAnimationFrame(() => {
            scrollContainer.scrollTop = scrollContainer.scrollHeight;
        });
    }

    // iOS keyboard handling via visualViewport API
    (function setupMobileViewport() {
        var appLayout = document.querySelector('.app-layout');
        if (!window.visualViewport) return;

        function onViewportChange() {
            var vv = window.visualViewport;
            appLayout.style.height = vv.height + 'px';
            // Compensate if iOS scrolled the page behind the keyboard
            appLayout.style.top = vv.offsetTop + 'px';
            // Remove 'bottom' so it doesn't conflict with explicit height
            appLayout.style.bottom = 'auto';
            scrollToBottom();
        }

        window.visualViewport.addEventListener('resize', onViewportChange);
        window.visualViewport.addEventListener('scroll', onViewportChange);
    })();

    function showTyping() {
        typingEl.classList.add('visible');
        scrollToBottom();
    }

    function hideTyping() {
        typingEl.classList.remove('visible');
    }

    function showToast(msg, duration) {
        toastEl.textContent = msg;
        toastEl.classList.add('visible');
        setTimeout(() => toastEl.classList.remove('visible'), duration || 3000);
    }

    function clearMessages() {
        var messages = messagesEl.querySelectorAll('.message');
        messages.forEach(function (m) { m.remove(); });
        conversationMessageCount = 0;
        hideConversationLimitNotice();
        updateChatEmptyState();
    }

    function updateChatEmptyState() {
        var hasMessages = messagesEl.querySelectorAll('.message').length > 0;
        chatEmptyEl.classList.toggle('hidden', hasMessages);
        chatSection.classList.toggle('hidden', !hasMessages);
    }

    function formatTimestamp(isoStr) {
        if (!isoStr) return '';
        var d = new Date(isoStr);
        if (isNaN(d.getTime())) return '';
        var now = new Date();
        var timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        if (d.toDateString() === now.toDateString()) return timeStr;
        var yesterday = new Date(now);
        yesterday.setDate(yesterday.getDate() - 1);
        if (d.toDateString() === yesterday.toDateString()) return 'Ontem ' + timeStr;
        return d.toLocaleDateString([], { day: '2-digit', month: '2-digit' }) + ' ' + timeStr;
    }

    function addMessage(role, content, imageUrls, timestamp) {
        var div = document.createElement('div');
        div.className = 'message message-' + role;

        if (role === 'assistant') {
            div.innerHTML = renderMarkdown(content);
        } else {
            div.textContent = content;
        }

        var ts = formatTimestamp(timestamp);
        if (ts) {
            var timeEl = document.createElement('span');
            timeEl.className = 'message-timestamp';
            timeEl.textContent = ts;
            div.appendChild(timeEl);
        }

        messagesEl.insertBefore(div, typingEl);

        if (imageUrls && imageUrls.length) {
            imageUrls.forEach(function (url) {
                var img = document.createElement('img');
                img.src = url;
                img.alt = 'Chart';
                img.loading = 'lazy';
                img.style.cursor = 'pointer';
                img.addEventListener('click', function () {
                    window.open(url, '_blank');
                });
                div.appendChild(img);
            });
        }

        scrollToBottom();
        updateChatEmptyState();
        return div;
    }

    function updateSendButton() {
        var hasText = inputEl.value.trim().length > 0;
        var hasFile = pendingFile !== null;
        sendBtn.disabled = (!hasText && !hasFile) || isSending;
    }

    // ================================================
    // Sidebar — Conversation management
    // ================================================

    function openSidebar() {
        sidebar.classList.add('open');
        sidebarOverlay.classList.add('visible');
    }

    function closeSidebar() {
        sidebar.classList.remove('open');
        sidebarOverlay.classList.remove('visible');
    }

    hamburgerBtn.addEventListener('click', function () {
        if (sidebar.classList.contains('open')) {
            closeSidebar();
        } else {
            openSidebar();
        }
    });

    sidebarOverlay.addEventListener('click', closeSidebar);

    // ================================================
    // Sidebar collapse — Desktop only
    // ================================================

    function collapseSidebar() {
        appLayout.classList.add('sidebar-collapsed');
        localStorage.setItem('pa_sidebar_collapsed', '1');
    }

    function expandSidebar() {
        appLayout.classList.remove('sidebar-collapsed');
        localStorage.removeItem('pa_sidebar_collapsed');
    }

    collapseSidebarBtn.addEventListener('click', collapseSidebar);
    expandSidebarBtn.addEventListener('click', expandSidebar);

    if (localStorage.getItem('pa_sidebar_collapsed') === '1') {
        appLayout.classList.add('sidebar-collapsed');
    }

    async function loadConversations() {
        try {
            var data = await apiGet('/api/conversations');
            if (!data) return;
            renderConversationList(data.conversations);

            if (data.conversations.length === 0) {
                await createConversation();
            } else if (!activeConversationId) {
                // No saved conversation — show empty state, don't auto-select
                updateChatEmptyState();
            } else if (!data.conversations.find(function (c) { return c.id === activeConversationId; })) {
                // Saved conversation no longer exists — clear it and show empty state
                activeConversationId = null;
                localStorage.removeItem('pa_active_conversation');
                updateChatEmptyState();
            } else {
                highlightActiveConversation();
                await loadConversationMessages(activeConversationId);
            }
        } catch (err) {
            showToast('Erro ao carregar conversas');
        }
    }

    function renderConversationList(conversations) {
        conversationListEl.innerHTML = '';
        conversations.forEach(function (conv) {
            var item = document.createElement('div');
            item.className = 'conversation-item' + (conv.id === activeConversationId ? ' active' : '');
            item.dataset.id = conv.id;

            var title = document.createElement('span');
            title.className = 'conversation-item-title';
            title.textContent = conv.title;

            var deleteBtn = document.createElement('button');
            deleteBtn.className = 'conversation-item-delete';
            deleteBtn.textContent = '✕';
            deleteBtn.title = 'Excluir';
            deleteBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                deleteConversation(conv.id);
            });

            item.appendChild(title);
            item.appendChild(deleteBtn);

            item.addEventListener('click', function () {
                switchConversation(conv.id);
                closeSidebar();
            });

            conversationListEl.appendChild(item);
        });
    }

    function highlightActiveConversation() {
        var items = conversationListEl.querySelectorAll('.conversation-item');
        items.forEach(function (item) {
            if (item.dataset.id === activeConversationId) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });
    }

    async function createConversation() {
        try {
            var data = await apiPost('/api/conversations', { title: 'Nova conversa' });
            if (!data) return;
            activeConversationId = data.id;
            localStorage.setItem('pa_active_conversation', data.id);
            clearMessages();
            await loadConversations();
            inputEl.focus();
        } catch (err) {
            showToast('Erro ao criar conversa');
        }
    }

    async function switchConversation(conversationId) {
        // Auto-switch to chat tab if not already there
        if (activeTab !== 'chat') switchTab('chat');

        if (conversationId === activeConversationId) return;
        activeConversationId = conversationId;
        localStorage.setItem('pa_active_conversation', conversationId);
        highlightActiveConversation();
        clearMessages();
        await loadConversationMessages(conversationId);
        inputEl.focus();
    }

    async function loadConversationMessages(conversationId) {
        try {
            var data = await apiGet('/api/conversations/' + conversationId + '/messages');
            if (!data) return;
            conversationMessageCount = data.message_count || data.messages.length;
            conversationMessageLimit = data.message_limit || 40;
            data.messages.forEach(function (msg) {
                addMessage(msg.role === 'user' ? 'user' : 'assistant', msg.content, null, msg.created_at);
            });
            checkMessageLimit();
        } catch (_) {
            // Conversation may be empty
        }
    }

    function checkMessageLimit() {
        if (conversationMessageCount >= conversationMessageLimit && activeConversationId) {
            showConversationLimitNotice();
        } else {
            hideConversationLimitNotice();
        }
    }

    function showConversationLimitNotice() {
        var existing = document.getElementById('conv-limit-notice');
        if (!existing) {
            var notice = document.createElement('div');
            notice.id = 'conv-limit-notice';
            notice.className = 'conv-limit-notice';
            notice.textContent = 'Esta conversa atingiu o limite de mensagens. Crie uma nova conversa para continuar.';
            chatInputWrapper.parentNode.insertBefore(notice, chatInputWrapper);
        }
        inputEl.disabled = true;
        sendBtn.disabled = true;
    }

    function hideConversationLimitNotice() {
        var existing = document.getElementById('conv-limit-notice');
        if (existing) existing.remove();
        inputEl.disabled = false;
        updateSendButton();
    }

    async function deleteConversation(conversationId) {
        try {
            await apiDelete('/api/conversations/' + conversationId);
            if (conversationId === activeConversationId) {
                activeConversationId = null;
                localStorage.removeItem('pa_active_conversation');
                clearMessages();
            }
            await loadConversations();
        } catch (err) {
            showToast('Erro ao excluir conversa');
        }
    }

    async function autoTitleConversation(conversationId, userMessage) {
        var title = userMessage.substring(0, 40).trim();
        if (userMessage.length > 40) title += '…';
        try {
            await apiPatch('/api/conversations/' + conversationId, { title: title });
            // Update sidebar
            var item = conversationListEl.querySelector('[data-id="' + conversationId + '"] .conversation-item-title');
            if (item) item.textContent = title;
        } catch (_) {
            // Non-critical
        }
    }

    newChatBtn.addEventListener('click', function () {
        createConversation();
        closeSidebar();
    });

    // ---- Textarea auto-resize ----
    inputEl.addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
        updateSendButton();
    });

    inputEl.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (!sendBtn.disabled) {
                sendMessage();
            }
        }
    });

    // ---- Send text message ----
    async function sendMessage() {
        var text = inputEl.value.trim();
        var file = pendingFile;

        if (!text && !file) return;
        if (isSending) return;

        isSending = true;
        updateSendButton();

        if (file) {
            addMessage('user', text || '📎 ' + file.name, null, new Date().toISOString());
            clearFilePreview();
        } else {
            addMessage('user', text, null, new Date().toISOString());
        }

        inputEl.value = '';
        inputEl.style.height = 'auto';
        showTyping();

        // Check if this is the first message in the conversation (auto-title)
        var isFirstMessage = messagesEl.querySelectorAll('.message').length === 1;

        try {
            var data;
            if (file) {
                var formData = new FormData();
                formData.append('file', file);
                formData.append('caption', text);
                if (activeConversationId) formData.append('conversation_id', activeConversationId);
                data = await apiPost('/api/chat/upload', formData, true);
            } else {
                data = await apiPost('/api/chat', {
                    message: text,
                    conversation_id: activeConversationId
                });
            }

            if (data) {
                addMessage('assistant', data.text, data.image_urls, new Date().toISOString());
                conversationMessageCount += 2;
                checkMessageLimit();
            }

            if (isFirstMessage && activeConversationId && text) {
                autoTitleConversation(activeConversationId, text);
            }
        } catch (err) {
            showToast('Erro: ' + err.message);
        } finally {
            hideTyping();
            isSending = false;
            updateSendButton();
            inputEl.focus();
        }
    }

    sendBtn.addEventListener('click', sendMessage);

    // ---- File attachment ----
    attachBtn.addEventListener('click', function () {
        fileInput.click();
    });

    fileInput.addEventListener('change', function () {
        if (this.files && this.files[0]) {
            pendingFile = this.files[0];
            fileNameEl.textContent = pendingFile.name;
            filePreview.classList.add('visible');
            updateSendButton();
        }
    });

    removeFileBtn.addEventListener('click', clearFilePreview);

    function clearFilePreview() {
        pendingFile = null;
        fileInput.value = '';
        filePreview.classList.remove('visible');
        fileNameEl.textContent = '';
        updateSendButton();
    }

    // ---- Audio recording ----
    if (typeof AudioRecorder !== 'undefined') {
        var recorder = new AudioRecorder();

        micBtn.addEventListener('click', async function () {
            if (recorder.isRecording()) {
                micBtn.classList.remove('recording');
                var blob = await recorder.stop();
                if (blob && blob.size > 0) {
                    await sendAudio(blob);
                }
            } else {
                var started = await recorder.start();
                if (started) {
                    micBtn.classList.add('recording');
                } else {
                    showToast('Não foi possível acessar o microfone');
                }
            }
        });
    } else {
        micBtn.style.display = 'none';
    }

    async function sendAudio(blob) {
        if (isSending) return;
        isSending = true;
        updateSendButton();

        addMessage('user', '🎙️ Mensagem de voz', null, new Date().toISOString());
        showTyping();

        var isFirstMessage = messagesEl.querySelectorAll('.message').length === 1;

        try {
            var formData = new FormData();
            formData.append('audio', blob, 'recording.webm');
            if (activeConversationId) formData.append('conversation_id', activeConversationId);
            var data = await apiPost('/api/chat/audio', formData, true);

            if (data) {
                if (data.transcribed_text) {
                    var userMessages = messagesEl.querySelectorAll('.message-user');
                    var lastUserMsg = userMessages[userMessages.length - 1];
                    if (lastUserMsg) {
                        lastUserMsg.textContent = '🎙️ ' + data.transcribed_text;
                    }

                    if (isFirstMessage && activeConversationId) {
                        autoTitleConversation(activeConversationId, data.transcribed_text);
                    }
                }
                addMessage('assistant', data.text, data.image_urls, new Date().toISOString());
                conversationMessageCount += 2;
                checkMessageLimit();
            }
        } catch (err) {
            showToast('Erro: ' + err.message);
        } finally {
            hideTyping();
            isSending = false;
            updateSendButton();
        }
    }

    // ---- Reset conversation ----
    resetBtn.addEventListener('click', async function () {
        if (isSending) return;
        try {
            var url = '/api/chat/reset';
            if (activeConversationId) {
                url += '?conversation_id=' + encodeURIComponent(activeConversationId);
            }
            await apiPost(url, {});
            clearMessages();
            showToast('Conversa limpa');
        } catch (err) {
            showToast('Erro ao limpar conversa');
        }
    });

    // ---- Logout ----
    function doLogout() {
        localStorage.removeItem('pa_token');
        localStorage.removeItem('pa_user');
        localStorage.removeItem('pa_active_conversation');
        window.location.href = '/';
    }

    logoutBtn.addEventListener('click', doLogout);
    logoutSidebarBtn.addEventListener('click', doLogout);

    // ---- Google OAuth ----
    var googleBtn = document.getElementById('btn-google-connect');
    var googleLabel = document.getElementById('google-connect-label');

    async function checkGoogleStatus() {
        try {
            var resp = await fetch('/api/google/status', { headers: authHeaders() });
            if (!resp.ok) throw new Error();
            var data = await resp.json();
            if (!data.configured) {
                googleBtn.style.display = 'none';
                return;
            }
            if (data.connected) {
                googleLabel.textContent = 'Google: conectado ✓';
                googleBtn.classList.remove('disconnected');
                googleBtn.classList.add('connected');
                googleBtn.title = 'Clique para desconectar';
            } else {
                googleLabel.textContent = 'Conectar Google';
                googleBtn.classList.remove('connected');
                googleBtn.classList.add('disconnected');
                googleBtn.title = 'Clique para autorizar Google';
            }
        } catch (_) {
            googleBtn.style.display = 'none';
        }
    }

    googleBtn.addEventListener('click', async function () {
        if (googleBtn.classList.contains('connected')) {
            if (!confirm('Desconectar conta Google?')) return;
            try {
                await fetch('/api/google/disconnect', { method: 'DELETE', headers: authHeaders() });
                showToast('Google desconectado');
            } catch (_) {
                showToast('Erro ao desconectar');
            }
            checkGoogleStatus();
            return;
        }
        try {
            var resp = await fetch('/api/google/auth-url', { headers: authHeaders() });
            if (!resp.ok) {
                var err = await resp.json().catch(function () { return {}; });
                showToast(err.detail || 'Erro ao iniciar autenticação Google');
                return;
            }
            var data = await resp.json();
            window.open(data.auth_url, '_blank');
            showToast('Complete a autorização na nova aba');
            setTimeout(checkGoogleStatus, 15000);
        } catch (_) {
            showToast('Erro ao iniciar autenticação Google');
        }
    });

    // Re-check Google status when user comes back to the tab
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) checkGoogleStatus();
    });

    // ---- Memories modal ----
    var memoriesBtn = document.getElementById('btn-memories');
    var memoriesOverlay = document.getElementById('memories-modal-overlay');
    var memoriesContent = document.getElementById('memories-modal-content');
    var memoriesClose = document.getElementById('memories-modal-close');

    function buildMemoryCard(file) {
        var card = document.createElement('div');
        card.className = 'memories-file';
        card.dataset.filename = file.filename;

        // Header
        var header = document.createElement('div');
        header.className = 'memories-file-header';

        var nameSpan = document.createElement('span');
        nameSpan.textContent = '\u{1F4C4} ' + file.display_name;
        header.appendChild(nameSpan);

        var headerRight = document.createElement('div');
        headerRight.className = 'memories-file-header-right';

        var editBtn = document.createElement('button');
        editBtn.className = 'memories-edit-btn';
        editBtn.title = 'Editar memória';
        editBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
        headerRight.appendChild(editBtn);

        var chevron = document.createElement('span');
        chevron.className = 'chevron';
        chevron.textContent = '\u25B6';
        headerRight.appendChild(chevron);

        header.appendChild(headerRight);
        card.appendChild(header);

        // Read-only content
        var contentDiv = document.createElement('div');
        contentDiv.className = 'memories-file-content';
        contentDiv.textContent = file.content;
        card.appendChild(contentDiv);

        // Edit area (hidden by default)
        var editArea = document.createElement('div');
        editArea.className = 'memories-file-edit hidden';

        var textarea = document.createElement('textarea');
        textarea.className = 'memories-edit-textarea';
        textarea.value = file.content;
        editArea.appendChild(textarea);

        var editActions = document.createElement('div');
        editActions.className = 'memories-edit-actions';

        var saveBtn = document.createElement('button');
        saveBtn.className = 'memories-edit-save';
        saveBtn.textContent = 'Salvar';

        var cancelBtn = document.createElement('button');
        cancelBtn.className = 'memories-edit-cancel';
        cancelBtn.textContent = 'Cancelar';

        var expandBtn = document.createElement('button');
        expandBtn.className = 'memories-edit-expand-btn';
        expandBtn.title = 'Expandir editor';
        expandBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg> Expandir';

        editActions.appendChild(saveBtn);
        editActions.appendChild(cancelBtn);
        editActions.appendChild(expandBtn);
        editArea.appendChild(editActions);
        card.appendChild(editArea);

        var isExpanded = false;

        function setExpanded(on) {
            isExpanded = on;
            textarea.classList.toggle('textarea-expanded', on);
            expandBtn.innerHTML = on
                ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="10" y1="14" x2="3" y2="21"/><line x1="21" y1="3" x2="14" y2="10"/></svg> Recolher'
                : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg> Expandir';
            memoriesContent.style.overflowY = on ? 'hidden' : 'auto';
        }

        expandBtn.addEventListener('click', function () { setExpanded(!isExpanded); });

        // Collapse toggle on header (skip clicks on edit button or actions)
        header.addEventListener('click', function (e) {
            if (e.target.closest('.memories-edit-btn')) return;
            if (card.classList.contains('editing')) return;
            card.classList.toggle('expanded');
        });

        function enterEdit() {
            card.classList.add('expanded', 'editing');
            textarea.value = contentDiv.textContent;
            contentDiv.classList.add('hidden');
            editArea.classList.remove('hidden');
            textarea.focus();
        }

        function exitEdit() {
            card.classList.remove('editing');
            setExpanded(false);
            contentDiv.classList.remove('hidden');
            editArea.classList.add('hidden');
        }

        // Enter edit mode
        editBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            enterEdit();
        });

        // Save
        saveBtn.addEventListener('click', async function () {
            var newContent = textarea.value;
            saveBtn.disabled = true;
            saveBtn.textContent = 'Salvando…';
            try {
                var resp = await fetch('/api/memories/' + encodeURIComponent(file.filename), {
                    method: 'PUT',
                    headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
                    body: JSON.stringify({ content: newContent }),
                });
                if (!resp.ok) throw new Error();
                file.content = newContent;
                contentDiv.textContent = newContent;
                exitEdit();
            } catch (_) {
                showToast('Erro ao salvar memória');
            } finally {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Salvar';
            }
        });

        // Cancel
        cancelBtn.addEventListener('click', function () {
            textarea.value = file.content;
            exitEdit();
        });

        return card;
    }

    memoriesBtn.addEventListener('click', async function () {
        memoriesOverlay.classList.add('visible');
        memoriesContent.innerHTML = '<div class="loading-spinner"></div>';
        try {
            var resp = await fetch('/api/memories', { headers: authHeaders() });
            if (!resp.ok) throw new Error();
            var data = await resp.json();
            memoriesContent.innerHTML = '';
            if (data.count === 0) {
                memoriesContent.innerHTML = '<p style="color:var(--color-text-muted);text-align:center;padding:12px 0">Nenhuma memória encontrada</p>';
            } else {
                data.files.forEach(function (file) {
                    memoriesContent.appendChild(buildMemoryCard(file));
                });
            }
        } catch (_) {
            memoriesContent.innerHTML = '<p style="color:#dc2626;text-align:center;padding:12px 0">Erro ao carregar memórias</p>';
        }
    });

    memoriesOverlay.addEventListener('click', function (e) {
        if (e.target === memoriesOverlay) memoriesOverlay.classList.remove('visible');
    });
    memoriesClose.addEventListener('click', function () {
        memoriesOverlay.classList.remove('visible');
    });

    // ================================================
    // Tab switching — Chat / Notes
    // ================================================

    function switchTab(tab) {
        if (tab === activeTab) return;

        // Generate metadata when leaving notes tab
        if (activeTab === 'notes' && activeNoteId && activeNoteContentDirty) {
            flushNoteAndGenerateMetadata();
        }

        activeTab = tab;

        // Update sidebar nav buttons
        sidebarNavChat.classList.toggle('active', tab === 'chat');
        sidebarNavNotes.classList.toggle('active', tab === 'notes');
        sidebarNavHealth.classList.toggle('active', tab === 'health');
        sidebarNavFinance.classList.toggle('active', tab === 'finance');
        sidebarNavTasks.classList.toggle('active', tab === 'tasks');

        // Show/hide sidebar list sections
        sidebarHeaderEl.style.display = (tab === 'chat') ? '' : 'none';
        conversationListSection.style.display = (tab === 'chat') ? '' : 'none';
        notesListSection.style.display = (tab === 'notes') ? '' : 'none';

        // Hide everything first
        chatSection.classList.add('hidden');
        chatEmptyEl.classList.add('hidden');
        chatInputWrapper.classList.add('hidden');
        notesEditorEl.classList.add('hidden');
        notesEmptyEl.classList.add('hidden');
        healthViewEl.classList.add('hidden');
        financeViewEl.classList.add('hidden');
        tasksViewEl.classList.add('hidden');
        resetBtn.style.visibility = 'hidden';
        var limitNotice = document.getElementById('conv-limit-notice');
        if (limitNotice) limitNotice.style.display = 'none';

        if (tab === 'chat') {
            chatEmptyEl.classList.add('hidden');
            chatInputWrapper.classList.remove('hidden');
            resetBtn.style.visibility = '';
            if (limitNotice) limitNotice.style.display = '';
            updateChatEmptyState();
            if (window.matchMedia('(min-width: 768px)').matches) {
                inputEl.focus();
            }
        } else if (tab === 'notes') {
            loadNotes();
        } else if (tab === 'health') {
            healthViewEl.classList.remove('hidden');
            loadHealthGoals().then(loadHealthDashboard);
        } else if (tab === 'finance') {
            financeViewEl.classList.remove('hidden');
            loadFinanceDashboard();
        } else if (tab === 'tasks') {
            tasksViewEl.classList.remove('hidden');
            loadTasks();
        }
    }

    // Sidebar nav click handler
    document.querySelector('.sidebar-nav').addEventListener('click', function (e) {
        var btn = e.target.closest('.sidebar-nav-item');
        if (!btn) return;
        var nav = btn.dataset.nav;
        if (nav) switchTab(nav);
        // On mobile close sidebar after nav selection
        if (window.matchMedia('(max-width: 767px)').matches) {
            sidebar.classList.remove('open');
            sidebarOverlay.classList.remove('visible');
        }
    });

    // ================================================
    // Notes — CRUD & Editor
    // ================================================

    function enterViewMode() {
        noteIsEditing = false;
        notesEditorEl.dataset.mode = 'view';
        var content = (activeNoteData && activeNoteData.content) || '';
        noteViewContentEl.innerHTML = content ? marked.parse(content) : '';
    }

    function enterEditMode() {
        noteIsEditing = true;
        notesEditorEl.dataset.mode = 'edit';
        initEasyMDE();
        var content = (activeNoteData && activeNoteData.content) || '';
        easyMDE.value(content);
        noteSaveStatus.textContent = '';
        activeNoteContentDirty = false;
        setTimeout(function () { easyMDE.codemirror.refresh(); easyMDE.codemirror.focus(); }, 50);
    }

    function initEasyMDE() {
        if (easyMDE) return;
        easyMDE = new EasyMDE({
            element: document.getElementById('note-content'),
            spellChecker: false,
            autofocus: false,
            status: false,
            minHeight: '200px',
            placeholder: 'Escreva sua anotação em Markdown…',
            toolbar: [
                'bold', 'italic', 'heading', '|',
                'quote', 'unordered-list', 'ordered-list', '|',
                'link', 'image', 'code', 'table', '|',
                'preview', '|',
                'guide',
            ],
        });
        easyMDE.codemirror.on('change', function () {
            scheduleNoteSave();
        });
    }

    function scheduleNoteSave() {
        if (!activeNoteId) return;
        activeNoteContentDirty = true;
        noteSaveStatus.textContent = 'Salvando…';
        clearTimeout(noteSaveTimer);
        noteSaveTimer = setTimeout(function () {
            saveCurrentNote();
        }, 2000);
        // Generate metadata after 5s of inactivity
        clearTimeout(noteMetadataTimer);
        noteMetadataTimer = setTimeout(function () {
            if (activeNoteId && activeNoteContentDirty) {
                flushNoteAndGenerateMetadata();
            }
        }, 5000);
    }

    async function saveCurrentNote() {
        if (!activeNoteId || !easyMDE) return;
        var content = easyMDE.value();

        try {
            await apiPatch('/api/notes/' + activeNoteId, { content: content });
            if (activeNoteData && activeNoteData.id === activeNoteId) {
                activeNoteData.content = content;
            }
            noteSaveStatus.textContent = 'Salvo ✓';
        } catch (err) {
            noteSaveStatus.textContent = 'Erro ao salvar';
        }
    }

    async function flushNoteAndGenerateMetadata() {
        if (!activeNoteId || !easyMDE) return;
        var noteId = activeNoteId;
        var content = easyMDE.value();

        clearTimeout(noteSaveTimer);
        clearTimeout(noteMetadataTimer);

        // Always save content first
        try {
            await apiPatch('/api/notes/' + noteId, { content: content });
            if (activeNoteData && activeNoteData.id === noteId) {
                activeNoteData.content = content;
            }
        } catch (_) { /* ignore save error here */ }

        // Skip metadata generation for very short content
        if (content.trim().length < 5) {
            if (activeNoteId === noteId) noteSaveStatus.textContent = 'Salvo ✓';
            activeNoteContentDirty = false;
            return;
        }

        if (activeNoteId === noteId) noteSaveStatus.textContent = 'Gerando título…';

        // Fire metadata generation (don't block UI)
        apiPost('/api/notes/' + noteId + '/generate-metadata', {}).then(function (meta) {
            if (!meta) return;
            // Update sidebar title
            var item = notesListEl.querySelector('.note-item[data-id="' + noteId + '"] .note-item-title');
            if (item) item.textContent = meta.title;
            // Update tags in sidebar
            var tagsContainer = notesListEl.querySelector('.note-item[data-id="' + noteId + '"] .note-item-tags');
            if (tagsContainer) renderSidebarNoteTags(tagsContainer, meta.tags || []);
            // Update header if still viewing the same note
            if (activeNoteId === noteId) {
                noteTitleDisplay.textContent = meta.title;
                renderNoteTags(meta.tags || []);
                noteSaveStatus.textContent = 'Salvo ✓';
                if (activeNoteData && activeNoteData.id === noteId) {
                    activeNoteData.title = meta.title;
                    activeNoteData.tags = meta.tags || [];
                }
            }
            activeNoteContentDirty = false;
            refreshUserTags();
        }).catch(function () {
            if (activeNoteId === noteId) noteSaveStatus.textContent = 'Salvo ✓';
        });
    }

    function renderNoteTags(tags) {
        noteTagsEl.innerHTML = '';
        (tags || []).forEach(function (tag) {
            var pill = document.createElement('span');
            pill.className = 'note-tag-pill';
            pill.textContent = tag;
            noteTagsEl.appendChild(pill);
        });
        // Store current tags for edit mode
        noteTagsEl._currentTags = tags || [];
    }

    // ---- Tag editing ----
    var editTagsBtn = document.getElementById('btn-edit-tags');
    var tagsEditEl = document.getElementById('note-tags-edit');
    var tagsInput = document.getElementById('note-tags-input');
    var tagsSaveBtn = document.getElementById('note-tags-save');
    var tagsCancelBtn = document.getElementById('note-tags-cancel');

    function openTagEditor() {
        var tags = noteTagsEl._currentTags || [];
        tagsInput.value = tags.join(', ');
        noteTagsEl.style.display = 'none';
        editTagsBtn.style.display = 'none';
        tagsEditEl.classList.remove('hidden');
        tagsInput.focus();
    }

    function closeTagEditor() {
        tagsEditEl.classList.add('hidden');
        noteTagsEl.style.display = '';
        editTagsBtn.style.display = '';
    }

    async function saveTagEdits() {
        if (!activeNoteId) return;
        var raw = tagsInput.value;
        var tags = raw.split(',').map(function (t) { return t.trim().toLowerCase(); }).filter(Boolean);
        // Remove duplicates
        tags = tags.filter(function (t, i, arr) { return arr.indexOf(t) === i; });
        closeTagEditor();
        renderNoteTags(tags);
        try {
            await apiPatch('/api/notes/' + activeNoteId, { tags: tags });
            if (activeNoteData && activeNoteData.id === activeNoteId) {
                activeNoteData.tags = tags;
            }
            // Update sidebar tags
            var sidebarItem = notesListEl.querySelector('[data-id="' + activeNoteId + '"]');
            if (sidebarItem) {
                var tagContainer = sidebarItem.querySelector('.note-item-tags');
                if (tagContainer) renderSidebarNoteTags(tagContainer, tags);
            }
            refreshUserTags();
            noteSaveStatus.textContent = 'Tags salvas ✓';
        } catch (_) {
            noteSaveStatus.textContent = 'Erro ao salvar tags';
        }
    }

    editTagsBtn.addEventListener('click', openTagEditor);
    tagsSaveBtn.addEventListener('click', saveTagEdits);
    tagsCancelBtn.addEventListener('click', closeTagEditor);
    tagsInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); saveTagEdits(); }
        if (e.key === 'Escape') { closeTagEditor(); }
    });

    function renderSidebarNoteTags(container, tags) {
        container.innerHTML = '';
        (tags || []).forEach(function (tag) {
            var span = document.createElement('span');
            span.className = 'note-item-tag';
            span.textContent = tag;
            container.appendChild(span);
        });
    }

    async function loadNotes() {
        try {
            var url = '/api/notes';
            if (activeTagFilter) url += '?tag=' + encodeURIComponent(activeTagFilter);
            var data = await apiGet(url);
            renderNotesList(data.notes || []);
        } catch (err) {
            showToast('Erro ao carregar anotações');
        }
    }

    function renderNotesList(notes) {
        notesListEl.innerHTML = '';
        notes.forEach(function (note) {
            var div = document.createElement('div');
            div.className = 'note-item' + (note.id === activeNoteId ? ' active' : '');
            div.dataset.id = note.id;

            var contentDiv = document.createElement('div');
            contentDiv.className = 'note-item-content';

            var titleSpan = document.createElement('span');
            titleSpan.className = 'note-item-title';
            titleSpan.textContent = note.title;
            contentDiv.appendChild(titleSpan);

            var tagsDiv = document.createElement('div');
            tagsDiv.className = 'note-item-tags';
            renderSidebarNoteTags(tagsDiv, note.tags || []);
            contentDiv.appendChild(tagsDiv);

            div.appendChild(contentDiv);

            var delBtn = document.createElement('button');
            delBtn.className = 'note-item-delete';
            delBtn.textContent = '✕';
            delBtn.title = 'Excluir';
            delBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                deleteNote(note.id);
            });
            div.appendChild(delBtn);

            div.addEventListener('click', function () {
                selectNote(note.id);
            });

            notesListEl.appendChild(div);
        });

        if (!activeNoteId) {
            notesEditorEl.classList.add('hidden');
            if (activeTab === 'notes') notesEmptyEl.classList.remove('hidden');
        } else if (activeTab === 'notes') {
            notesEmptyEl.classList.add('hidden');
            notesEditorEl.classList.remove('hidden');
            // Refresh CodeMirror layout only when in edit mode
            if (easyMDE && noteIsEditing) setTimeout(function () { easyMDE.codemirror.refresh(); }, 0);
        }
    }

    async function createNote() {
        // Generate metadata for current note before creating new one
        if (activeNoteId && easyMDE && activeNoteContentDirty) {
            flushNoteAndGenerateMetadata();
        }

        try {
            var data = await apiPost('/api/notes', { title: 'Nova anotação' });
            activeNoteId = data.id;
            activeNoteContentDirty = false;
            await loadNotes();
            await selectNote(data.id, { startEditing: true });
        } catch (err) {
            showToast('Erro ao criar anotação');
        }
    }

    async function selectNote(noteId, options) {
        var startEditing = (options && options.startEditing) || false;

        // Auto-switch to notes tab if not already there
        if (activeTab !== 'notes') switchTab('notes');

        // Save and flush previous note if it was being edited
        if (activeNoteId && activeNoteId !== noteId && noteIsEditing) {
            if (activeNoteContentDirty) flushNoteAndGenerateMetadata();
            else { clearTimeout(noteSaveTimer); await saveCurrentNote(); }
        } else if (activeNoteId && noteIsEditing) {
            clearTimeout(noteSaveTimer);
            await saveCurrentNote();
        }

        activeNoteId = noteId;
        activeNoteData = null;
        noteIsEditing = false;
        activeNoteContentDirty = false;
        clearTimeout(noteMetadataTimer);

        // Update sidebar active state
        notesListEl.querySelectorAll('.note-item').forEach(function (item) {
            item.classList.toggle('active', item.dataset.id === noteId);
        });

        try {
            var note = await apiGet('/api/notes/' + noteId);
            activeNoteData = note;
            notesEmptyEl.classList.add('hidden');
            notesEditorEl.classList.remove('hidden');
            noteTitleDisplay.textContent = note.title;
            renderNoteTags(note.tags || []);
            noteSaveStatus.textContent = '';
            activeNoteContentDirty = false;
            if (startEditing) {
                enterEditMode();
            } else {
                enterViewMode();
            }
            // Close sidebar on mobile after selecting
            closeSidebar();
        } catch (err) {
            showToast('Erro ao abrir anotação');
            activeNoteId = null;
            activeNoteData = null;
        }
    }

    async function deleteNote(noteId) {
        if (!confirm('Excluir esta anotação?')) return;
        try {
            await apiDelete('/api/notes/' + noteId);
            if (activeNoteId === noteId) {
                activeNoteId = null;
                activeNoteData = null;
                noteIsEditing = false;
                activeNoteContentDirty = false;
                notesEditorEl.classList.add('hidden');
                notesEmptyEl.classList.remove('hidden');
                if (easyMDE) easyMDE.value('');
                noteTitleDisplay.textContent = 'Nova anotação';
                noteTagsEl.innerHTML = '';
            }
            await loadNotes();
            refreshUserTags();
        } catch (err) {
            showToast('Erro ao excluir anotação');
        }
    }

    editNoteBtn.addEventListener('click', function () {
        if (activeNoteId) enterEditMode();
    });

    doneEditingBtn.addEventListener('click', async function () {
        if (!activeNoteId) return;
        clearTimeout(noteSaveTimer);
        clearTimeout(noteMetadataTimer);
        await saveCurrentNote();
        enterViewMode();
    });

    // Keyboard shortcut: Ctrl/Cmd+S to save note immediately
    document.addEventListener('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 's' && activeTab === 'notes' && activeNoteId) {
            e.preventDefault();
            clearTimeout(noteSaveTimer);
            saveCurrentNote();
        }
    });

    newNoteBtn.addEventListener('click', createNote);
    deleteNoteBtn.addEventListener('click', function () {
        if (activeNoteId) deleteNote(activeNoteId);
    });

    // ================================================
    // Notes — Search by tag
    // ================================================

    async function refreshUserTags() {
        try {
            var data = await apiGet('/api/notes/tags');
            allUserTags = data.tags || [];
        } catch (_) {
            allUserTags = [];
        }
    }

    function toggleNotesSearch() {
        var isVisible = !notesSearchEl.classList.contains('hidden');
        if (isVisible) {
            notesSearchEl.classList.add('hidden');
            searchNotesBtn.classList.remove('active');
            if (activeTagFilter) {
                activeTagFilter = null;
                notesSearchInput.value = '';
                loadNotes();
            }
        } else {
            notesSearchEl.classList.remove('hidden');
            searchNotesBtn.classList.add('active');
            notesSearchInput.focus();
            refreshUserTags();
        }
    }

    function showAutocomplete(filter) {
        var text = (filter || '').toLowerCase();
        var matches = allUserTags.filter(function (tag) {
            return tag.toLowerCase().indexOf(text) >= 0;
        });
        notesSearchAutocomplete.innerHTML = '';
        if (matches.length === 0 || (text.length === 0 && matches.length === 0)) {
            notesSearchAutocomplete.classList.add('hidden');
            return;
        }
        matches.forEach(function (tag) {
            var div = document.createElement('div');
            div.className = 'notes-search-autocomplete-item';
            div.textContent = tag;
            div.addEventListener('click', function () {
                notesSearchInput.value = tag;
                notesSearchAutocomplete.classList.add('hidden');
                activeTagFilter = tag;
                loadNotes();
            });
            notesSearchAutocomplete.appendChild(div);
        });
        notesSearchAutocomplete.classList.remove('hidden');
    }

    searchNotesBtn.addEventListener('click', toggleNotesSearch);

    notesSearchInput.addEventListener('input', function () {
        showAutocomplete(notesSearchInput.value);
    });

    notesSearchInput.addEventListener('focus', function () {
        if (notesSearchInput.value.length === 0) {
            showAutocomplete('');
        }
    });

    notesSearchInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            var text = notesSearchInput.value.trim().toLowerCase();
            notesSearchAutocomplete.classList.add('hidden');
            if (text) {
                activeTagFilter = text;
            } else {
                activeTagFilter = null;
            }
            loadNotes();
        } else if (e.key === 'Escape') {
            notesSearchAutocomplete.classList.add('hidden');
        }
    });

    // Close autocomplete when clicking outside
    document.addEventListener('click', function (e) {
        if (!notesSearchEl.contains(e.target)) {
            notesSearchAutocomplete.classList.add('hidden');
        }
    });

    notesSearchClear.addEventListener('click', function () {
        notesSearchInput.value = '';
        activeTagFilter = null;
        notesSearchAutocomplete.classList.add('hidden');
        loadNotes();
    });

    // Empty state buttons
    document.getElementById('btn-new-note-empty').addEventListener('click', function () {
        newNoteBtn.click();
    });
    document.getElementById('btn-new-chat-empty').addEventListener('click', function () {
        createConversation();
    });

    // ================================================
    // Health — Dashboard, Meals, Exercises
    // ================================================

    var DAY_NAMES = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'];
    var MONTH_NAMES = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
        'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];

    async function loadHealthGoals() {
        try {
            var g = await apiGet('/api/health/goals');
            healthGoals.calorie_goal = g.calorie_goal || 2400;
            healthGoals.exercise_calorie_goal = g.exercise_calorie_goal || 0;
            healthGoals.exercise_time_goal = g.exercise_time_goal || 0;
        } catch (e) { /* use defaults */ }
    }

    // Goals modal: open
    document.getElementById('btn-health-goals').addEventListener('click', function () {
        goalCalorieGoalInput.value = healthGoals.calorie_goal || '';
        goalExerciseCalorieGoalInput.value = healthGoals.exercise_calorie_goal || '';
        goalExerciseTimeGoalInput.value = healthGoals.exercise_time_goal || '';
        healthGoalsOverlay.classList.add('visible');
    });

    // Goals modal: close
    document.getElementById('health-goals-close').addEventListener('click', function () {
        healthGoalsOverlay.classList.remove('visible');
    });
    healthGoalsOverlay.addEventListener('click', function (e) {
        if (e.target === healthGoalsOverlay) healthGoalsOverlay.classList.remove('visible');
    });

    // Goals modal: save
    healthGoalsForm.addEventListener('submit', async function (e) {
        e.preventDefault();
        var btn = document.getElementById('goal-save-btn');
        btn.disabled = true;
        btn.textContent = 'Salvando…';
        try {
            var payload = {
                calorie_goal: parseInt(goalCalorieGoalInput.value) || 0,
                exercise_calorie_goal: parseInt(goalExerciseCalorieGoalInput.value) || 0,
                exercise_time_goal: parseInt(goalExerciseTimeGoalInput.value) || 0,
            };
            var updated = await apiPut('/api/health/goals', payload);
            healthGoals.calorie_goal = updated.calorie_goal;
            healthGoals.exercise_calorie_goal = updated.exercise_calorie_goal;
            healthGoals.exercise_time_goal = updated.exercise_time_goal;
            healthGoalsOverlay.classList.remove('visible');
            showToast('Metas salvas!');
            loadHealthDashboard();
        } catch (err) {
            showToast('Erro ao salvar metas: ' + err.message);
        } finally {
            btn.disabled = false;
            btn.textContent = 'Salvar Metas';
        }
    });

    // Nutritional analysis modal
    var analysisOverlay = document.getElementById('health-analysis-overlay');
    var analysisLoadingEl = document.getElementById('health-analysis-loading');
    var analysisContentEl = document.getElementById('health-analysis-content');

    document.getElementById('btn-health-analysis').addEventListener('click', async function () {
        analysisOverlay.classList.add('visible');
        analysisLoadingEl.classList.remove('hidden');
        analysisContentEl.classList.add('hidden');
        analysisContentEl.innerHTML = '';

        try {
            var data = await apiPost('/api/health/analysis', {});
            var html = typeof marked !== 'undefined' ? marked.parse(data.analysis || '') : escapeHtml(data.analysis || '');
            analysisContentEl.innerHTML = html;
        } catch (err) {
            analysisContentEl.innerHTML = '<p style="color:#DC2626">Erro ao gerar análise: ' + escapeHtml(err.message) + '</p>';
        } finally {
            analysisLoadingEl.classList.add('hidden');
            analysisContentEl.classList.remove('hidden');
        }
    });

    document.getElementById('health-analysis-close').addEventListener('click', function () {
        analysisOverlay.classList.remove('visible');
    });
    analysisOverlay.addEventListener('click', function (e) {
        if (e.target === analysisOverlay) analysisOverlay.classList.remove('visible');
    });

    function formatHealthDate(d) {
        var today = new Date();
        today.setHours(0, 0, 0, 0);
        var target = new Date(d);
        target.setHours(0, 0, 0, 0);
        var diff = Math.round((target - today) / 86400000);
        if (diff === 0) return 'Hoje';
        if (diff === -1) return 'Ontem';
        if (diff === 1) return 'Amanhã';
        return target.getDate() + ' ' + MONTH_NAMES[target.getMonth()] + ' ' + target.getFullYear();
    }

    function healthDateISO() {
        var d = healthDate;
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        var dd = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + dd;
    }

    function updateHealthDateLabel() {
        healthDateLabel.textContent = formatHealthDate(healthDate);
    }

    healthPrevBtn.addEventListener('click', function () {
        healthDate.setDate(healthDate.getDate() - 1);
        updateHealthDateLabel();
        loadHealthDashboard();
    });

    healthNextBtn.addEventListener('click', function () {
        healthDate.setDate(healthDate.getDate() + 1);
        updateHealthDateLabel();
        loadHealthDashboard();
    });

    async function loadHealthDashboard() {
        if (healthLoading) return;
        healthLoading = true;
        updateHealthDateLabel();

        healthLoadingEl.classList.remove('hidden');
        healthContentEl.classList.add('hidden');

        try {
            var dateStr = healthDateISO();
            var dashData = await apiGet('/api/health/dashboard?date=' + dateStr);

            if (!dashData) {
                healthLoadingEl.classList.add('hidden');
                showToast('Erro ao carregar dados de saúde.');
                return;
            }

            renderHealthDashboard(dashData);

            // Load weekly in parallel
            apiGet('/api/health/weekly?end_date=' + dateStr).then(function (weeklyData) {
                if (weeklyData && weeklyData.days) {
                    renderHealthWeekly(weeklyData.days, dateStr);
                }
            }).catch(function () {});
        } catch (err) {
            showToast('Erro ao carregar saúde: ' + err.message);
            healthLoadingEl.classList.add('hidden');
        } finally {
            healthLoading = false;
        }
    }

    function renderHealthDashboard(data) {
        healthLoadingEl.classList.add('hidden');
        healthContentEl.classList.remove('hidden');

        var consumed = data.totals.calories_consumed;
        var burned = data.totals.calories_burned;
        var balance = data.totals.balance;
        var calorieGoal = healthGoals.calorie_goal || 2400;

        healthCaloriesConsumed.textContent = Math.round(consumed) + ' / ' + calorieGoal + ' kcal consumidas';
        healthCaloriesBurned.textContent = Math.round(burned) + ' kcal queimadas'
            + (healthGoals.exercise_calorie_goal > 0 ? ' / ' + healthGoals.exercise_calorie_goal + ' meta' : '');
        healthBalance.textContent = 'Saldo: ' + (balance >= 0 ? '+' : '') + Math.round(balance) + ' kcal';

        var pct = Math.min((consumed / calorieGoal) * 100, 100);
        healthProgressFill.style.width = pct + '%';
        healthProgressFill.title = Math.round(pct) + '%';

        // Exercise time goal row
        var timeRowEl = document.getElementById('health-exercise-time-row');
        if (healthGoals.exercise_time_goal > 0) {
            var totalMin = (data.exercises || []).reduce(function (s, e) { return s + (parseInt(e.duration_minutes) || 0); }, 0);
            var timePct = Math.min((totalMin / healthGoals.exercise_time_goal) * 100, 100);
            if (!timeRowEl) {
                var summary = document.getElementById('health-summary');
                var row = document.createElement('div');
                row.id = 'health-exercise-time-row';
                row.className = 'health-summary-row';
                row.innerHTML = '<span class="health-summary-icon">⏱️</span>'
                    + '<span class="health-summary-text" id="health-exercise-time-text"></span>';
                summary.appendChild(row);
                var pb = document.createElement('div');
                pb.className = 'health-progress-bar';
                pb.innerHTML = '<div class="health-progress-fill health-progress-fill--exercise-time" id="health-exercise-time-fill"></div>';
                summary.appendChild(pb);
                timeRowEl = row;
            }
            document.getElementById('health-exercise-time-text').textContent = totalMin + ' / ' + healthGoals.exercise_time_goal + ' min de exercício';
            document.getElementById('health-exercise-time-fill').style.width = timePct + '%';
            timeRowEl.style.display = '';
            timeRowEl.nextElementSibling.style.display = '';
        } else if (timeRowEl) {
            timeRowEl.style.display = 'none';
            timeRowEl.nextElementSibling.style.display = 'none';
        }

        // Render meals grouped by type
        renderMealGroups(data.meals);

        // Render exercises
        renderExercises(data.exercises);
    }

    var MEAL_TYPE_ORDER = ['CAFÉ DA MANHÃ', 'ALMOÇO', 'LANCHE', 'JANTAR', 'SUPLEMENTO'];
    var MEAL_TYPE_LABELS = {
        'CAFÉ DA MANHÃ': 'Café da manhã',
        'ALMOÇO': 'Almoço',
        'LANCHE': 'Lanche',
        'JANTAR': 'Jantar',
        'SUPLEMENTO': 'Suplemento'
    };

    function renderMealGroups(meals) {
        if (!meals || meals.length === 0) {
            healthMealsEl.innerHTML = '<div class="health-empty-day">Nenhuma refeição registrada</div>';
            return;
        }

        // Group by meal_type
        var groups = {};
        meals.forEach(function (m) {
            var mt = (m.meal_type || 'OUTRO').toUpperCase();
            if (!groups[mt]) groups[mt] = [];
            groups[mt].push(m);
        });

        var html = '';
        MEAL_TYPE_ORDER.forEach(function (type) {
            if (!groups[type]) return;
            var items = groups[type];
            var subtotal = items.reduce(function (s, m) { return s + (parseFloat(m.calories) || 0); }, 0);
            html += '<div class="health-meal-group">';
            html += '<div class="health-meal-group-title">' + escapeHtml(MEAL_TYPE_LABELS[type] || type) + '</div>';
            items.forEach(function (m) {
                html += '<div class="health-meal-item">';
                html += '<span class="health-meal-food">' + escapeHtml(m.food || '') + '</span>';
                html += '<span class="health-meal-qty">' + escapeHtml(m.quantity || '') + '</span>';
                html += '<span class="health-meal-kcal">' + Math.round(parseFloat(m.calories) || 0) + ' kcal</span>';
                html += '</div>';
            });
            html += '<div class="health-meal-subtotal">' + Math.round(subtotal) + ' kcal</div>';
            html += '</div>';
        });

        // Remaining types not in order
        Object.keys(groups).forEach(function (type) {
            if (MEAL_TYPE_ORDER.indexOf(type) >= 0) return;
            var items = groups[type];
            var subtotal = items.reduce(function (s, m) { return s + (parseFloat(m.calories) || 0); }, 0);
            html += '<div class="health-meal-group">';
            html += '<div class="health-meal-group-title">' + escapeHtml(type) + '</div>';
            items.forEach(function (m) {
                html += '<div class="health-meal-item">';
                html += '<span class="health-meal-food">' + escapeHtml(m.food || '') + '</span>';
                html += '<span class="health-meal-qty">' + escapeHtml(m.quantity || '') + '</span>';
                html += '<span class="health-meal-kcal">' + Math.round(parseFloat(m.calories) || 0) + ' kcal</span>';
                html += '</div>';
            });
            html += '<div class="health-meal-subtotal">' + Math.round(subtotal) + ' kcal</div>';
            html += '</div>';
        });

        healthMealsEl.innerHTML = html;
    }

    function renderExercises(exercises) {
        if (!exercises || exercises.length === 0) {
            healthExercisesEl.innerHTML = '<h3 class="health-section-title">Exercícios</h3><div class="health-empty-day">Nenhum exercício registrado</div>';
            return;
        }

        var html = '<h3 class="health-section-title">Exercícios</h3>';
        exercises.forEach(function (e) {
            var isDone = e.done === true || e.done === 'true';
            var pageId = e.id || e.page_id || '';
            html += '<div class="health-exercise-item">';
            html += '<div class="health-exercise-check ' + (isDone ? 'done' : '') + '" data-page-id="' + escapeHtml(pageId) + '" data-done="' + isDone + '">' + (isDone ? '✓' : '') + '</div>';
            html += '<span class="health-exercise-name">' + escapeHtml(e.activity || '') + '</span>';
            if (e.duration_minutes) {
                html += '<span class="health-exercise-duration">' + e.duration_minutes + ' min</span>';
            }
            if (e.observations) {
                html += '<span class="health-exercise-obs">' + escapeHtml(e.observations) + '</span>';
            }
            html += '<span class="health-exercise-kcal">' + Math.round(parseFloat(e.calories) || 0) + ' kcal</span>';
            html += '</div>';
        });
        healthExercisesEl.innerHTML = html;
    }

    // Toggle exercise done status
    healthExercisesEl.addEventListener('click', async function (e) {
        var check = e.target.closest('.health-exercise-check');
        if (!check) return;
        var pageId = check.dataset.pageId;
        if (!pageId) return;
        var newDone = check.dataset.done !== 'true';

        check.classList.toggle('done', newDone);
        check.textContent = newDone ? '✓' : '';
        check.dataset.done = String(newDone);

        try {
            await apiPatch('/api/health/exercises/' + encodeURIComponent(pageId), { done: newDone });
        } catch (err) {
            showToast('Erro ao atualizar: ' + err.message);
            check.classList.toggle('done', !newDone);
            check.textContent = !newDone ? '✓' : '';
            check.dataset.done = String(!newDone);
        }
    });

    function renderHealthWeekly(days, currentDate) {
        if (!days || days.length === 0) {
            healthWeeklyBars.innerHTML = '';
            return;
        }

        var maxCal = Math.max(healthGoals.calorie_goal || 2400, Math.max.apply(null, days.map(function (d) { return d.calories_consumed; })));

        var html = '';
        days.forEach(function (d) {
            var dt = new Date(d.date + 'T12:00:00');
            var dayName = DAY_NAMES[dt.getDay()];
            var pct = maxCal > 0 ? Math.min((d.calories_consumed / maxCal) * 100, 100) : 0;
            var isToday = d.date === currentDate;

            html += '<div class="health-weekly-row' + (isToday ? ' today' : '') + '">';
            html += '<span class="health-weekly-day">' + dayName + '</span>';
            html += '<div class="health-weekly-bar-wrap"><div class="health-weekly-bar" style="width:' + pct + '%"></div></div>';
            html += '<span class="health-weekly-kcal">' + Math.round(d.calories_consumed) + '</span>';
            html += '</div>';
        });
        healthWeeklyBars.innerHTML = html;
    }

    // ---- Meal modal ----
    var mealOverlay = document.getElementById('health-meal-overlay');
    var mealForm = document.getElementById('health-meal-form');
    var mealCloseBtn = document.getElementById('health-meal-close');
    var mealSubmitBtn = document.getElementById('meal-submit-btn');
    var mealChipGroup = document.getElementById('meal-type-chips');
    var selectedMealType = 'ALMOÇO';

    document.getElementById('btn-add-meal').addEventListener('click', function () {
        mealOverlay.classList.add('visible');
        document.getElementById('meal-food').focus();
    });

    mealCloseBtn.addEventListener('click', function () {
        mealOverlay.classList.remove('visible');
    });

    mealOverlay.addEventListener('click', function (e) {
        if (e.target === mealOverlay) mealOverlay.classList.remove('visible');
    });

    mealChipGroup.addEventListener('click', function (e) {
        var chip = e.target.closest('.health-chip');
        if (!chip) return;
        mealChipGroup.querySelectorAll('.health-chip').forEach(function (c) { c.classList.remove('active'); });
        chip.classList.add('active');
        selectedMealType = chip.dataset.value;
    });

    mealForm.addEventListener('submit', async function (e) {
        e.preventDefault();
        if (mealSubmitBtn.disabled) return;
        mealSubmitBtn.disabled = true;
        mealSubmitBtn.textContent = 'Registrando…';

        try {
            await apiPost('/api/health/meals', {
                food: document.getElementById('meal-food').value.trim(),
                meal_type: selectedMealType,
                quantity: document.getElementById('meal-quantity').value.trim(),
                estimated_calories: parseFloat(document.getElementById('meal-calories').value)
            });
            mealOverlay.classList.remove('visible');
            mealForm.reset();
            // Re-select default chip
            mealChipGroup.querySelectorAll('.health-chip').forEach(function (c) { c.classList.remove('active'); });
            mealChipGroup.querySelector('[data-value="ALMOÇO"]').classList.add('active');
            selectedMealType = 'ALMOÇO';
            showToast('Refeição registrada ✓');
            loadHealthDashboard();
        } catch (err) {
            showToast('Erro: ' + err.message);
        } finally {
            mealSubmitBtn.disabled = false;
            mealSubmitBtn.textContent = 'Registrar Refeição';
        }
    });

    // ---- Exercise modal ----
    var exerciseOverlay = document.getElementById('health-exercise-overlay');
    var exerciseForm = document.getElementById('health-exercise-form');
    var exerciseCloseBtn = document.getElementById('health-exercise-close');
    var exerciseSubmitBtn = document.getElementById('exercise-submit-btn');

    document.getElementById('btn-add-exercise').addEventListener('click', function () {
        exerciseOverlay.classList.add('visible');
        document.getElementById('exercise-activity').focus();
    });

    exerciseCloseBtn.addEventListener('click', function () {
        exerciseOverlay.classList.remove('visible');
    });

    exerciseOverlay.addEventListener('click', function (e) {
        if (e.target === exerciseOverlay) exerciseOverlay.classList.remove('visible');
    });

    exerciseForm.addEventListener('submit', async function (e) {
        e.preventDefault();
        if (exerciseSubmitBtn.disabled) return;
        exerciseSubmitBtn.disabled = true;
        exerciseSubmitBtn.textContent = 'Registrando…';

        try {
            var durVal = document.getElementById('exercise-duration').value;
            var durMin = durVal ? parseInt(durVal) : null;
            await apiPost('/api/health/exercises', {
                activity: document.getElementById('exercise-activity').value.trim(),
                calories: parseFloat(document.getElementById('exercise-calories').value),
                observations: document.getElementById('exercise-observations').value.trim(),
                done: document.getElementById('exercise-done').checked,
                duration_minutes: durMin && durMin > 0 ? durMin : null,
            });
            exerciseOverlay.classList.remove('visible');
            exerciseForm.reset();
            document.getElementById('exercise-done').checked = true;
            showToast('Exercício registrado ✓');
            loadHealthDashboard();
        } catch (err) {
            showToast('Erro: ' + err.message);
        } finally {
            exerciseSubmitBtn.disabled = false;
            exerciseSubmitBtn.textContent = 'Registrar Exercício';
        }
    });

    // ================================================
    // Finance — Dashboard, Expenses, Bills
    // ================================================

    function financeMonthISO() {
        return financeMonth.getFullYear() + '-' + String(financeMonth.getMonth() + 1).padStart(2, '0');
    }

    function updateFinanceMonthLabel() {
        financeMonthLabel.textContent = MONTH_NAMES[financeMonth.getMonth()] + ' ' + financeMonth.getFullYear();
    }

    financePrevBtn.addEventListener('click', function () {
        financeMonth.setMonth(financeMonth.getMonth() - 1);
        loadFinanceDashboard();
    });

    financeNextBtn.addEventListener('click', function () {
        financeMonth.setMonth(financeMonth.getMonth() + 1);
        loadFinanceDashboard();
    });

    async function loadFinanceDashboard() {
        if (financeLoading) return;
        financeLoading = true;
        updateFinanceMonthLabel();

        financeLoadingEl.classList.remove('hidden');
        financeContentEl.classList.add('hidden');

        try {
            var monthStr = financeMonthISO();
            var data = await apiGet('/api/finance/dashboard?month=' + monthStr);
            if (!data) {
                financeLoadingEl.classList.add('hidden');
                return;
            }
            renderFinanceDashboard(data);
        } catch (err) {
            showToast('Erro ao carregar finanças: ' + err.message);
            financeLoadingEl.classList.add('hidden');
        } finally {
            financeLoading = false;
        }
    }

    function formatBRL(value) {
        return 'R$ ' + Number(value).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function renderFinanceDashboard(data) {
        financeLoadingEl.classList.add('hidden');
        financeContentEl.classList.remove('hidden');

        var totals = data.totals;
        financeTotalExpenses.textContent = formatBRL(totals.total_expenses) + ' em despesas';
        financeTotalBills.textContent = formatBRL(totals.total_budget) + ' em contas fixas';
        financePending.textContent = formatBRL(totals.pending_budget) + ' pendente';

        var pct = totals.total_budget > 0 ? Math.min((totals.total_paid / totals.total_budget) * 100, 100) : 0;
        financeProgressFill.style.width = pct + '%';

        renderBills(data.bills);
        renderExpensesByCategory(data.expenses, data.category_breakdown);
    }

    function renderBills(bills) {
        if (!bills || bills.length === 0) {
            financeBillsEl.innerHTML = '<h3 class="finance-section-title">Contas fixas</h3>' +
                '<div class="finance-empty-state">Nenhuma conta registrada neste mês</div>';
            return;
        }

        var today = new Date().toISOString().slice(0, 10);
        var unpaid = bills.filter(function (b) { return !b.paid; });
        var paid = bills.filter(function (b) { return b.paid; });
        var sorted = unpaid.concat(paid);

        var html = '<h3 class="finance-section-title">Contas fixas (' + bills.length + ')</h3>';
        sorted.forEach(function (bill) {
            var badgeClass = 'badge-pending';
            var badgeText = 'Pendente';
            if (bill.paid) {
                badgeClass = 'badge-paid';
                badgeText = 'Pago';
            } else if (bill.due_date && bill.due_date < today) {
                badgeClass = 'badge-overdue';
                badgeText = 'Vencido';
            }

            var dueMeta = bill.due_date ? 'Venc. ' + formatDateShort(bill.due_date) : '';
            if (bill.category && bill.category !== 'Outros') {
                dueMeta = (dueMeta ? dueMeta + ' · ' : '') + bill.category;
            }

            html += '<div class="finance-bill-card' + (bill.paid ? ' paid' : '') + '" data-bill-id="' + bill.id + '">' +
                '<div class="finance-bill-check' + (bill.paid ? ' done' : '') + '" data-bill-id="' + bill.id + '">' +
                (bill.paid ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>' : '') +
                '</div>' +
                '<div class="finance-bill-info">' +
                '<div class="finance-bill-name">' + escapeHtml(bill.bill_name) + '</div>' +
                (dueMeta ? '<div class="finance-bill-meta">' + dueMeta + '</div>' : '') +
                '</div>' +
                '<div class="finance-bill-amount">' + formatBRL(bill.budget) + '</div>' +
                '<span class="finance-badge ' + badgeClass + '">' + badgeText + '</span>' +
                '<button class="finance-bill-delete" data-bill-id="' + bill.id + '" title="Excluir">' +
                '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
                '</button>' +
                '</div>';
        });
        financeBillsEl.innerHTML = html;

        // Toggle paid
        financeBillsEl.querySelectorAll('.finance-bill-check').forEach(function (el) {
            el.addEventListener('click', function (e) {
                e.stopPropagation();
                toggleBillPaid(el.dataset.billId);
            });
        });

        // Delete bill
        financeBillsEl.querySelectorAll('.finance-bill-delete').forEach(function (el) {
            el.addEventListener('click', function (e) {
                e.stopPropagation();
                deleteBill(el.dataset.billId);
            });
        });
    }

    function formatDateShort(dateStr) {
        if (!dateStr) return '';
        var parts = dateStr.split('-');
        return parts[2] + '/' + parts[1];
    }

    async function toggleBillPaid(billId) {
        var card = financeBillsEl.querySelector('[data-bill-id="' + billId + '"].finance-bill-card');
        var isPaid = card && card.classList.contains('paid');
        try {
            await apiRequest('PATCH', '/api/finance/bills/' + billId, {
                paid: !isPaid,
                paid_amount: !isPaid ? undefined : 0
            });
            loadFinanceDashboard();
        } catch (err) {
            showToast('Erro: ' + err.message);
        }
    }

    async function deleteBill(billId) {
        if (!confirm('Excluir esta conta?')) return;
        try {
            await apiDelete('/api/finance/bills/' + billId);
            showToast('Conta excluída ✓');
            loadFinanceDashboard();
        } catch (err) {
            showToast('Erro: ' + err.message);
        }
    }

    function renderExpensesByCategory(expenses, breakdown) {
        if (!expenses || expenses.length === 0) {
            financeExpensesEl.innerHTML = '<h3 class="finance-section-title">Despesas</h3>' +
                '<div class="finance-empty-state">Nenhuma despesa registrada neste mês</div>';
            return;
        }

        var grouped = {};
        expenses.forEach(function (exp) {
            var cat = exp.category || 'Outros';
            if (!grouped[cat]) grouped[cat] = [];
            grouped[cat].push(exp);
        });

        var catTotals = {};
        (breakdown || []).forEach(function (b) { catTotals[b.category] = b.total; });

        var html = '<h3 class="finance-section-title">Despesas (' + expenses.length + ')</h3>';
        Object.keys(grouped).sort().forEach(function (cat) {
            var items = grouped[cat];
            var total = catTotals[cat] || items.reduce(function (s, e) { return s + e.amount; }, 0);

            html += '<div class="finance-category-group">' +
                '<div class="finance-category-header">' +
                '<span class="finance-category-name">' + escapeHtml(cat) + '</span>' +
                '<span class="finance-category-total">' + formatBRL(total) + '</span>' +
                '</div>';

            items.forEach(function (exp) {
                html += '<div class="finance-expense-item" data-expense-id="' + exp.id + '">' +
                    '<span class="finance-expense-name">' + escapeHtml(exp.name) + '</span>' +
                    '<span class="finance-expense-date">' + formatDateShort(exp.date) + '</span>' +
                    '<span class="finance-expense-amount">' + formatBRL(exp.amount) + '</span>' +
                    '<button class="finance-expense-delete" data-expense-id="' + exp.id + '" title="Excluir">' +
                    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
                    '</button>' +
                    '</div>';
            });
            html += '</div>';
        });
        financeExpensesEl.innerHTML = html;

        // Delete expense
        financeExpensesEl.querySelectorAll('.finance-expense-delete').forEach(function (el) {
            el.addEventListener('click', function (e) {
                e.stopPropagation();
                deleteExpense(el.dataset.expenseId);
            });
        });
    }

    async function deleteExpense(expenseId) {
        if (!confirm('Excluir esta despesa?')) return;
        try {
            await apiDelete('/api/finance/expenses/' + expenseId);
            showToast('Despesa excluída ✓');
            loadFinanceDashboard();
        } catch (err) {
            showToast('Erro: ' + err.message);
        }
    }

    // ---- Finance modals ----
    var expenseOverlay = document.getElementById('finance-expense-overlay');
    var expenseForm = document.getElementById('finance-expense-form');
    var expenseSubmitBtn = document.getElementById('expense-submit-btn');
    var expenseCloseBtn = document.getElementById('finance-expense-close');
    var expenseDateInput = document.getElementById('expense-date');

    var billOverlay = document.getElementById('finance-bill-overlay');
    var billForm = document.getElementById('finance-bill-form');
    var billSubmitBtn = document.getElementById('bill-submit-btn');
    var billCloseBtn = document.getElementById('finance-bill-close');

    document.getElementById('btn-add-expense').addEventListener('click', function () {
        expenseDateInput.value = new Date().toISOString().slice(0, 10);
        expenseOverlay.classList.add('visible');
    });

    expenseCloseBtn.addEventListener('click', function () {
        expenseOverlay.classList.remove('visible');
    });

    expenseOverlay.addEventListener('click', function (e) {
        if (e.target === expenseOverlay) expenseOverlay.classList.remove('visible');
    });

    // Category chips for expenses
    setupChipGroup('expense-category-chips');

    expenseForm.addEventListener('submit', async function (e) {
        e.preventDefault();
        expenseSubmitBtn.disabled = true;
        expenseSubmitBtn.textContent = 'Registrando…';

        var activeChip = document.querySelector('#expense-category-chips .health-chip.active');
        try {
            await apiPost('/api/finance/expenses', {
                name: document.getElementById('expense-name').value.trim(),
                amount: parseFloat(document.getElementById('expense-amount').value),
                category: activeChip ? activeChip.dataset.value : 'Outros',
                date: expenseDateInput.value || undefined
            });
            expenseOverlay.classList.remove('visible');
            expenseForm.reset();
            showToast('Despesa registrada ✓');
            loadFinanceDashboard();
        } catch (err) {
            showToast('Erro: ' + err.message);
        } finally {
            expenseSubmitBtn.disabled = false;
            expenseSubmitBtn.textContent = 'Registrar Despesa';
        }
    });

    document.getElementById('btn-add-bill').addEventListener('click', function () {
        billOverlay.classList.add('visible');
    });

    billCloseBtn.addEventListener('click', function () {
        billOverlay.classList.remove('visible');
    });

    billOverlay.addEventListener('click', function (e) {
        if (e.target === billOverlay) billOverlay.classList.remove('visible');
    });

    // Category chips for bills
    setupChipGroup('bill-category-chips');

    billForm.addEventListener('submit', async function (e) {
        e.preventDefault();
        billSubmitBtn.disabled = true;
        billSubmitBtn.textContent = 'Registrando…';

        var activeChip = document.querySelector('#bill-category-chips .health-chip.active');
        try {
            await apiPost('/api/finance/bills', {
                bill_name: document.getElementById('bill-name').value.trim(),
                budget: parseFloat(document.getElementById('bill-budget').value),
                category: activeChip ? activeChip.dataset.value : 'Outros',
                due_date: document.getElementById('bill-due-date').value || undefined,
                reference_month: financeMonthISO()
            });
            billOverlay.classList.remove('visible');
            billForm.reset();
            showToast('Conta registrada ✓');
            loadFinanceDashboard();
        } catch (err) {
            showToast('Erro: ' + err.message);
        } finally {
            billSubmitBtn.disabled = false;
            billSubmitBtn.textContent = 'Registrar Conta';
        }
    });

    // Generic chip group setup
    function setupChipGroup(groupId) {
        var group = document.getElementById(groupId);
        if (!group) return;
        group.addEventListener('click', function (e) {
            var chip = e.target.closest('.health-chip');
            if (!chip) return;
            group.querySelectorAll('.health-chip').forEach(function (c) { c.classList.remove('active'); });
            chip.classList.add('active');
        });
    }

    // ---- Init ----
    inputEl.focus();
    updateSendButton();
    updateChatEmptyState();
    loadConversations();
    checkGoogleStatus();

    // ================================================
    // Tasks
    // ================================================

    async function loadTasks() {
        var loadingEl = document.getElementById('tasks-loading');
        var contentEl = document.getElementById('tasks-content');
        if (loadingEl) loadingEl.style.display = 'flex';

        try {
            var data = await apiGet('/api/tasks?include_done=true');
            tasksData = data.tasks || [];
            var metaData = await apiGet('/api/tasks/meta');
            tasksMeta = metaData || { projects: [], tags: [] };
        } catch (err) {
            showToast('Erro ao carregar tarefas');
            return;
        } finally {
            if (loadingEl) loadingEl.style.display = 'none';
        }
        renderTaskGroups();
    }

    function classifyTask(task) {
        if (!task.deadline) return 'no-deadline';
        var today = new Date();
        today.setHours(0, 0, 0, 0);
        var dl = new Date(task.deadline + 'T00:00:00');
        var diff = Math.floor((dl - today) / 86400000);
        if (diff < 0) return 'overdue';
        if (diff === 0) return 'today';
        if (diff <= 7) return 'week';
        return 'later';
    }

    function renderTaskGroups() {
        var contentEl = document.getElementById('tasks-content');
        if (!contentEl) return;
        contentEl.innerHTML = '';

        var pending = tasksData.filter(function (t) { return !t.done; });
        var done = tasksData.filter(function (t) { return t.done; });

        var groups = [
            { key: 'overdue', label: 'Atrasadas', accent: 'red', tasks: [] },
            { key: 'today', label: 'Hoje', accent: 'orange', tasks: [] },
            { key: 'week', label: 'Próximos 7 dias', accent: 'blue', tasks: [] },
            { key: 'later', label: 'Mais tarde', accent: 'gray', tasks: [] },
            { key: 'no-deadline', label: 'Sem prazo', accent: 'gray', tasks: [] },
        ];

        pending.forEach(function (t) {
            var g = groups.find(function (g) { return g.key === classifyTask(t); });
            if (g) g.tasks.push(t);
        });

        groups.forEach(function (g) {
            if (g.tasks.length === 0) return;
            contentEl.appendChild(buildTaskGroup(g));
        });

        if (tasksShowDone && done.length > 0) {
            contentEl.appendChild(buildTaskGroup({ key: 'done', label: 'Concluídas', accent: 'gray', tasks: done, collapsed: true }));
        }

        if (pending.length === 0 && !(tasksShowDone && done.length > 0)) {
            var empty = document.createElement('div');
            empty.className = 'tasks-empty';
            empty.textContent = 'Nenhuma tarefa pendente.';
            contentEl.appendChild(empty);
        }
    }

    function buildTaskGroup(group) {
        var el = document.createElement('div');
        el.className = 'task-group' + (group.collapsed ? ' collapsed' : '');
        el.dataset.key = group.key;

        var header = document.createElement('div');
        header.className = 'task-group-header';
        header.dataset.accent = group.accent;

        var labelSpan = document.createElement('span');
        labelSpan.className = 'task-group-label';
        labelSpan.textContent = group.label;

        var badge = document.createElement('span');
        badge.className = 'task-group-count';
        badge.textContent = group.tasks.length;

        header.appendChild(labelSpan);
        header.appendChild(badge);

        if (group.collapsed) {
            var arrow = document.createElement('span');
            arrow.className = 'task-group-arrow';
            arrow.textContent = '▸';
            header.appendChild(arrow);
        }

        header.addEventListener('click', function () {
            el.classList.toggle('collapsed');
            if (arrow) arrow.textContent = el.classList.contains('collapsed') ? '▸' : '▾';
        });

        var list = document.createElement('div');
        list.className = 'task-group-list';

        group.tasks.forEach(function (task) {
            list.appendChild(buildTaskItem(task));
        });

        el.appendChild(header);
        el.appendChild(list);
        return el;
    }

    function buildTaskItem(task) {
        var el = document.createElement('div');
        el.className = 'task-item' + (task.done ? ' done' : '');
        el.dataset.taskId = task.id;

        // Checkbox
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'task-checkbox';
        cb.checked = task.done;
        cb.addEventListener('change', function () {
            toggleTaskDone(task.id, cb.checked);
        });

        // Name
        var nameSpan = document.createElement('span');
        nameSpan.className = 'task-item-name';
        nameSpan.textContent = task.name;
        nameSpan.addEventListener('click', function () {
            openTaskEditInline(el, task);
        });

        el.appendChild(cb);
        el.appendChild(nameSpan);

        // Project badge
        if (task.project) {
            var proj = document.createElement('span');
            proj.className = 'task-project-badge';
            proj.textContent = task.project;
            el.appendChild(proj);
        }

        // Tag pills
        if (task.tags && task.tags.length > 0) {
            task.tags.forEach(function (tag) {
                var pill = document.createElement('span');
                pill.className = 'task-tag-pill';
                pill.textContent = tag;
                el.appendChild(pill);
            });
        }

        // Deadline badge
        if (task.deadline) {
            el.appendChild(buildDeadlineBadge(task.deadline));
        }

        // Delete button
        var delBtn = document.createElement('button');
        delBtn.className = 'task-delete-btn';
        delBtn.title = 'Excluir tarefa';
        delBtn.innerHTML = '&times;';
        delBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            deleteTask(task.id);
        });
        el.appendChild(delBtn);

        return el;
    }

    function buildDeadlineBadge(deadline) {
        var span = document.createElement('span');
        span.className = 'task-deadline-badge';

        var today = new Date();
        today.setHours(0, 0, 0, 0);
        var dl = new Date(deadline + 'T00:00:00');
        var diff = Math.floor((dl - today) / 86400000);

        if (diff < 0) {
            span.classList.add('overdue');
            span.textContent = formatDeadline(deadline);
        } else if (diff === 0) {
            span.classList.add('today');
            span.textContent = 'hoje';
        } else if (diff === 1) {
            span.textContent = 'amanhã';
        } else {
            span.textContent = formatDeadline(deadline);
        }
        return span;
    }

    function formatDeadline(dateStr) {
        var parts = dateStr.split('-');
        if (parts.length !== 3) return dateStr;
        return parts[2] + '/' + parts[1];
    }

    async function createTask(name, deadline, project, tags) {
        var body = { name: name };
        if (deadline) body.deadline = deadline;
        if (project) body.project = project;
        if (tags && tags.length > 0) body.tags = tags;
        try {
            await apiPost('/api/tasks', body);
            await loadTasks();
        } catch (err) {
            showToast('Erro ao criar tarefa: ' + err.message);
        }
    }

    async function toggleTaskDone(taskId, done) {
        try {
            await apiPatch('/api/tasks/' + taskId, { done: done });
            var idx = tasksData.findIndex(function (t) { return t.id === taskId; });
            if (idx !== -1) tasksData[idx].done = done;
            renderTaskGroups();
        } catch (err) {
            showToast('Erro ao atualizar tarefa');
        }
    }

    async function deleteTask(taskId) {
        try {
            await apiDelete('/api/tasks/' + taskId);
            tasksData = tasksData.filter(function (t) { return t.id !== taskId; });
            renderTaskGroups();
        } catch (err) {
            showToast('Erro ao excluir tarefa');
        }
    }

    async function saveTaskEdit(taskId, data) {
        try {
            await apiPatch('/api/tasks/' + taskId, data);
            await loadTasks();
        } catch (err) {
            showToast('Erro ao salvar tarefa');
        }
    }

    function openTaskEditInline(taskEl, task) {
        // Prevent double-edit
        if (taskEl.querySelector('.task-edit-input')) return;

        var nameSpan = taskEl.querySelector('.task-item-name');
        var originalName = task.name;

        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'task-edit-input';
        input.value = originalName;
        input.maxLength = 200;

        nameSpan.replaceWith(input);
        input.focus();
        input.select();

        function save() {
            var newName = input.value.trim();
            if (!newName) {
                cancel();
                return;
            }
            saveTaskEdit(task.id, { name: newName });
        }

        function cancel() {
            input.replaceWith(nameSpan);
        }

        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); save(); }
            if (e.key === 'Escape') cancel();
        });
        input.addEventListener('blur', function () { save(); });
    }

    // Autocomplete helper
    function setupTaskAutocomplete(inputEl, acEl, getItems, onSelect) {
        inputEl.addEventListener('input', function () {
            var val = inputEl.value.split(',').pop().trim().toLowerCase();
            if (!val) { acEl.classList.add('hidden'); return; }
            var items = getItems().filter(function (i) {
                return i.toLowerCase().startsWith(val);
            });
            if (items.length === 0) { acEl.classList.add('hidden'); return; }
            acEl.innerHTML = '';
            items.slice(0, 8).forEach(function (item) {
                var div = document.createElement('div');
                div.className = 'tasks-autocomplete-item';
                div.textContent = item;
                div.addEventListener('mousedown', function (e) {
                    e.preventDefault();
                    onSelect(item);
                    acEl.classList.add('hidden');
                });
                acEl.appendChild(div);
            });
            acEl.classList.remove('hidden');
        });
        inputEl.addEventListener('blur', function () {
            setTimeout(function () { acEl.classList.add('hidden'); }, 150);
        });
    }

    // Wire up add-task form
    var btnNewTask = document.getElementById('btn-new-task');
    var tasksAddForm = document.getElementById('tasks-add-form');
    var taskFormName = document.getElementById('task-form-name');
    var taskFormDeadline = document.getElementById('task-form-deadline');
    var taskFormProject = document.getElementById('task-form-project');
    var taskFormProjectAc = document.getElementById('task-form-project-ac');
    var taskFormTags = document.getElementById('task-form-tags');
    var taskFormTagsAc = document.getElementById('task-form-tags-ac');
    var taskFormSave = document.getElementById('task-form-save');
    var taskFormCancel = document.getElementById('task-form-cancel');
    var tasksShowDoneEl = document.getElementById('tasks-show-done');

    if (btnNewTask) {
        btnNewTask.addEventListener('click', function () {
            tasksAddForm.classList.toggle('hidden');
            if (!tasksAddForm.classList.contains('hidden')) {
                taskFormName.focus();
            }
        });
    }

    function clearTaskForm() {
        taskFormName.value = '';
        taskFormDeadline.value = '';
        taskFormProject.value = '';
        taskFormTags.value = '';
        tasksAddForm.classList.add('hidden');
    }

    if (taskFormSave) {
        taskFormSave.addEventListener('click', async function () {
            var name = taskFormName.value.trim();
            if (!name) { taskFormName.focus(); return; }
            var deadline = taskFormDeadline.value || null;
            var project = taskFormProject.value.trim() || null;
            var rawTags = taskFormTags.value.split(',').map(function (t) { return t.trim().toLowerCase(); }).filter(Boolean);
            clearTaskForm();
            await createTask(name, deadline, project, rawTags);
        });
    }

    if (taskFormCancel) {
        taskFormCancel.addEventListener('click', function () { clearTaskForm(); });
    }

    if (taskFormName) {
        taskFormName.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); taskFormSave && taskFormSave.click(); }
        });
    }

    if (tasksShowDoneEl) {
        tasksShowDoneEl.addEventListener('change', function () {
            tasksShowDone = tasksShowDoneEl.checked;
            renderTaskGroups();
        });
    }

    // Set up autocomplete for project and tags
    if (taskFormProject && taskFormProjectAc) {
        setupTaskAutocomplete(
            taskFormProject, taskFormProjectAc,
            function () { return tasksMeta.projects || []; },
            function (item) { taskFormProject.value = item; }
        );
    }

    if (taskFormTags && taskFormTagsAc) {
        setupTaskAutocomplete(
            taskFormTags, taskFormTagsAc,
            function () { return tasksMeta.tags || []; },
            function (item) {
                var parts = taskFormTags.value.split(',');
                parts[parts.length - 1] = item;
                taskFormTags.value = parts.join(', ');
            }
        );
    }

})();
