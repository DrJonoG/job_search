/**
 * job_search – Frontend Logic
 */

// ── Global state for modal ───────────────────────────────────
let _currentModalJobId = null;
let _currentModalFav = false;
let _currentModalApplied = false;
let _currentModalNotInterested = false;

// ── Multi-select state ────────────────────────────────────────
const _selectedJobIds = new Set();

// ── Search overlay: current task id and abort flag for cancel ──
let _searchTaskId = null;
let _searchPollAborted = false;

// ── Theme Toggle ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    const html = document.documentElement;
    const toggle = document.getElementById('themeToggle');
    const saved = localStorage.getItem('theme') || 'dark';
    html.setAttribute('data-bs-theme', saved);
    updateThemeIcon(saved);

    if (toggle) {
        toggle.addEventListener('click', () => {
            const current = html.getAttribute('data-bs-theme');
            const next = current === 'dark' ? 'light' : 'dark';
            html.setAttribute('data-bs-theme', next);
            localStorage.setItem('theme', next);
            updateThemeIcon(next);
        });
    }

    // Max results slider
    const slider = document.getElementById('maxResults');
    const sliderVal = document.getElementById('maxResultsVal');
    if (slider && sliderVal) {
        slider.addEventListener('input', () => sliderVal.textContent = slider.value);
    }

    // Keywords help popover
    const keywordsHelpTrigger = document.getElementById('keywordsHelpTrigger');
    const keywordsHelpContent = document.getElementById('keywordsHelpContent');
    if (keywordsHelpTrigger && keywordsHelpContent) {
        const content = keywordsHelpContent.innerHTML.trim();
        new bootstrap.Popover(keywordsHelpTrigger, {
            content,
            html: true,
            trigger: 'click',
            placement: 'auto',
            container: 'body',
            customClass: 'keywords-help-popover-container',
        });
    }

    // Work type = Remote: disable location
    const remoteSelect = document.getElementById('remote');
    const locationInput = document.getElementById('location');
    const locationGroup = document.getElementById('locationGroup');
    const locationHint = document.getElementById('locationHint');
    if (remoteSelect && locationInput) {
        function updateLocationState() {
            const isRemote = remoteSelect.value === 'Remote';
            locationInput.disabled = isRemote;
            locationInput.placeholder = isRemote ? 'Not used for remote jobs' : 'e.g. London, New York';
            if (locationGroup) locationGroup.classList.toggle('field-disabled', isRemote);
            if (locationHint) locationHint.textContent = isRemote ? 'Location is not used when Work type is Remote.' : 'City, region, or country';
        }
        remoteSelect.addEventListener('change', updateLocationState);
        updateLocationState();
    }

    // Sources: select all / unselect all
    const selectAll = document.getElementById('selectAllSources');
    const unselectAll = document.getElementById('unselectAllSources');
    if (selectAll) {
        selectAll.addEventListener('click', () => {
            document.querySelectorAll('#searchForm .source-checkbox:not(:disabled)').forEach(cb => cb.checked = true);
            updateSearchButtonState();
        });
    }
    if (unselectAll) {
        unselectAll.addEventListener('click', () => {
            document.querySelectorAll('#searchForm .source-checkbox:not(:disabled)').forEach(cb => cb.checked = false);
            updateSearchButtonState();
        });
    }

    // Sources: any checkbox change updates button state and count
    const sourcesGrid = document.getElementById('sourcesGrid');
    if (sourcesGrid) {
        sourcesGrid.addEventListener('change', () => {
            updateSearchButtonState();
        });
    }

    // Initial search button state and count
    updateSearchButtonState();

    // Saved searches: load on page load and bind save button
    loadSavedSearches();
    const saveSearchBtn = document.getElementById('saveSearchBtn');
    if (saveSearchBtn) saveSearchBtn.addEventListener('click', handleSaveSearch);

    // Search form
    const form = document.getElementById('searchForm');
    if (form) form.addEventListener('submit', handleSearch);

    // Search overlay: Cancel and Close
    const cancelBtn = document.getElementById('searchOverlayCancel');
    if (cancelBtn) {
        cancelBtn.addEventListener('click', async () => {
            if (!_searchTaskId) return;
            cancelBtn.disabled = true;
            try {
                await fetch(`/api/search/${_searchTaskId}/cancel`, { method: 'POST' });
            } catch (e) { /* ignore */ }
            // Poll will soon get status 'cancelled' and show done state
        });
    }
    const closeBtn = document.getElementById('searchOverlayClose');
    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            hideSearchOverlay();
        });
    }

    // Filter buttons
    const clearBtn = document.getElementById('clearFilters');
    if (clearBtn) clearBtn.addEventListener('click', clearFilters);

    // Auto-apply filters: instant for dropdowns, debounced for text/number inputs
    const filterSelects = ['filterSource', 'filterRemote', 'filterJobType', 'filterPostedInLastDays', 'filterSort', 'filterOrder', 'filterRegion'];
    filterSelects.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => loadJobs(1));
    });

    let _filterDebounceTimer = null;
    function debouncedLoadJobs() {
        clearTimeout(_filterDebounceTimer);
        _filterDebounceTimer = setTimeout(() => loadJobs(1), 350);
    }

    const filterQuery = document.getElementById('filterQuery');
    if (filterQuery) filterQuery.addEventListener('input', debouncedLoadJobs);

    const filterSalaryMin = document.getElementById('filterSalaryMin');
    if (filterSalaryMin) filterSalaryMin.addEventListener('input', debouncedLoadJobs);

    const filterNI = document.getElementById('filterIncludeNotInterested');
    if (filterNI) filterNI.addEventListener('change', () => loadJobs(1));
});

function updateThemeIcon(theme) {
    const btn = document.getElementById('themeToggle');
    if (!btn) return;
    btn.innerHTML = theme === 'dark'
        ? '<i class="bi bi-sun-fill"></i>'
        : '<i class="bi bi-moon-fill"></i>';
}

/** Enable/disable search button based on at least one source selected; update sources count text. */
function updateSearchButtonState() {
    const searchBtn = document.getElementById('searchBtn');
    const countEl = document.getElementById('sourcesCount');
    const checkboxes = document.querySelectorAll('#searchForm .source-checkbox:not(:disabled)');
    const checked = document.querySelectorAll('#searchForm .source-checkbox:not(:disabled):checked');
    const n = checkboxes.length;
    const c = checked.length;

    if (searchBtn) {
        searchBtn.disabled = c === 0;
    }
    if (countEl) {
        if (c === 0) {
            countEl.textContent = 'Select at least one source to enable search.';
        } else {
            countEl.textContent = c === n ? `${c} of ${n} sources selected.` : `${c} of ${n} sources selected.`;
        }
    }
}

