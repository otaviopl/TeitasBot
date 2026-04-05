/* audio.js — MediaRecorder wrapper for voice messages */
var AudioRecorder = (function () {
    'use strict';

    function AudioRecorder() {
        this._mediaRecorder = null;
        this._chunks = [];
        this._stream = null;
        this._recording = false;
    }

    AudioRecorder.prototype.isRecording = function () {
        return this._recording;
    };

    AudioRecorder.prototype.start = async function () {
        try {
            this._stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            var mimeType = 'audio/webm;codecs=opus';
            if (!MediaRecorder.isTypeSupported(mimeType)) {
                mimeType = 'audio/webm';
            }
            if (!MediaRecorder.isTypeSupported(mimeType)) {
                mimeType = '';
            }

            var options = mimeType ? { mimeType: mimeType } : {};
            this._mediaRecorder = new MediaRecorder(this._stream, options);
            this._chunks = [];
            var self = this;

            this._mediaRecorder.ondataavailable = function (e) {
                if (e.data && e.data.size > 0) {
                    self._chunks.push(e.data);
                }
            };

            this._mediaRecorder.start();
            this._recording = true;
            return true;
        } catch (err) {
            console.warn('Microphone access denied:', err);
            this._cleanup();
            return false;
        }
    };

    AudioRecorder.prototype.stop = function () {
        var self = this;
        return new Promise(function (resolve) {
            if (!self._mediaRecorder || self._mediaRecorder.state === 'inactive') {
                self._cleanup();
                resolve(null);
                return;
            }

            self._mediaRecorder.onstop = function () {
                var blob = new Blob(self._chunks, { type: 'audio/webm' });
                self._cleanup();
                resolve(blob);
            };

            self._mediaRecorder.stop();
            self._recording = false;
        });
    };

    AudioRecorder.prototype._cleanup = function () {
        this._recording = false;
        this._chunks = [];
        if (this._stream) {
            this._stream.getTracks().forEach(function (t) { t.stop(); });
            this._stream = null;
        }
        this._mediaRecorder = null;
    };

    return AudioRecorder;
})();
