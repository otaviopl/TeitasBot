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
    const newChatBtn = document.getElementById('btn-new-chat');
    const conversationListEl = document.getElementById('conversation-list');

    let pendingFile = null;
    let isSending = false;
    let activeConversationId = localStorage.getItem('pa_active_conversation') || null;

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
    function scrollToBottom() {
        requestAnimationFrame(() => {
            messagesEl.scrollTop = messagesEl.scrollHeight;
        });
    }

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
    }

    function addMessage(role, content, imageUrls) {
        var div = document.createElement('div');
        div.className = 'message message-' + role;

        if (role === 'assistant') {
            div.innerHTML = renderMarkdown(content);
        } else {
            div.textContent = content;
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
            data.messages.forEach(function (msg) {
                addMessage(msg.role === 'user' ? 'user' : 'assistant', msg.content);
            });
        } catch (_) {
            // Conversation may be empty
        }
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
            addMessage('user', text || '📎 ' + file.name);
            clearFilePreview();
        } else {
            addMessage('user', text);
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
                addMessage('assistant', data.text, data.image_urls);
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

        addMessage('user', '🎙️ Mensagem de voz');
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
                addMessage('assistant', data.text, data.image_urls);
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

    // ---- Notion Status ----
    var notionBtn = document.getElementById('btn-notion-status');
    var notionLabel = document.getElementById('notion-status-label');
    var notionOverlay = document.getElementById('notion-modal-overlay');
    var notionContent = document.getElementById('notion-modal-content');
    var notionClose = document.getElementById('notion-modal-close');

    notionBtn.addEventListener('click', async function () {
        notionOverlay.classList.add('visible');
        notionContent.innerHTML = '<div class="notion-spinner"></div>';
        try {
            var resp = await fetch('/api/notion/check', { headers: authHeaders() });
            if (!resp.ok) throw new Error();
            var data = await resp.json();
            var html = '';
            if (!data.api_key_configured) {
                html = '<p style="color:var(--color-text-muted);text-align:center;padding:12px 0">API Key não configurada</p>';
            } else {
                var dbs = data.databases;
                for (var name in dbs) {
                    var st = dbs[name];
                    var icon = st === 'ok' ? '<span class="status-ok">✓</span>'
                             : st === 'error' ? '<span class="status-error">✗</span>'
                             : '<span class="status-na">—</span>';
                    html += '<div class="notion-modal-item"><span>' + name + '</span>' + icon + '</div>';
                }
            }
            notionContent.innerHTML = html;

            // Update sidebar button
            if (data.api_key_configured) {
                var allOk = Object.values(data.databases).every(function(s) { return s === 'ok'; });
                var anyOk = Object.values(data.databases).some(function(s) { return s === 'ok'; });
                if (allOk) {
                    notionLabel.textContent = 'Notion: conectado ✓';
                    notionBtn.className = 'btn-notion-status connected';
                } else if (anyOk) {
                    notionLabel.textContent = 'Notion: parcial ⚠';
                    notionBtn.className = 'btn-notion-status partial';
                } else {
                    notionLabel.textContent = 'Notion: erro ✗';
                    notionBtn.className = 'btn-notion-status disconnected';
                }
            } else {
                notionLabel.textContent = 'Notion: não configurado';
                notionBtn.className = 'btn-notion-status disconnected';
            }
        } catch (_) {
            notionContent.innerHTML = '<p style="color:#dc2626;text-align:center;padding:12px 0">Erro ao verificar</p>';
        }
    });

    notionOverlay.addEventListener('click', function (e) {
        if (e.target === notionOverlay) notionOverlay.classList.remove('visible');
    });
    notionClose.addEventListener('click', function () {
        notionOverlay.classList.remove('visible');
    });

    // ---- Init ----
    inputEl.focus();
    updateSendButton();
    loadConversations();
    checkGoogleStatus();
})();