// ── Search ────────────────────────────────────────────────────
async function handleSearch(e) {
    e.preventDefault();

    const sources = [];
    document.querySelectorAll('#searchForm .source-checkbox:checked').forEach(cb => {
        sources.push(cb.value);
    });

    if (sources.length === 0) {
        showToast('Please select at least one source', 'warning');
        return;
    }

    const keywordsRaw = document.getElementById('keywords').value.trim();
    const keywords = keywordsRaw ? keywordsRaw.split(',').map(k => k.trim()).filter(k => k) : [];

    const locationEl = document.getElementById('location');
    const postedEl = document.getElementById('postedInLastDays');
    const payload = {
        keywords: keywords.length ? keywords.join(', ') : '',
        location: locationEl && !locationEl.disabled ? locationEl.value.trim() : '',
        remote: document.getElementById('remote').value,
        job_type: document.getElementById('jobType').value,
        experience_level: document.getElementById('experienceLevel').value,
        salary_min: document.getElementById('salaryMin').value || null,
        sources: sources,
        max_results_per_source: parseInt(document.getElementById('maxResults').value),
        posted_in_last_days: postedEl && postedEl.value ? parseInt(postedEl.value, 10) : null,
    };

    const btn = document.getElementById('searchBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Searching...';

    _searchPollAborted = false;
    showSearchOverlayRunning();
    document.getElementById('searchOverlay').style.display = 'flex';
    document.getElementById('searchOverlay').setAttribute('aria-hidden', 'false');

    try {
        const resp = await fetch('/api/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();

        if (data.error) {
            showToast(data.error, 'danger');
            hideSearchOverlay();
            resetSearchButton();
            return;
        }

        _searchTaskId = data.task_id;
        pollSearchProgress(data.task_id);
    } catch (err) {
        showToast('Failed to start search: ' + err.message, 'danger');
        hideSearchOverlay();
        resetSearchButton();
    }
}

function showSearchOverlayRunning() {
    document.getElementById('searchOverlayRunning').style.display = 'block';
    document.getElementById('searchOverlayDone').style.display = 'none';
    document.getElementById('progressBar').style.width = '0%';
    document.getElementById('searchOverlaySubtitle').textContent = 'Starting...';
    document.getElementById('progressDetails').innerHTML = '';
    const cancelBtn = document.getElementById('searchOverlayCancel');
    if (cancelBtn) cancelBtn.disabled = false;
    const browseBtn = document.getElementById('searchOverlayBrowse');
    if (browseBtn) browseBtn.style.display = 'inline-block';
}

function hideSearchOverlay() {
    const el = document.getElementById('searchOverlay');
    if (el) {
        el.style.display = 'none';
        el.setAttribute('aria-hidden', 'true');
    }
    _searchTaskId = null;
}

async function pollSearchProgress(taskId) {
    const progressBar = document.getElementById('progressBar');
    const progressDetails = document.getElementById('progressDetails');
    const subtitle = document.getElementById('searchOverlaySubtitle');

    const poll = async () => {
        if (_searchPollAborted) return;

        try {
            const resp = await fetch(`/api/search/${taskId}`);
            const task = await resp.json();

            if (task.error) {
                showToast(task.error, 'danger');
                hideSearchOverlay();
                resetSearchButton();
                return;
            }

            const pct = task.total_sources > 0
                ? Math.round((task.completed_sources / task.total_sources) * 100)
                : 0;
            progressBar.style.width = pct + '%';
            if (subtitle) {
                subtitle.textContent = task.status === 'running'
                    ? `${task.current_source || 'Starting'} (${task.completed_sources}/${task.total_sources} sources)`
                    : task.status;
            }

            let details = '';
            if (task.jobs_found !== undefined || task.new_jobs_saved !== undefined) {
                details += `<div class="d-flex justify-content-between mb-2 pb-2 border-bottom border-secondary border-opacity-25"><span class="text-muted">Jobs found</span><strong>${task.jobs_found ?? 0}</strong></div>`;
                details += `<div class="d-flex justify-content-between mb-2 pb-2 border-bottom border-secondary border-opacity-25"><span class="text-muted">New jobs saved</span><strong class="text-success">${task.new_jobs_saved ?? 0}</strong></div>`;
            }
            for (const [src, count] of Object.entries(task.source_results || {})) {
                details += `<div class="source-row"><span>${src}</span><span class="fw-semibold">${count} jobs</span></div>`;
            }
            progressDetails.innerHTML = details;

            if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
                showSearchOverlayDone(task);
                resetSearchButton();
                refreshStats();
                _searchTaskId = null;
                return;
            }

            setTimeout(poll, 1000);
        } catch (err) {
            if (!_searchPollAborted) setTimeout(poll, 2000);
        }
    };

    poll();
}

function showSearchOverlayDone(task) {
    document.getElementById('searchOverlayRunning').style.display = 'none';
    const doneEl = document.getElementById('searchOverlayDone');
    doneEl.style.display = 'block';

    const iconEl = document.getElementById('searchOverlayDoneIcon');
    const titleEl = document.getElementById('searchOverlayDoneTitle');
    const summaryEl = document.getElementById('searchOverlaySummary');
    const browseBtn = document.getElementById('searchOverlayBrowse');

    if (task.status === 'cancelled') {
        iconEl.className = 'search-overlay-icon search-overlay-icon-done warning';
        iconEl.innerHTML = '<i class="bi bi-x-lg"></i>';
        titleEl.textContent = 'Search cancelled';
        summaryEl.innerHTML = '<p class="text-muted mb-0">No new jobs were saved. You can run a new search anytime.</p>';
        browseBtn.style.display = 'none';
    } else {
        iconEl.className = 'search-overlay-icon search-overlay-icon-done success';
        iconEl.innerHTML = '<i class="bi bi-check-lg"></i>';
        titleEl.textContent = 'Search complete';
        let html = `
            <div class="d-flex justify-content-between mb-2"><span class="text-muted">Jobs found</span><strong>${task.jobs_found}</strong></div>
            <div class="d-flex justify-content-between mb-2"><span class="text-muted">New jobs saved</span><strong class="text-success">${task.new_jobs_saved}</strong></div>
            <div class="d-flex justify-content-between mb-2"><span class="text-muted">Time</span><strong>${task.elapsed_seconds}s</strong></div>
        `;
        if (task.source_results && Object.keys(task.source_results).length) {
            html += '<hr class="my-2"><h6 class="fw-bold small mb-2">By source</h6>';
            for (const [src, count] of Object.entries(task.source_results)) {
                html += `<div class="d-flex justify-content-between small mb-1"><span>${src}</span><span>${count}</span></div>`;
            }
        }
        if (task.errors && task.errors.length) {
            html += `<div class="mt-2 small text-warning"><i class="bi bi-exclamation-triangle me-1"></i>${task.errors.length} source(s) had errors</div>`;
        }
        summaryEl.innerHTML = html;
        browseBtn.style.display = 'inline-block';
        if (task.new_jobs_saved > 0) {
            showToast(`Search complete! ${task.new_jobs_saved} new jobs saved.`, 'success');
        } else {
            showToast('Search complete. No new jobs (all already saved).', 'info');
        }
    }
}

function resetSearchButton() {
    const btn = document.getElementById('searchBtn');
    if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-search me-2"></i> Start Search';
    }
}

