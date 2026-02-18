/**
 * AIQueue — client-side AI analysis queue.
 *
 * Manages a FIFO queue of (job_id, prompt_id) analysis requests,
 * processing them one at a time so Ollama is never overloaded.
 *
 * Public API:
 *   AIQueue.trigger(jobId, jobTitle)   — entry point from "AI Analyse" button
 *   AIQueue.enqueue(...)               — add an item directly (used by picker)
 *   AIQueue.cancel()                   — abort the currently running analysis
 *   AIQueue.remove(jobId, promptId)    — remove a queued (not running) item
 *   AIQueue.reorderByIds(pairs)        — reorder queue from drag-drop result
 *   AIQueue.getState()                 — return {running, queue} snapshot
 *
 * Events dispatched on document:
 *   'aiqueue:update'  — fired after every state change; detail = getState()
 *
 * Dependencies (globals from app.js, loaded first):
 *   showToast(message, type)
 *   escapeHtml(str)
 *   _currentModalJobId
 */

const AIQueue = (() => {
    // ── Internal state ─────────────────────────────────────────
    // _running is null when idle, or the item object currently being processed.
    const _queue           = [];   // [{jobId, jobTitle, promptId, promptTitle, model}]
    let   _running         = null;
    let   _abortController = null;

    const _STORAGE_KEY = 'aiqueue_state';

    function _saveState() {
        try {
            sessionStorage.setItem(_STORAGE_KEY, JSON.stringify({
                running: _running,
                queue:   _queue.slice(),
            }));
        } catch (_) {}
    }

    function _clearSavedState() {
        try { sessionStorage.removeItem(_STORAGE_KEY); } catch (_) {}
    }

    // ── Helpers ────────────────────────────────────────────────

    /** Snapshot of current state (defensive copies). */
    function getState() {
        return {
            running: _running ? { ..._running } : null,
            queue:   _queue.map(item => ({ ...item })),
        };
    }

    /** Persist state and fire aiqueue:update + navbar badge refresh. */
    function _dispatch() {
        _saveState();
        _updateBadge();
        document.dispatchEvent(
            new CustomEvent('aiqueue:update', { detail: getState() })
        );
    }

    // ── Public: entry point ─────────────────────────────────────
    async function trigger(jobId, jobTitle) {
        if (!jobId) {
            showToast('No job selected', 'warning');
            return;
        }
        const title = (jobTitle || 'this job').trim();

        try {
            const resp    = await fetch('/api/ai-prompts');
            const data    = await resp.json();
            const prompts = data.prompts || [];

            if (prompts.length === 0) {
                showToast(
                    'No AI prompts configured. ' +
                    '<a href="/ai-prompts" class="fw-bold" style="color:inherit;">Create one here →</a>',
                    'warning'
                );
                return;
            }

            const active = prompts.find(
                p => p.is_active == 1 || p.is_active === true || p.is_active === '1'
            );

            if (active) {
                enqueue(jobId, title, active.id, active.title, active.model);
            } else {
                _showPicker(jobId, title, prompts);
            }
        } catch (err) {
            showToast('Failed to load AI prompts: ' + err.message, 'danger');
        }
    }

    // ── Public: add to queue ────────────────────────────────────
    function enqueue(jobId, jobTitle, promptId, promptTitle, model) {
        const duplicate = _queue.some(
            item => item.jobId === jobId && item.promptId == promptId
        );
        if (duplicate) {
            showToast(`Already queued: "${escapeHtml(jobTitle)}"`, 'info');
            return;
        }

        _queue.push({ jobId, jobTitle, promptId: parseInt(promptId), promptTitle, model });
        _dispatch();

        showToast(
            `<i class="bi bi-robot me-1"></i>Queued: <strong>${escapeHtml(jobTitle)}</strong>`,
            'info'
        );

        if (!_running) _processNext();
    }

    // ── Public: cancel running analysis ────────────────────────
    function cancel() {
        if (_abortController) {
            // Immediately persist state WITHOUT the running item so that if the
            // user navigates away right after clicking Cancel, the cancelled job
            // is not restored and re-run on the next page load.
            try {
                sessionStorage.setItem(_STORAGE_KEY, JSON.stringify({
                    running: null,
                    queue:   _queue.slice(),
                }));
            } catch (_) {}
            _abortController.abort();
        }
    }

    // ── Public: remove item from queue ──────────────────────────
    function remove(jobId, promptId) {
        const idx = _queue.findIndex(
            item => item.jobId === jobId && item.promptId == promptId
        );
        if (idx !== -1) {
            _queue.splice(idx, 1);
            _dispatch();
        }
    }

    // ── Public: reorder queue (from drag-drop) ──────────────────
    // pairs = [{jobId, promptId}, ...] in the new desired order
    function reorderByIds(pairs) {
        const reordered = [];
        pairs.forEach(({ jobId, promptId }) => {
            const item = _queue.find(
                q => q.jobId === jobId && q.promptId == promptId
            );
            if (item) reordered.push(item);
        });
        _queue.length = 0;
        reordered.forEach(item => _queue.push(item));
        _dispatch();
    }

    // ── Process queue ───────────────────────────────────────────
    async function _processNext() {
        if (_queue.length === 0) {
            _running         = null;
            _abortController = null;
            _clearSavedState();   // nothing left — remove stale storage entry
            _dispatch();
            return;
        }

        const item       = _queue.shift();
        _running         = item;
        _abortController = new AbortController();
        _dispatch();

        showToast(
            `<i class="bi bi-hourglass-split me-1"></i>` +
            `Analysing <strong>${escapeHtml(item.jobTitle)}</strong> ` +
            `with <span class="font-monospace">${escapeHtml(item.model || '?')}</span>…`,
            'secondary'
        );

        try {
            const resp = await fetch('/api/ai-analyse', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ job_id: item.jobId, prompt_id: item.promptId }),
                signal:  _abortController.signal,
            });

            const data = await resp.json();

            if (!resp.ok || data.error) {
                _showErrorToast(item.jobTitle, data);
            } else {
                _showSuccessToast(item, data);
            }
        } catch (err) {
            if (err.name === 'AbortError') {
                showToast(
                    `<i class="bi bi-slash-circle me-1"></i>Cancelled: <strong>${escapeHtml(item.jobTitle)}</strong>`,
                    'secondary'
                );
            } else {
                _showErrorToast(item.jobTitle, { error: err.message });
            }
        }

        _running         = null;
        _abortController = null;
        _dispatch();
        _processNext();
    }

    // ── Toasts ──────────────────────────────────────────────────

    function _showSuccessToast(item, data) {
        const score = data.match_score != null ? `${data.match_score}/10` : '–';
        const rec   = (data.recommendation || '').toLowerCase();

        const recColour = rec === 'apply' ? 'success'
                        : rec === 'maybe' ? 'warning'
                        : rec === 'skip'  ? 'danger'
                        : 'secondary';

        const recBadge = rec
            ? `<span class="badge bg-${recColour} ms-1">${rec.toUpperCase()}</span>`
            : '';

        const viewLink = data.analysis_id
            ? `<br><a href="/ai-analysis/${data.analysis_id}" class="fw-bold small" style="color:inherit;">View Analysis →</a>`
            : '';

        _showPersistentToast(
            `<i class="bi bi-robot me-1"></i><strong>Analysis complete</strong> — ` +
            `${escapeHtml(item.jobTitle)}<br>` +
            `<small>Score: <strong>${score}</strong>${recBadge}</small>` +
            viewLink,
            'success',
            30000
        );
    }

    function _showErrorToast(jobTitle, data) {
        let reason = data.error || 'Unknown error';
        if (data.validation_errors && data.validation_errors.length) {
            reason += '<br><small>' + data.validation_errors.map(escapeHtml).join('<br>') + '</small>';
        } else if (data.raw_preview) {
            const preview = escapeHtml(data.raw_preview.substring(0, 160));
            reason += `<br><small class="text-break font-monospace">Response: ${preview}…</small>`;
        }

        _showPersistentToast(
            `<i class="bi bi-x-circle me-1"></i><strong>Analysis failed</strong> — ` +
            `${escapeHtml(jobTitle)}<br><small>${reason}</small>`,
            'danger',
            20000
        );
    }

    function _showPersistentToast(html, type, duration = 0) {
        const container = document.getElementById('toastContainer');
        if (!container) return;

        const id = 'ai_toast_' + Date.now();
        container.insertAdjacentHTML('beforeend', `
        <div id="${id}" class="toast align-items-center text-bg-${type} border-0"
             role="alert" data-bs-autohide="${duration > 0}" data-bs-delay="${duration}">
            <div class="d-flex">
                <div class="toast-body lh-sm">${html}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto"
                        data-bs-dismiss="toast"></button>
            </div>
        </div>`);

        const el    = document.getElementById(id);
        const toast = new bootstrap.Toast(el, { autohide: duration > 0, delay: duration });
        toast.show();
        el.addEventListener('hidden.bs.toast', () => el.remove());
    }

    // ── Prompt picker modal ─────────────────────────────────────
    function _showPicker(jobId, jobTitle, prompts) {
        const titleEl = document.getElementById('promptPickerJobTitle');
        const listEl  = document.getElementById('promptPickerList');
        if (!titleEl || !listEl) return;

        titleEl.textContent = jobTitle;
        listEl.innerHTML = '';

        prompts.forEach(p => {
            const btn = document.createElement('button');
            btn.type      = 'button';
            btn.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-center py-3';
            btn.innerHTML = `
                <div>
                    <div class="fw-semibold">${escapeHtml(p.title)}</div>
                    <span class="badge bg-info text-dark font-monospace small mt-1">
                        ${escapeHtml(p.model || 'no model')}
                    </span>
                </div>
                <i class="bi bi-chevron-right text-muted"></i>`;

            btn.addEventListener('click', () => {
                const modal = bootstrap.Modal.getInstance(
                    document.getElementById('promptPickerModal')
                );
                if (modal) modal.hide();
                enqueue(jobId, jobTitle, p.id, p.title, p.model);
            });

            listEl.appendChild(btn);
        });

        let modal = bootstrap.Modal.getInstance(
            document.getElementById('promptPickerModal')
        );
        if (!modal) {
            modal = new bootstrap.Modal(document.getElementById('promptPickerModal'));
        }
        modal.show();
    }

    // ── Navbar badge ────────────────────────────────────────────
    function _updateBadge() {
        const badge   = document.getElementById('aiQueueBadge');
        const countEl = document.getElementById('aiQueueCount');
        if (!badge) return;

        const total = _queue.length + (_running ? 1 : 0);
        if (total > 0) {
            if (countEl) countEl.textContent = total;
            badge.style.display = 'inline-flex';
        } else {
            badge.style.display = 'none';
        }
    }

    // ── Restore queue after page navigation ────────────────────
    // sessionStorage survives navigation within a tab. Only pending queue items
    // are restored — the item that was "running" when the user left is NOT
    // re-run because: (a) the server-side Ollama call may still be in progress,
    // and (b) re-running it would launch a duplicate call. The user can
    // re-trigger it manually if needed once the page settles.
    document.addEventListener('DOMContentLoaded', () => {
        try {
            const raw = sessionStorage.getItem(_STORAGE_KEY);
            if (!raw) return;

            const saved = JSON.parse(raw);
            _clearSavedState();   // consume immediately — don't restore twice

            const toRestore = saved.queue || [];
            if (toRestore.length === 0) return;

            toRestore.forEach(item => _queue.push(item));
            _dispatch();
            _processNext();
        } catch (_) {}
    });

    // ── Public API ──────────────────────────────────────────────
    return { trigger, enqueue, cancel, remove, reorderByIds, getState };
})();
