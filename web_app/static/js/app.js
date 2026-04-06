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
    const headerTabs = document.getElementById('header-tabs');
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

    // Search refs
    const searchNotesBtn = document.getElementById('btn-search-notes');
    const notesSearchEl = document.getElementById('notes-search');
    const notesSearchInput = document.getElementById('notes-search-input');
    const notesSearchClear = document.getElementById('notes-search-clear');
    const notesSearchAutocomplete = document.getElementById('notes-search-autocomplete');

    let activeTab = 'chat';
    let activeNoteId = null;
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
    const healthNotConfiguredEl = document.getElementById('health-not-configured');
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

    // Sidebar nav refs
    const sidebarNavChat = document.getElementById('sidebar-nav-chat');
    const sidebarNavHealth = document.getElementById('sidebar-nav-health');
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
            } else if (!activeConversationId || !data.conversations.find(function (c) { return c.id === activeConversationId; })) {
                await switchConversation(data.conversations[0].id);
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

    memoriesBtn.addEventListener('click', async function () {
        memoriesOverlay.classList.add('visible');
        memoriesContent.innerHTML = '<div class="notion-spinner"></div>';
        try {
            var resp = await fetch('/api/memories', { headers: authHeaders() });
            if (!resp.ok) throw new Error();
            var data = await resp.json();
            if (data.count === 0) {
                memoriesContent.innerHTML = '<p style="color:var(--color-text-muted);text-align:center;padding:12px 0">Nenhuma memória encontrada</p>';
            } else {
                var html = '';
                data.files.forEach(function (file) {
                    html += '<div class="memories-file">' +
                        '<div class="memories-file-header" onclick="this.parentElement.classList.toggle(\'expanded\')">' +
                        '<span>\u{1F4C4} ' + escapeHtml(file.display_name) + '</span>' +
                        '<span class="chevron">\u25B6</span>' +
                        '</div>' +
                        '<div class="memories-file-content">' + escapeHtml(file.content) + '</div>' +
                        '</div>';
                });
                memoriesContent.innerHTML = html;
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

        // Update header tab buttons (chat / notes only)
        var headerTab = (tab === 'chat' || tab === 'notes') ? tab : null;
        headerTabs.querySelectorAll('.header-tab').forEach(function (btn) {
            btn.classList.toggle('active', btn.dataset.tab === headerTab);
        });
        headerTabs.classList.remove('tab-notes-active', 'tab-none-active');
        if (tab === 'notes') headerTabs.classList.add('tab-notes-active');
        if (tab === 'health') headerTabs.classList.add('tab-none-active');

        // Update sidebar nav buttons
        sidebarNavChat.classList.toggle('active', tab === 'chat' || tab === 'notes');
        sidebarNavHealth.classList.toggle('active', tab === 'health');

        // Show/hide sidebar list sections
        var showLists = (tab !== 'health');
        sidebarHeaderEl.style.display = showLists ? '' : 'none';
        conversationListSection.style.display = showLists ? '' : 'none';
        notesListSection.style.display = showLists ? '' : 'none';

        // Hide header tabs (Conversas/Anotações) when in health view
        headerTabs.style.display = (tab === 'health') ? 'none' : '';

        // Hide everything first
        chatSection.classList.add('hidden');
        chatEmptyEl.classList.add('hidden');
        chatInputWrapper.classList.add('hidden');
        notesEditorEl.classList.add('hidden');
        notesEmptyEl.classList.add('hidden');
        healthViewEl.classList.add('hidden');
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
            loadHealthDashboard();
        }
    }

    headerTabs.addEventListener('click', function (e) {
        var tab = e.target.dataset && e.target.dataset.tab;
        if (tab) switchTab(tab);
    });

    // Sidebar nav click handler
    document.querySelector('.sidebar-nav').addEventListener('click', function (e) {
        var btn = e.target.closest('.sidebar-nav-item');
        if (!btn) return;
        var nav = btn.dataset.nav;
        if (nav === 'chat') {
            switchTab('chat');
        } else if (nav === 'health') {
            switchTab('health');
        }
        // On mobile close sidebar after nav selection
        if (window.matchMedia('(max-width: 767px)').matches) {
            sidebar.classList.remove('open');
            sidebarOverlay.classList.remove('visible');
        }
    });

    // ================================================
    // Notes — CRUD & Editor
    // ================================================

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
            // Update editor if still viewing the same note
            if (activeNoteId === noteId) {
                noteTitleDisplay.textContent = meta.title;
                renderNoteTags(meta.tags || []);
                noteSaveStatus.textContent = 'Salvo ✓';
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
            // Refresh CodeMirror layout after being hidden
            if (easyMDE) setTimeout(function () { easyMDE.codemirror.refresh(); }, 0);
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
            await selectNote(data.id);
        } catch (err) {
            showToast('Erro ao criar anotação');
        }
    }

    async function selectNote(noteId) {
        // Auto-switch to notes tab if not already there
        if (activeTab !== 'notes') switchTab('notes');

        // Generate metadata for previous note if content changed
        if (activeNoteId && activeNoteId !== noteId && easyMDE && activeNoteContentDirty) {
            flushNoteAndGenerateMetadata();
        }

        // Save pending changes for current note first
        if (activeNoteId && easyMDE) {
            clearTimeout(noteSaveTimer);
            await saveCurrentNote();
        }

        activeNoteId = noteId;
        activeNoteContentDirty = false;
        clearTimeout(noteMetadataTimer);

        // Update sidebar active state
        notesListEl.querySelectorAll('.note-item').forEach(function (item) {
            item.classList.toggle('active', item.dataset.id === noteId);
        });

        try {
            var note = await apiGet('/api/notes/' + noteId);
            notesEmptyEl.classList.add('hidden');
            notesEditorEl.classList.remove('hidden');
            initEasyMDE();
            noteTitleDisplay.textContent = note.title;
            renderNoteTags(note.tags || []);
            easyMDE.value(note.content || '');
            noteSaveStatus.textContent = '';
            activeNoteContentDirty = false;
            // Close sidebar on mobile after selecting
            closeSidebar();
        } catch (err) {
            showToast('Erro ao abrir anotação');
            activeNoteId = null;
        }
    }

    async function deleteNote(noteId) {
        if (!confirm('Excluir esta anotação?')) return;
        try {
            await apiDelete('/api/notes/' + noteId);
            if (activeNoteId === noteId) {
                activeNoteId = null;
                activeNoteContentDirty = false;
                notesEditorEl.classList.add('hidden');
                notesEmptyEl.classList.remove('hidden');
                if (easyMDE) {
                    easyMDE.value('');
                }
                noteTitleDisplay.textContent = 'Nova anotação';
                noteTagsEl.innerHTML = '';
            }
            await loadNotes();
            refreshUserTags();
        } catch (err) {
            showToast('Erro ao excluir anotação');
        }
    }

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
    var CALORIE_GOAL = 2400;

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
        healthNotConfiguredEl.classList.add('hidden');

        try {
            var dateStr = healthDateISO();
            var dashData = await apiGet('/api/health/dashboard?date=' + dateStr);

            if (!dashData || !dashData.notion_configured) {
                healthLoadingEl.classList.add('hidden');
                healthNotConfiguredEl.classList.remove('hidden');
                return;
            }

            renderHealthDashboard(dashData);

            // Load weekly in parallel
            apiGet('/api/health/weekly?end_date=' + dateStr).then(function (weeklyData) {
                if (weeklyData && weeklyData.notion_configured) {
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

        healthCaloriesConsumed.textContent = Math.round(consumed) + ' / ' + CALORIE_GOAL + ' kcal consumidas';
        healthCaloriesBurned.textContent = Math.round(burned) + ' kcal queimadas';
        healthBalance.textContent = 'Saldo: ' + (balance >= 0 ? '+' : '') + Math.round(balance) + ' kcal';

        var pct = Math.min((consumed / CALORIE_GOAL) * 100, 100);
        healthProgressFill.style.width = pct + '%';

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

        var maxCal = Math.max(CALORIE_GOAL, Math.max.apply(null, days.map(function (d) { return d.calories_consumed; })));

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
            await apiPost('/api/health/exercises', {
                activity: document.getElementById('exercise-activity').value.trim(),
                calories: parseFloat(document.getElementById('exercise-calories').value),
                observations: document.getElementById('exercise-observations').value.trim(),
                done: document.getElementById('exercise-done').checked
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

    // ---- Init ----
    inputEl.focus();
    updateSendButton();
    updateChatEmptyState();
    loadConversations();
    loadNotes();
    checkGoogleStatus();
})();