async function refreshStats() {
    try {
        const resp = await fetch('/api/stats');
        const stats = await resp.json();
        const el = document.getElementById('statTotal');
        if (el) el.textContent = stats.total;
    } catch (e) { /* silent */ }
}

// ── Job Board ─────────────────────────────────────────────────
async function loadJobs(page = 1) {
    const listEl = document.getElementById('jobsList');
    const loadingEl = document.getElementById('jobsLoading');
    const emptyEl = document.getElementById('jobsEmpty');
    const paginationNav = document.getElementById('paginationNav');

    if (!listEl) return;

    clearJobSelection();
    listEl.innerHTML = '';
    loadingEl.style.display = 'block';
    emptyEl.style.display = 'none';
    paginationNav.style.display = 'none';

    const params = new URLSearchParams();
    params.set('page', page);
    params.set('per_page', 25);

    const q = document.getElementById('filterQuery')?.value || '';
    if (q) params.set('q', q);

    const source = document.getElementById('filterSource')?.value || '';
    if (source) params.set('source', source);

    const remote = document.getElementById('filterRemote')?.value || '';
    if (remote && remote !== 'Any') params.set('remote', remote);

    const jobType = document.getElementById('filterJobType')?.value || '';
    if (jobType) params.set('job_type', jobType);

    const salaryMin = document.getElementById('filterSalaryMin')?.value || '';
    if (salaryMin) params.set('salary_min', salaryMin);

    const postedInLastDays = document.getElementById('filterPostedInLastDays')?.value || '';
    if (postedInLastDays) params.set('posted_in_last_days', postedInLastDays);

    const region = document.getElementById('filterRegion')?.value || '';
    if (region) params.set('region', region);

    const includeNI = document.getElementById('filterIncludeNotInterested')?.checked;
    if (includeNI) params.set('include_not_interested', '1');

    const sortBy = document.getElementById('filterSort')?.value || 'date_posted';
    params.set('sort_by', sortBy);

    const order = document.getElementById('filterOrder')?.value || 'desc';
    params.set('order', order);

    try {
        const resp = await fetch(`/api/jobs?${params.toString()}`);
        const data = await resp.json();

        loadingEl.style.display = 'none';

        if (!data.jobs || data.jobs.length === 0) {
            emptyEl.style.display = 'block';
            const totalEl = document.getElementById('totalJobs');
            if (totalEl) totalEl.textContent = data.pagination?.total || 0;
            return;
        }

        const totalEl = document.getElementById('totalJobs');
        if (totalEl) totalEl.textContent = data.pagination.total;

        // Fetch favourite/applied statuses for this page of jobs
        const jobIds = data.jobs.map(j => j.job_id);
        const statuses = await fetchJobStatuses(jobIds);

        data.jobs.forEach((job, idx) => {
            const st = statuses[job.job_id] || {};
            listEl.innerHTML += renderJobCard(job, idx, !!st.is_favourite, !!st.is_applied, !!st.is_not_interested);
        });

        renderPagination(data.pagination);
    } catch (err) {
        loadingEl.style.display = 'none';
        listEl.innerHTML = `<div class="alert alert-danger">Failed to load jobs: ${err.message}</div>`;
    }
}

async function fetchJobStatuses(jobIds) {
    try {
        const resp = await fetch('/api/jobs/statuses', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_ids: jobIds }),
        });
        return await resp.json();
    } catch {
        return {};
    }
}

function renderJobCard(job, idx, isFav = false, isApplied = false, isNotInterested = false) {
    const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency);
    const remoteClass = (job.remote || '').toLowerCase() === 'remote' ? 'badge-remote'
        : (job.remote || '').toLowerCase() === 'hybrid' ? 'badge-hybrid' : 'badge-onsite';
    const sourceClass = (job.source || '').replace(/\s+/g, '');

    const tags = (job.tags || '').split(',')
        .map(t => t.trim())
        .filter(t => t)
        .slice(0, 5);

    const desc = stripHtmlTags(job.description || '').substring(0, 200);

    const favClass = isFav ? 'active' : '';
    const favIcon = isFav ? 'bi-heart-fill' : 'bi-heart';
    const appliedClass = isApplied ? 'active' : '';
    const appliedIcon = isApplied ? 'bi-send-check-fill' : 'bi-send';
    const niClass = isNotInterested ? 'active' : '';
    const niIcon = isNotInterested ? 'bi-eye-slash-fill' : 'bi-eye-slash';

    return `
    <div class="job-card fade-in${isNotInterested ? ' job-card-dimmed' : ''}" style="animation-delay: ${idx * 0.03}s" id="card-${job.job_id}">
        <div class="d-flex justify-content-between align-items-start">
            <div class="flex-grow-1" onclick="showJobDetail('${job.job_id}')" style="cursor:pointer">
                <div class="job-title">${escapeHtml(job.title)}</div>
                <div class="job-company">${escapeHtml(job.company)}</div>
            </div>
            <div class="d-flex align-items-center gap-2">
                <input type="checkbox" class="job-select-check form-check-input"
                       id="sel-${job.job_id}"
                       title="Select for bulk AI analysis"
                       onclick="event.stopPropagation(); toggleJobSelection('${job.job_id}', this)">
                <button class="action-btn ni-btn ${niClass}" onclick="toggleNotInterested('${job.job_id}', this)"
                        title="${isNotInterested ? 'Remove not interested' : 'Mark as not interested'}">
                    <i class="bi ${niIcon}"></i>
                </button>
                <button class="action-btn fav-btn ${favClass}" onclick="toggleFavourite('${job.job_id}', this)"
                        title="${isFav ? 'Remove from favourites' : 'Add to favourites'}">
                    <i class="bi ${favIcon}"></i>
                </button>
                <button class="action-btn applied-btn ${appliedClass}" onclick="toggleApplied('${job.job_id}', this)"
                        title="${isApplied ? 'Remove application' : 'Mark as applied'}">
                    <i class="bi ${appliedIcon}"></i>
                </button>
            </div>
        </div>
        <div class="job-meta" onclick="showJobDetail('${job.job_id}')" style="cursor:pointer">
            <span><i class="bi bi-geo-alt"></i>${escapeHtml(job.location || 'Not specified')}</span>
            <span class="badge ${remoteClass}">${escapeHtml(job.remote || 'Unknown')}</span>
            ${job.job_type ? `<span><i class="bi bi-clock"></i>${escapeHtml(job.job_type)}</span>` : ''}
            ${job.date_posted ? `<span><i class="bi bi-calendar3"></i>${formatDate(job.date_posted)}</span>` : ''}
            <span class="source-badge ${sourceClass}">${escapeHtml(job.source)}</span>
            ${salary ? `<span class="salary-badge">${salary}</span>` : ''}
        </div>
        ${desc ? `<div class="job-description-preview" onclick="showJobDetail('${job.job_id}')" style="cursor:pointer">${escapeHtml(desc)}</div>` : ''}
        ${tags.length > 0 ? `<div class="job-tags">${tags.map(t => `<span class="job-tag">${escapeHtml(t)}</span>`).join('')}</div>` : ''}
    </div>`;
}

