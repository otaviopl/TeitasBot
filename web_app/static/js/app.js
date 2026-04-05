/* app.js — Chat interface logic */
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
    const attachBtn = document.getElementById('btn-attach');
    const fileInput = document.getElementById('file-input');
    const filePreview = document.getElementById('file-preview');
    const fileNameEl = document.getElementById('file-name');
    const removeFileBtn = document.getElementById('remove-file');
    const micBtn = document.getElementById('btn-mic');
    const toastEl = document.getElementById('toast');

    let pendingFile = null;
    let isSending = false;

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
        // Fallback: just escape HTML and wrap in <p>
        return '<p>' + text.replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</p>';
    }

    // ---- API helpers ----
    function authHeaders(extra) {
        return { 'Authorization': 'Bearer ' + token, ...extra };
    }

    async function apiPost(url, body, isFormData) {
        const headers = authHeaders(isFormData ? {} : { 'Content-Type': 'application/json' });
        const res = await fetch(url, {
            method: 'POST',
            headers,
            body: isFormData ? body : JSON.stringify(body),
        });
        if (res.status === 401) {
            localStorage.removeItem('pa_token');
            localStorage.removeItem('pa_user');
            window.location.href = '/';
            return null;
        }
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || 'Erro no servidor');
        }
        return res.json();
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

    function addMessage(role, content, imageUrls) {
        const div = document.createElement('div');
        div.className = 'message message-' + role;

        if (role === 'assistant') {
            div.innerHTML = renderMarkdown(content);
        } else {
            div.textContent = content;
        }

        // Insert before typing indicator
        messagesEl.insertBefore(div, typingEl);

        // Append images if any
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
        const hasText = inputEl.value.trim().length > 0;
        const hasFile = pendingFile !== null;
        sendBtn.disabled = (!hasText && !hasFile) || isSending;
    }

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
        const text = inputEl.value.trim();
        const file = pendingFile;

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

        try {
            let data;
            if (file) {
                const formData = new FormData();
                formData.append('file', file);
                formData.append('caption', text);
                data = await apiPost('/api/chat/upload', formData, true);
            } else {
                data = await apiPost('/api/chat', { message: text });
            }

            if (data) {
                addMessage('assistant', data.text, data.image_urls);
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
        const recorder = new AudioRecorder();

        micBtn.addEventListener('click', async function () {
            if (recorder.isRecording()) {
                micBtn.classList.remove('recording');
                const blob = await recorder.stop();
                if (blob && blob.size > 0) {
                    await sendAudio(blob);
                }
            } else {
                const started = await recorder.start();
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

        try {
            const formData = new FormData();
            formData.append('audio', blob, 'recording.webm');
            const data = await apiPost('/api/chat/audio', formData, true);

            if (data) {
                if (data.transcribed_text) {
                    // Update the user message to show what was said
                    var userMessages = messagesEl.querySelectorAll('.message-user');
                    var lastUserMsg = userMessages[userMessages.length - 1];
                    if (lastUserMsg) {
                        lastUserMsg.textContent = '🎙️ ' + data.transcribed_text;
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
            await apiPost('/api/chat/reset', {});
            // Clear messages from UI
            var messages = messagesEl.querySelectorAll('.message');
            messages.forEach(function (m) { m.remove(); });
            showToast('Nova conversa iniciada');
        } catch (err) {
            showToast('Erro ao resetar conversa');
        }
    });

    // ---- Logout ----
    logoutBtn.addEventListener('click', function () {
        localStorage.removeItem('pa_token');
        localStorage.removeItem('pa_user');
        window.location.href = '/';
    });

    // ---- Init ----
    inputEl.focus();
    updateSendButton();
})();