// ── Multi-select helpers ──────────────────────────────────────

function _updateSelectionBar() {
    const bar = document.getElementById('bulkActionBar');
    if (!bar) return;
    const count = _selectedJobIds.size;
    const countEl = document.getElementById('bulkSelCount');
    if (countEl) countEl.textContent = `${count} job${count !== 1 ? 's' : ''} selected`;
    bar.classList.toggle('d-none', count === 0);
}

function toggleJobSelection(jobId, checkbox) {
    const id = String(jobId);
    if (checkbox.checked) {
        _selectedJobIds.add(id);
        document.getElementById(`card-${id}`)?.classList.add('job-card-selected');
    } else {
        _selectedJobIds.delete(id);
        document.getElementById(`card-${id}`)?.classList.remove('job-card-selected');
    }
    _updateSelectionBar();
}

function selectAllVisibleJobs() {
    document.querySelectorAll('.job-select-check').forEach(cb => {
        const id = cb.id.replace('sel-', '');
        if (!id) return;
        cb.checked = true;
        _selectedJobIds.add(id);
        document.getElementById(`card-${id}`)?.classList.add('job-card-selected');
    });
    _updateSelectionBar();
}

function clearJobSelection() {
    _selectedJobIds.clear();
    document.querySelectorAll('.job-select-check').forEach(cb => { cb.checked = false; });
    document.querySelectorAll('.job-card-selected').forEach(card => card.classList.remove('job-card-selected'));
    _updateSelectionBar();
}

async function analyseSelectedJobs() {
    if (_selectedJobIds.size === 0) return;

    const jobs = [..._selectedJobIds].map(jobId => {
        const cardEl = document.getElementById(`card-${jobId}`);
        const title  = cardEl?.querySelector('.job-title')?.textContent?.trim() || jobId;
        return { jobId, jobTitle: title };
    });

    // Clear immediately so the bar hides and won't be triggered twice
    clearJobSelection();
    await AIQueue.triggerMultiple(jobs);
}

function renderPagination(pagination) {
    const nav = document.getElementById('paginationNav');
    const info = document.getElementById('paginationInfo');
    const list = document.getElementById('paginationList');

    if (pagination.total_pages <= 1) {
        nav.style.display = 'none';
        info.textContent = `Showing all ${pagination.total} jobs`;
        nav.style.display = 'flex';
        list.innerHTML = '';
        return;
    }

    nav.style.display = 'flex';
    const start = (pagination.page - 1) * pagination.per_page + 1;
    const end = Math.min(pagination.page * pagination.per_page, pagination.total);
    info.textContent = `Showing ${start}–${end} of ${pagination.total} jobs`;

    let html = '';

    html += `<li class="page-item ${pagination.page <= 1 ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="loadJobs(${pagination.page - 1}); return false;">&laquo;</a>
    </li>`;

    const maxVisible = 7;
    let startPage = Math.max(1, pagination.page - Math.floor(maxVisible / 2));
    let endPage = Math.min(pagination.total_pages, startPage + maxVisible - 1);
    startPage = Math.max(1, endPage - maxVisible + 1);

    if (startPage > 1) {
        html += `<li class="page-item"><a class="page-link" href="#" onclick="loadJobs(1); return false;">1</a></li>`;
        if (startPage > 2) html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
    }

    for (let i = startPage; i <= endPage; i++) {
        html += `<li class="page-item ${i === pagination.page ? 'active' : ''}">
            <a class="page-link" href="#" onclick="loadJobs(${i}); return false;">${i}</a>
        </li>`;
    }

    if (endPage < pagination.total_pages) {
        if (endPage < pagination.total_pages - 1) html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
        html += `<li class="page-item"><a class="page-link" href="#" onclick="loadJobs(${pagination.total_pages}); return false;">${pagination.total_pages}</a></li>`;
    }

    html += `<li class="page-item ${pagination.page >= pagination.total_pages ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="loadJobs(${pagination.page + 1}); return false;">&raquo;</a>
    </li>`;

    list.innerHTML = html;
}

// ── Favourite / Applied Toggle ────────────────────────────────

async function toggleFavourite(jobId, btnEl) {
    const isActive = btnEl.classList.contains('active');
    const method = isActive ? 'DELETE' : 'POST';

    try {
        const resp = await fetch(`/api/favourite/${jobId}`, { method });
        const data = await resp.json();

        if (isActive) {
            btnEl.classList.remove('active');
            btnEl.querySelector('i').className = 'bi bi-heart';
            btnEl.title = 'Add to favourites';
            showToast('Removed from favourites', 'info');
        } else {
            btnEl.classList.add('active');
            btnEl.querySelector('i').className = 'bi bi-heart-fill';
            btnEl.title = 'Remove from favourites';
            showToast('Added to favourites', 'success');
        }
    } catch (err) {
        showToast('Failed to update favourite: ' + err.message, 'danger');
    }
}

async function toggleApplied(jobId, btnEl) {
    const isActive = btnEl.classList.contains('active');
    const method = isActive ? 'DELETE' : 'POST';

    try {
        const resp = await fetch(`/api/applied/${jobId}`, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: method === 'POST' ? JSON.stringify({}) : undefined,
        });
        const data = await resp.json();

        if (isActive) {
            btnEl.classList.remove('active');
            btnEl.querySelector('i').className = 'bi bi-send';
            btnEl.title = 'Mark as applied';
            showToast('Removed application status', 'info');
        } else {
            btnEl.classList.add('active');
            btnEl.querySelector('i').className = 'bi bi-send-check-fill';
            btnEl.title = 'Remove application';
            showToast('Marked as applied', 'success');
        }
    } catch (err) {
        showToast('Failed to update applied status: ' + err.message, 'danger');
    }
}

async function toggleNotInterested(jobId, btnEl) {
    const isActive = btnEl.classList.contains('active');
    const method = isActive ? 'DELETE' : 'POST';

    try {
        const resp = await fetch(`/api/not-interested/${jobId}`, { method });
        const data = await resp.json();

        if (isActive) {
            btnEl.classList.remove('active');
            btnEl.querySelector('i').className = 'bi bi-eye-slash';
            btnEl.title = 'Mark as not interested';
            btnEl.closest('.job-card')?.classList.remove('job-card-dimmed');
            showToast('Removed not interested status', 'info');
        } else {
            btnEl.classList.add('active');
            btnEl.querySelector('i').className = 'bi bi-eye-slash-fill';
            btnEl.title = 'Remove not interested';
            btnEl.closest('.job-card')?.classList.add('job-card-dimmed');
            showToast('Marked as not interested', 'success');
        }
    } catch (err) {
        showToast('Failed to update not interested: ' + err.message, 'danger');
    }
}

// ── Modal Favourite / Applied / Not Interested ────────────────

async function toggleFavouriteModal() {
    if (!_currentModalJobId) return;
    const btn = document.getElementById('jobModalFavBtn');
    const method = _currentModalFav ? 'DELETE' : 'POST';

    try {
        await fetch(`/api/favourite/${_currentModalJobId}`, { method });
        _currentModalFav = !_currentModalFav;
        updateModalFavBtn();

        // Update card button if visible
        const cardBtn = document.querySelector(`#card-${_currentModalJobId} .fav-btn`);
        if (cardBtn) {
            if (_currentModalFav) {
                cardBtn.classList.add('active');
                cardBtn.querySelector('i').className = 'bi bi-heart-fill';
            } else {
                cardBtn.classList.remove('active');
                cardBtn.querySelector('i').className = 'bi bi-heart';
            }
        }

        showToast(_currentModalFav ? 'Added to favourites' : 'Removed from favourites',
                  _currentModalFav ? 'success' : 'info');
    } catch (err) {
        showToast('Failed: ' + err.message, 'danger');
    }
}

async function toggleAppliedModal() {
    if (!_currentModalJobId) return;
    const method = _currentModalApplied ? 'DELETE' : 'POST';

    try {
        await fetch(`/api/applied/${_currentModalJobId}`, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: method === 'POST' ? JSON.stringify({}) : undefined,
        });
        _currentModalApplied = !_currentModalApplied;
        updateModalAppliedBtn();

        // Update card button if visible
        const cardBtn = document.querySelector(`#card-${_currentModalJobId} .applied-btn`);
        if (cardBtn) {
            if (_currentModalApplied) {
                cardBtn.classList.add('active');
                cardBtn.querySelector('i').className = 'bi bi-send-check-fill';
            } else {
                cardBtn.classList.remove('active');
                cardBtn.querySelector('i').className = 'bi bi-send';
            }
        }

        showToast(_currentModalApplied ? 'Marked as applied' : 'Removed application status',
                  _currentModalApplied ? 'success' : 'info');
    } catch (err) {
        showToast('Failed: ' + err.message, 'danger');
    }
}

function updateModalFavBtn() {
    const btn = document.getElementById('jobModalFavBtn');
    if (!btn) return;
    if (_currentModalFav) {
        btn.className = 'btn btn-danger';
        btn.innerHTML = '<i class="bi bi-heart-fill me-1"></i> Favourited';
    } else {
        btn.className = 'btn btn-outline-danger';
        btn.innerHTML = '<i class="bi bi-heart me-1"></i> Favourite';
    }
}

function updateModalAppliedBtn() {
    const btn = document.getElementById('jobModalAppliedBtn');
    if (!btn) return;
    if (_currentModalApplied) {
        btn.className = 'btn btn-success';
        btn.innerHTML = '<i class="bi bi-send-check-fill me-1"></i> Applied';
    } else {
        btn.className = 'btn btn-outline-success';
        btn.innerHTML = '<i class="bi bi-send me-1"></i> Applied';
    }
}

async function toggleNotInterestedModal() {
    if (!_currentModalJobId) return;
    const method = _currentModalNotInterested ? 'DELETE' : 'POST';

    try {
        await fetch(`/api/not-interested/${_currentModalJobId}`, { method });
        _currentModalNotInterested = !_currentModalNotInterested;
        updateModalNotInterestedBtn();

        const cardBtn = document.querySelector(`#card-${_currentModalJobId} .ni-btn`);
        if (cardBtn) {
            if (_currentModalNotInterested) {
                cardBtn.classList.add('active');
                cardBtn.querySelector('i').className = 'bi bi-eye-slash-fill';
                cardBtn.closest('.job-card')?.classList.add('job-card-dimmed');
            } else {
                cardBtn.classList.remove('active');
                cardBtn.querySelector('i').className = 'bi bi-eye-slash';
                cardBtn.closest('.job-card')?.classList.remove('job-card-dimmed');
            }
        }

        showToast(_currentModalNotInterested ? 'Marked as not interested' : 'Removed not interested status',
                  _currentModalNotInterested ? 'success' : 'info');
    } catch (err) {
        showToast('Failed: ' + err.message, 'danger');
    }
}

function updateModalNotInterestedBtn() {
    const btn = document.getElementById('jobModalNIBtn');
    if (!btn) return;
    if (_currentModalNotInterested) {
        btn.className = 'btn btn-secondary';
        btn.innerHTML = '<i class="bi bi-eye-slash-fill me-1"></i> Not Interested';
    } else {
        btn.className = 'btn btn-outline-secondary';
        btn.innerHTML = '<i class="bi bi-eye-slash me-1"></i> Not Interested';
    }
}

// ── Job Detail Modal ──────────────────────────────────────────
async function showJobDetail(jobId) {
    const modal = new bootstrap.Modal(document.getElementById('jobModal'));

    _currentModalJobId = jobId;
    _currentModalFav = false;
    _currentModalApplied = false;
    _currentModalNotInterested = false;

    document.getElementById('jobModalTitle').textContent = 'Loading...';
    document.getElementById('jobModalCompany').textContent = '';
    document.getElementById('jobModalBody').innerHTML = '<div class="text-center py-4"><div class="spinner-border text-primary"></div></div>';
    document.getElementById('jobModalApply').href = '#';

    updateModalFavBtn();
    updateModalAppliedBtn();
    updateModalNotInterestedBtn();

    modal.show();

    try {
        const resp = await fetch(`/api/jobs/${jobId}`);
        const job = await resp.json();

        if (job.error) {
            document.getElementById('jobModalBody').innerHTML = `<div class="alert alert-danger">${job.error}</div>`;
            return;
        }

        // Update favourite/applied/not-interested state from API
        _currentModalFav = !!job.is_favourite && job.is_favourite !== '' && job.is_favourite !== 0;
        _currentModalApplied = !!job.is_applied && job.is_applied !== '' && job.is_applied !== 0;
        _currentModalNotInterested = !!job.is_not_interested && job.is_not_interested !== '' && job.is_not_interested !== 0;
        updateModalFavBtn();
        updateModalAppliedBtn();
        updateModalNotInterestedBtn();

        document.getElementById('jobModalTitle').textContent = job.title || 'Untitled';
        document.getElementById('jobModalCompany').textContent = job.company || '';
        document.getElementById('jobModalApply').href = job.url || '#';

        const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency);
        const remoteClass = (job.remote || '').toLowerCase() === 'remote' ? 'badge-remote'
            : (job.remote || '').toLowerCase() === 'hybrid' ? 'badge-hybrid' : 'badge-onsite';

        let body = `
            <div class="detail-section">
                <div class="d-flex flex-wrap gap-2 mb-3">
                    <span class="badge ${remoteClass}">${escapeHtml(job.remote || 'Unknown')}</span>
                    ${job.job_type ? `<span class="badge bg-secondary">${escapeHtml(job.job_type)}</span>` : ''}
                    ${job.experience_level ? `<span class="badge bg-info">${escapeHtml(job.experience_level)}</span>` : ''}
                    ${salary ? `<span class="salary-badge">${salary}</span>` : ''}
                    <span class="source-badge ${(job.source || '').replace(/\s+/g, '')}">${escapeHtml(job.source)}</span>
                </div>
                <div class="row g-3">
                    <div class="col-sm-6">
                        <div class="detail-label">Location</div>
                        <div><i class="bi bi-geo-alt me-1 text-primary"></i>${escapeHtml(job.location || 'Not specified')}</div>
                    </div>
                    <div class="col-sm-6">
                        <div class="detail-label">Date Posted</div>
                        <div><i class="bi bi-calendar3 me-1 text-primary"></i>${formatDate(job.date_posted) || 'Not specified'}</div>
                    </div>
                </div>
            </div>
        `;

        if (job.description) {
            const descHasHtml = /<[a-z][\s\S]*>/i.test(job.description);
            const descClass = descHasHtml ? 'job-description-rendered' : 'job-description-rendered job-description-plaintext';
            body += `
                <div class="detail-section">
                    <div class="detail-label">Description</div>
                    <div class="${descClass}">
                        ${descHasHtml ? job.description : escapeHtml(job.description)}
                    </div>
                </div>
            `;
        }

        if (job.tags) {
            const tags = job.tags.split(',').map(t => t.trim()).filter(t => t);
            if (tags.length > 0) {
                body += `
                    <div class="detail-section">
                        <div class="detail-label">Tags / Skills</div>
                        <div class="d-flex flex-wrap gap-2 mt-1">
                            ${tags.map(t => `<span class="badge bg-primary bg-opacity-10 text-primary">${escapeHtml(t)}</span>`).join('')}
                        </div>
                    </div>
                `;
            }
        }

        body += `
            <div class="detail-section">
                <div class="detail-label">Metadata</div>
                <div class="small text-muted">
                    <div>Source: ${escapeHtml(job.source)}</div>
                    <div>Scraped: ${formatDate(job.date_scraped)}</div>
                    <div>ID: ${escapeHtml(job.job_id)}</div>
                </div>
            </div>
        `;

        document.getElementById('jobModalBody').innerHTML = body;
    } catch (err) {
        document.getElementById('jobModalBody').innerHTML = `<div class="alert alert-danger">Failed to load: ${err.message}</div>`;
    }
}

// ── Favourites Page ───────────────────────────────────────────
async function loadFavourites() {
    const listEl = document.getElementById('favList');
    const loadingEl = document.getElementById('favLoading');
    const emptyEl = document.getElementById('favEmpty');

    if (!listEl) return;

    clearJobSelection();
    try {
        const resp = await fetch('/api/favourites');
        const data = await resp.json();

        loadingEl.style.display = 'none';

        if (!data.jobs || data.jobs.length === 0) {
            emptyEl.style.display = 'block';
            return;
        }

        const totalEl = document.getElementById('totalFavourites');
        if (totalEl) totalEl.textContent = data.total;

        data.jobs.forEach((job, idx) => {
            listEl.innerHTML += renderJobCard(job, idx, true, !!job.is_applied, false);
        });
    } catch (err) {
        loadingEl.style.display = 'none';
        listEl.innerHTML = `<div class="alert alert-danger">Failed to load favourites: ${err.message}</div>`;
    }
}

// ── Applied Page ──────────────────────────────────────────────
async function loadApplications() {
    const listEl = document.getElementById('appliedList');
    const loadingEl = document.getElementById('appliedLoading');
    const emptyEl = document.getElementById('appliedEmpty');

    if (!listEl) return;

    clearJobSelection();
    try {
        const resp = await fetch('/api/applications');
        const data = await resp.json();

        loadingEl.style.display = 'none';

        if (!data.jobs || data.jobs.length === 0) {
            emptyEl.style.display = 'block';
            return;
        }

        const totalEl = document.getElementById('totalApplied');
        if (totalEl) totalEl.textContent = data.total;

        data.jobs.forEach((job, idx) => {
            let card = renderJobCard(job, idx, !!job.is_favourite, true, false);
            // Add applied date and notes badge
            const appliedAt = job.applied_at ? formatDate(job.applied_at) : '';
            const notes = job.application_notes || '';
            let extra = '';
            if (appliedAt) extra += `<span class="badge bg-success bg-opacity-10 text-success"><i class="bi bi-calendar-check me-1"></i>Applied ${appliedAt}</span>`;
            if (notes) extra += ` <span class="badge bg-info bg-opacity-10 text-info"><i class="bi bi-sticky me-1"></i>${escapeHtml(notes.substring(0, 80))}</span>`;
            if (extra) {
                card = card.replace('</div><!-- end-card -->', `<div class="mt-2">${extra}</div></div>`);
            }
            listEl.innerHTML += card;
        });
    } catch (err) {
        loadingEl.style.display = 'none';
        listEl.innerHTML = `<div class="alert alert-danger">Failed to load applications: ${err.message}</div>`;
    }
}

// ── Filter Helpers ────────────────────────────────────────────
function clearFilters() {
    const ids = ['filterQuery', 'filterSource', 'filterRemote', 'filterJobType', 'filterSalaryMin', 'filterPostedInLastDays', 'filterRegion'];
    ids.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = el.tagName === 'SELECT' ? el.options[0].value : '';
    });
    document.getElementById('filterSort').value = 'date_posted';
    document.getElementById('filterOrder').value = 'desc';
    const niCheckbox = document.getElementById('filterIncludeNotInterested');
    if (niCheckbox) niCheckbox.checked = false;
    loadJobs(1);
}

// ── Utility ───────────────────────────────────────────────────
function formatSalary(min, max, currency) {
    if (!min && !max) return '';
    const cur = currency || '';
    const fmt = (v) => {
        const n = parseFloat(v);
        if (isNaN(n) || n <= 0) return null;
        return n >= 1000 ? `${Math.round(n / 1000)}k` : n.toString();
    };
    const fmin = fmt(min);
    const fmax = fmt(max);
    if (fmin && fmax) return `${cur} ${fmin}–${fmax}`;
    if (fmin) return `${cur} ${fmin}+`;
    if (fmax) return `Up to ${cur} ${fmax}`;
    return '';
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    try {
        const d = new Date(dateStr);
        if (isNaN(d.getTime())) return dateStr.substring(0, 10);
        return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
    } catch {
        return dateStr.substring(0, 10);
    }
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function stripHtmlTags(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.innerHTML = str;
    return div.textContent || div.innerText || '';
}

async function loadRegions() {
    const select = document.getElementById('filterRegion');
    if (!select) return;
    try {
        const resp = await fetch('/api/regions');
        const data = await resp.json();
        if (data.regions && data.regions.length) {
            data.regions.forEach(r => {
                const opt = document.createElement('option');
                opt.value = r;
                opt.textContent = r.split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
                select.appendChild(opt);
            });
        }
    } catch (e) { /* silent */ }
}

// ── Saved Searches ────────────────────────────────────────────

/** Gather current search form values into a params object. */
function gatherSearchParams() {
    const sources = [];
    document.querySelectorAll('#searchForm .source-checkbox:checked').forEach(cb => {
        sources.push(cb.value);
    });

    const locationEl = document.getElementById('location');
    return {
        keywords: (document.getElementById('keywords')?.value || '').trim(),
        location: locationEl && !locationEl.disabled ? locationEl.value.trim() : '',
        remote: document.getElementById('remote')?.value || 'Any',
        job_type: document.getElementById('jobType')?.value || '',
        experience_level: document.getElementById('experienceLevel')?.value || '',
        salary_min: document.getElementById('salaryMin')?.value || '',
        max_results_per_source: parseInt(document.getElementById('maxResults')?.value || '200'),
        posted_in_last_days: document.getElementById('postedInLastDays')?.value || '',
        sources: sources,
    };
}

/** Apply saved params into the search form fields. */
function applySearchParams(params) {
    if (!params) return;

    const keywordsEl = document.getElementById('keywords');
    if (keywordsEl) keywordsEl.value = params.keywords || '';

    const locationEl = document.getElementById('location');
    if (locationEl) locationEl.value = params.location || '';

    const remoteEl = document.getElementById('remote');
    if (remoteEl) {
        remoteEl.value = params.remote || 'Any';
        remoteEl.dispatchEvent(new Event('change'));
    }

    const jobTypeEl = document.getElementById('jobType');
    if (jobTypeEl) jobTypeEl.value = params.job_type || '';

    const expEl = document.getElementById('experienceLevel');
    if (expEl) expEl.value = params.experience_level || '';

    const salaryEl = document.getElementById('salaryMin');
    if (salaryEl) salaryEl.value = params.salary_min || '';

    const maxResultsEl = document.getElementById('maxResults');
    const maxResultsVal = document.getElementById('maxResultsVal');
    if (maxResultsEl) {
        maxResultsEl.value = params.max_results_per_source || 200;
        if (maxResultsVal) maxResultsVal.textContent = maxResultsEl.value;
    }

    const postedEl = document.getElementById('postedInLastDays');
    if (postedEl) postedEl.value = params.posted_in_last_days || '';

    // Sources: uncheck all, then check the saved ones
    if (params.sources && Array.isArray(params.sources)) {
        document.querySelectorAll('#searchForm .source-checkbox:not(:disabled)').forEach(cb => {
            cb.checked = params.sources.includes(cb.value);
        });
    }

    updateSearchButtonState();
}

/** Prompt user for a name and save current search params. */
async function handleSaveSearch() {
    const name = prompt('Name this search:');
    if (!name || !name.trim()) return;

    const params = gatherSearchParams();

    try {
        const resp = await fetch('/api/saved-searches', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name.trim(), params }),
        });
        const data = await resp.json();
        if (data.error) {
            showToast(data.error, 'danger');
            return;
        }
        showToast('Search saved!', 'success');
        loadSavedSearches();
    } catch (err) {
        showToast('Failed to save search: ' + err.message, 'danger');
    }
}

/** Load saved search by id and apply to the form. */
async function loadSavedSearch(searchId) {
    try {
        const resp = await fetch(`/api/saved-searches/${searchId}`);
        const data = await resp.json();
        if (data.error) {
            showToast(data.error, 'danger');
            return;
        }
        applySearchParams(data.params);
        showToast(`Loaded: ${data.name}`, 'success');

        // Highlight the active chip
        document.querySelectorAll('.saved-search-chip').forEach(el => el.classList.remove('active'));
        const chip = document.getElementById(`saved-search-${searchId}`);
        if (chip) chip.classList.add('active');
    } catch (err) {
        showToast('Failed to load search: ' + err.message, 'danger');
    }
}

/** Delete a saved search. */
async function deleteSavedSearch(searchId, event) {
    event.stopPropagation();
    if (!confirm('Delete this saved search?')) return;

    try {
        const resp = await fetch(`/api/saved-searches/${searchId}`, { method: 'DELETE' });
        const data = await resp.json();
        if (data.error) {
            showToast(data.error, 'danger');
            return;
        }
        showToast('Saved search deleted', 'info');
        loadSavedSearches();
    } catch (err) {
        showToast('Failed to delete: ' + err.message, 'danger');
    }
}

/** Fetch and render the saved searches row. */
async function loadSavedSearches() {
    const row = document.getElementById('savedSearchesRow');
    const list = document.getElementById('savedSearchesList');
    if (!row || !list) return;

    try {
        const resp = await fetch('/api/saved-searches');
        const data = await resp.json();

        if (!data.searches || data.searches.length === 0) {
            row.style.display = 'none';
            return;
        }

        row.style.display = 'block';
        list.innerHTML = data.searches.map(s => {
            const p = s.params || {};
            const kw = p.keywords || 'All jobs';
            const loc = p.location ? ` \u00B7 ${p.location}` : '';
            const remote = p.remote && p.remote !== 'Any' ? ` \u00B7 ${p.remote}` : '';
            const srcCount = (p.sources || []).length;
            const subtitle = `${srcCount} source${srcCount !== 1 ? 's' : ''}${loc}${remote}`;

            return `
                <div class="saved-search-chip" id="saved-search-${s.id}" onclick="loadSavedSearch(${s.id})" title="${escapeHtml(kw)}">
                    <div class="saved-search-chip-content">
                        <span class="saved-search-chip-name">${escapeHtml(s.name)}</span>
                        <span class="saved-search-chip-meta">${escapeHtml(subtitle)}</span>
                    </div>
                    <button class="saved-search-chip-delete" onclick="deleteSavedSearch(${s.id}, event)" title="Delete saved search">
                        <i class="bi bi-x"></i>
                    </button>
                </div>
            `;
        }).join('');
    } catch (err) {
        row.style.display = 'none';
    }
}

// ── Saved Board Searches ──────────────────────────────────────

/** Gather current board filter values into a params object. */
function gatherBoardFilterParams() {
    return {
        query: (document.getElementById('filterQuery')?.value || '').trim(),
        source: document.getElementById('filterSource')?.value || '',
        remote: document.getElementById('filterRemote')?.value || '',
        job_type: document.getElementById('filterJobType')?.value || '',
        salary_min: document.getElementById('filterSalaryMin')?.value || '',
        posted_in_last_days: document.getElementById('filterPostedInLastDays')?.value || '',
        region: document.getElementById('filterRegion')?.value || '',
        include_not_interested: document.getElementById('filterIncludeNotInterested')?.checked || false,
        sort_by: document.getElementById('filterSort')?.value || 'date_posted',
        order: document.getElementById('filterOrder')?.value || 'desc',
    };
}

/** Apply saved board params into the filter fields and reload jobs. */
function applyBoardFilterParams(params) {
    if (!params) return;

    const queryEl = document.getElementById('filterQuery');
    if (queryEl) queryEl.value = params.query || '';

    const sourceEl = document.getElementById('filterSource');
    if (sourceEl) sourceEl.value = params.source || '';

    const remoteEl = document.getElementById('filterRemote');
    if (remoteEl) remoteEl.value = params.remote || '';

    const jobTypeEl = document.getElementById('filterJobType');
    if (jobTypeEl) jobTypeEl.value = params.job_type || '';

    const salaryEl = document.getElementById('filterSalaryMin');
    if (salaryEl) salaryEl.value = params.salary_min || '';

    const postedEl = document.getElementById('filterPostedInLastDays');
    if (postedEl) postedEl.value = params.posted_in_last_days || '';

    const regionEl = document.getElementById('filterRegion');
    if (regionEl) regionEl.value = params.region || '';

    const niEl = document.getElementById('filterIncludeNotInterested');
    if (niEl) niEl.checked = !!params.include_not_interested;

    const sortEl = document.getElementById('filterSort');
    if (sortEl) sortEl.value = params.sort_by || 'date_posted';

    const orderEl = document.getElementById('filterOrder');
    if (orderEl) orderEl.value = params.order || 'desc';

    loadJobs(1);
}

/** Prompt user for a name and save current board filter params. */
async function handleSaveBoardSearch() {
    const name = prompt('Name this board search:');
    if (!name || !name.trim()) return;

    const params = gatherBoardFilterParams();

    try {
        const resp = await fetch('/api/saved-board-searches', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name.trim(), params }),
        });
        const data = await resp.json();
        if (data.error) {
            showToast(data.error, 'danger');
            return;
        }
        showToast('Board search saved!', 'success');
        loadSavedBoardSearches();
    } catch (err) {
        showToast('Failed to save board search: ' + err.message, 'danger');
    }
}

/** Load a saved board search by id and apply to the filters. */
async function loadSavedBoardSearch(searchId) {
    try {
        const resp = await fetch(`/api/saved-board-searches/${searchId}`);
        const data = await resp.json();
        if (data.error) {
            showToast(data.error, 'danger');
            return;
        }
        applyBoardFilterParams(data.params);
        showToast(`Loaded: ${data.name}`, 'success');

        document.querySelectorAll('#savedBoardSearchesList .saved-search-chip').forEach(el => el.classList.remove('active'));
        const chip = document.getElementById(`saved-board-search-${searchId}`);
        if (chip) chip.classList.add('active');
    } catch (err) {
        showToast('Failed to load board search: ' + err.message, 'danger');
    }
}

/** Delete a saved board search. */
async function deleteSavedBoardSearch(searchId, event) {
    event.stopPropagation();
    if (!confirm('Delete this saved board search?')) return;

    try {
        const resp = await fetch(`/api/saved-board-searches/${searchId}`, { method: 'DELETE' });
        const data = await resp.json();
        if (data.error) {
            showToast(data.error, 'danger');
            return;
        }
        showToast('Saved board search deleted', 'info');
        loadSavedBoardSearches();
    } catch (err) {
        showToast('Failed to delete: ' + err.message, 'danger');
    }
}

/** Fetch and render the saved board searches row. */
async function loadSavedBoardSearches() {
    const row = document.getElementById('savedBoardSearchesRow');
    const list = document.getElementById('savedBoardSearchesList');
    if (!row || !list) return;

    try {
        const resp = await fetch('/api/saved-board-searches');
        const data = await resp.json();

        if (!data.searches || data.searches.length === 0) {
            row.style.display = 'none';
            return;
        }

        row.style.display = 'block';
        list.innerHTML = data.searches.map(s => {
            const p = s.params || {};
            const q = p.query || 'All jobs';
            const src = p.source ? ` \u00B7 ${p.source}` : '';
            const remote = p.remote && p.remote !== 'Any' && p.remote !== '' ? ` \u00B7 ${p.remote}` : '';
            const subtitle = `${q}${src}${remote}`;

            return `
                <div class="saved-search-chip" id="saved-board-search-${s.id}" onclick="loadSavedBoardSearch(${s.id})" title="${escapeHtml(subtitle)}">
                    <div class="saved-search-chip-content">
                        <span class="saved-search-chip-name">${escapeHtml(s.name)}</span>
                        <span class="saved-search-chip-meta">${escapeHtml(subtitle)}</span>
                    </div>
                    <button class="saved-search-chip-delete" onclick="deleteSavedBoardSearch(${s.id}, event)" title="Delete saved board search">
                        <i class="bi bi-x"></i>
                    </button>
                </div>
            `;
        }).join('');
    } catch (err) {
        row.style.display = 'none';
    }
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const icons = {
        success: 'bi-check-circle-fill',
        danger: 'bi-x-circle-fill',
        warning: 'bi-exclamation-triangle-fill',
        info: 'bi-info-circle-fill',
    };

    const id = 'toast_' + Date.now();
    const html = `
    <div class="toast align-items-center text-bg-${type} border-0" id="${id}" role="alert">
        <div class="d-flex">
            <div class="toast-body">
                <i class="bi ${icons[type] || icons.info} me-2"></i>${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>
    </div>`;

    container.insertAdjacentHTML('beforeend', html);
    const toastEl = document.getElementById(id);
    const toast = new bootstrap.Toast(toastEl, { delay: 5000 });
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}
