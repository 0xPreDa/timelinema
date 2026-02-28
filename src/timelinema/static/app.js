/* Timelinema - Frontend logic */

const state = {
    commands: [],
    sessions: [],
    sessionColors: {},
    searchQuery: '',
    matchedEntries: [],
    matchIndex: -1,
    hideNoOutput: false,
    tzOffset: localStorage.getItem('timelinema-tz-offset') !== null
        ? parseFloat(localStorage.getItem('timelinema-tz-offset'))
        : -(new Date().getTimezoneOffset() / 60),
};

/* Generate N visually distinct colors spread across the hue wheel */
function generateSessionColors(count) {
    const colors = [];
    for (let i = 0; i < count; i++) {
        const hue = Math.round((i * 360) / count) % 360;
        colors.push(`hsl(${hue}, 70%, 55%)`);
    }
    return colors;
}

/* Init */
document.addEventListener('DOMContentLoaded', init);

async function init() {
    loadTheme();
    initTimezoneSelect();
    document.getElementById('theme-toggle').addEventListener('click', toggleTheme);
    document.getElementById('search').addEventListener('input', debounce(handleSearch, 200));
    document.getElementById('search-prev').addEventListener('click', () => navigateMatch(-1));
    document.getElementById('search-next').addEventListener('click', () => navigateMatch(1));
    document.getElementById('search').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); navigateMatch(e.shiftKey ? -1 : 1); }
    });
    document.getElementById('hide-no-output').addEventListener('click', toggleHideNoOutput);
    document.getElementById('session-filter').addEventListener('change', handleSessionFilter);
    document.getElementById('reload-btn').addEventListener('click', handleReload);
    document.getElementById('upload-btn').addEventListener('click', () => document.getElementById('upload-input').click());
    document.getElementById('upload-input').addEventListener('change', handleUploadInput);
    initDragDrop();

    await Promise.all([loadSessions(), loadTimeline()]);
}

/* Theme */
function loadTheme() {
    const saved = localStorage.getItem('timelinema-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    updateThemeButton(saved);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('timelinema-theme', next);
    updateThemeButton(next);
}

function updateThemeButton(theme) {
    document.getElementById('theme-toggle').textContent = theme === 'dark' ? '☀️' : '🌙';
}

/* Timezone */
function getLocalUtcOffset() {
    return -(new Date().getTimezoneOffset() / 60);
}

function offsetLabel(h) {
    if (h === 0) return 'UTC';
    const sign = h > 0 ? '+' : '\u2212';
    const abs = Math.abs(h);
    if (Number.isInteger(abs)) return `UTC${sign}${abs}`;
    const hours = Math.floor(abs);
    const minutes = (abs - hours) * 60;
    return `UTC${sign}${hours}:${String(minutes).padStart(2, '0')}`;
}

function initTimezoneSelect() {
    const select = document.getElementById('tz-select');
    const localOffset = getLocalUtcOffset();

    // Generate offsets from UTC-12 to UTC+14 (whole + common halves)
    const offsets = [];
    for (let h = -12; h <= 14; h++) offsets.push(h);
    [5.5, 5.75, 3.5, 4.5, 9.5, 10.5, -3.5, -9.5, 6.5, 8.75, 12.75].forEach(h => {
        if (!offsets.includes(h)) offsets.push(h);
    });
    offsets.sort((a, b) => a - b);

    offsets.forEach(h => {
        const opt = document.createElement('option');
        opt.value = h;
        const label = offsetLabel(h);
        opt.textContent = h === localOffset ? `${label} (local)` : label;
        if (h === state.tzOffset) opt.selected = true;
        select.appendChild(opt);
    });

    select.addEventListener('change', () => {
        state.tzOffset = parseFloat(select.value);
        localStorage.setItem('timelinema-tz-offset', state.tzOffset);
        renderTimeline();
        applySearch();
    });
}

function applyOffset(epoch) {
    // Convert epoch to a Date shifted by the selected UTC offset
    return new Date((epoch + state.tzOffset * 3600) * 1000);
}

function formatTsDate(epoch) {
    const dt = applyOffset(epoch);
    const y = dt.getUTCFullYear();
    const m = String(dt.getUTCMonth() + 1).padStart(2, '0');
    const d = String(dt.getUTCDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
}

function formatTsTime(epoch) {
    const dt = applyOffset(epoch);
    const h = String(dt.getUTCHours()).padStart(2, '0');
    const m = String(dt.getUTCMinutes()).padStart(2, '0');
    const s = String(dt.getUTCSeconds()).padStart(2, '0');
    return `${h}:${m}:${s}`;
}

/* Sessions */
async function loadSessions() {
    const res = await fetch('/api/sessions');
    state.sessions = await res.json();

    const colors = generateSessionColors(state.sessions.length);
    state.sessions.forEach((s, i) => {
        state.sessionColors[s.id] = colors[i];
    });

    const select = document.getElementById('session-filter');
    select.innerHTML = '<option value="">All sessions</option>';
    state.sessions.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = `${s.title || s.filename} (${s.command_count})`;
        select.appendChild(opt);
    });
}

/* Timeline */
async function loadTimeline() {
    const params = new URLSearchParams();
    params.set('per_page', '2000');

    const sessionFilter = document.getElementById('session-filter').value;
    if (sessionFilter) params.set('session_id', sessionFilter);

    const res = await fetch(`/api/timeline?${params}`);
    const data = await res.json();
    state.commands = data.commands;

    document.getElementById('stats').textContent =
        `${data.total} commands across ${state.sessions.length} sessions`;

    renderTimeline();
    applySearch();
}

function renderTimeline() {
    const container = document.getElementById('timeline');
    container.innerHTML = '';

    if (state.commands.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <h2>No commands found</h2>
                <p>Load asciinema files in the data directory and click reload.</p>
            </div>`;
        return;
    }

    let currentDate = '';

    state.commands.forEach(cmd => {
        const dateStr = formatTsDate(cmd.absolute_timestamp);
        const timeStr = formatTsTime(cmd.absolute_timestamp);

        // Date separator
        if (dateStr !== currentDate) {
            currentDate = dateStr;
            const sep = document.createElement('div');
            sep.className = 'date-separator';
            sep.innerHTML = `<span class="date-label">${formatDate(dateStr)}</span>`;
            container.appendChild(sep);
        }

        const entry = document.createElement('div');
        entry.className = 'timeline-entry';
        entry.dataset.id = cmd.id;
        entry.dataset.command = cmd.command.toLowerCase();
        entry.dataset.hasOutput = cmd.has_output ? '1' : '0';

        const color = state.sessionColors[cmd.session_id] || '#666';
        const durationStr = cmd.duration != null ? formatDuration(cmd.duration) : '';
        const sessionName = cmd.session_title || '';

        entry.innerHTML = `
            <div class="timeline-marker" style="background: ${color}"></div>
            <div class="timeline-header" onclick="toggleOutput(${cmd.id})">
                <span class="timestamp">${timeStr}</span>
                <span class="session-badge" style="border-left: 3px solid ${color}">${sessionName}</span>
                <code class="command">${escapeHtml(cmd.command)}</code>
                <span class="cwd">${escapeHtml(cmd.working_directory || '')}</span>
                ${durationStr ? `<span class="duration">${durationStr}</span>` : ''}
                <button class="copy-btn" title="Copy command" onclick="event.stopPropagation(); copyCommand(${cmd.id}, this)">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                </button>
                <span class="expand-icon">&#9654;</span>
            </div>
            <div class="timeline-output" id="output-${cmd.id}">
                <pre class="loading">Click to load output...</pre>
            </div>`;

        container.appendChild(entry);
    });
}

/* Toggle output */
async function toggleOutput(commandId) {
    const entry = document.querySelector(`.timeline-entry[data-id="${commandId}"]`);
    if (!entry) return;

    const outputDiv = document.getElementById(`output-${commandId}`);
    const isExpanded = entry.classList.contains('expanded');

    if (isExpanded) {
        entry.classList.remove('expanded');
        return;
    }

    entry.classList.add('expanded');

    // Lazy load
    if (!outputDiv.dataset.loaded) {
        outputDiv.innerHTML = '<pre class="loading">Loading...</pre>';
        try {
            const res = await fetch(`/api/command/${commandId}`);
            const data = await res.json();
            if (data.output_html && data.output_html.trim()) {
                outputDiv.innerHTML = `
                    <div class="output-toolbar">
                        <button class="copy-btn copy-output-btn" title="Copy output" onclick="copyOutput(${commandId}, this)">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                            <span>Copy output</span>
                        </button>
                    </div>
                    <pre>${data.output_html}</pre>`;
                outputDiv.dataset.rawOutput = data.output_raw || '';
            } else {
                outputDiv.innerHTML = '<pre class="no-output">No output</pre>';
            }
            outputDiv.dataset.loaded = 'true';
        } catch (err) {
            outputDiv.innerHTML = '<pre class="no-output">Error loading output</pre>';
        }
    }
}

/* Copy */
function flashCopyBtn(btn, ok) {
    btn.classList.add(ok ? 'copied' : 'copy-fail');
    setTimeout(() => btn.classList.remove('copied', 'copy-fail'), 1200);
}

function copyCommand(commandId, btn) {
    const entry = document.querySelector(`.timeline-entry[data-id="${commandId}"]`);
    const cmd = entry?.querySelector('.command')?.textContent || '';
    navigator.clipboard.writeText(cmd).then(
        () => flashCopyBtn(btn, true),
        () => flashCopyBtn(btn, false),
    );
}

function copyOutput(commandId, btn) {
    const outputDiv = document.getElementById(`output-${commandId}`);
    // Prefer raw output (no HTML), fallback to pre text content
    const text = outputDiv.dataset.rawOutput || outputDiv.querySelector('pre')?.textContent || '';
    navigator.clipboard.writeText(text).then(
        () => flashCopyBtn(btn, true),
        () => flashCopyBtn(btn, false),
    );
}

/* Search */
function handleSearch(e) {
    state.searchQuery = e.target.value.trim().toLowerCase();
    applySearch();
}

function applySearch() {
    const query = state.searchQuery;
    const entries = document.querySelectorAll('.timeline-entry');

    state.matchedEntries = [];
    state.matchIndex = -1;

    // Split query into keywords (space-separated), all must match
    const keywords = query ? query.split(/\s+/).filter(Boolean) : [];

    entries.forEach(entry => {
        const cmd = entry.dataset.command;
        entry.classList.remove('search-active');

        // Hide entries without output if filter is active
        if (state.hideNoOutput && entry.dataset.hasOutput === '0') {
            entry.classList.add('hidden-entry');
            return;
        }
        entry.classList.remove('hidden-entry');

        const matches = keywords.length === 0 || keywords.every(kw => cmd.includes(kw));
        if (matches) {
            entry.classList.remove('dimmed');
            if (keywords.length > 0) state.matchedEntries.push(entry);
        } else {
            entry.classList.add('dimmed');
        }
    });

    updateMatchCounter();

    // Auto-jump to first match
    if (state.matchedEntries.length > 0) {
        navigateMatch(1);
    }
}

function navigateMatch(direction) {
    if (state.matchedEntries.length === 0) return;

    // Remove highlight from current
    if (state.matchIndex >= 0 && state.matchIndex < state.matchedEntries.length) {
        state.matchedEntries[state.matchIndex].classList.remove('search-active');
    }

    // Move index
    state.matchIndex += direction;
    if (state.matchIndex >= state.matchedEntries.length) state.matchIndex = 0;
    if (state.matchIndex < 0) state.matchIndex = state.matchedEntries.length - 1;

    // Highlight and scroll to new match
    const entry = state.matchedEntries[state.matchIndex];
    entry.classList.add('search-active');
    entry.scrollIntoView({ behavior: 'smooth', block: 'center' });

    updateMatchCounter();
}

function updateMatchCounter() {
    const counter = document.getElementById('search-counter');
    if (!state.searchQuery || state.matchedEntries.length === 0) {
        counter.textContent = state.searchQuery ? 'No results' : '';
    } else {
        counter.textContent = `${state.matchIndex + 1} / ${state.matchedEntries.length}`;
    }
}

/* Hide no-output toggle */
function toggleHideNoOutput() {
    state.hideNoOutput = !state.hideNoOutput;
    const btn = document.getElementById('hide-no-output');
    btn.classList.toggle('active', state.hideNoOutput);
    applySearch();
}

/* Session filter */
function handleSessionFilter() {
    loadTimeline();
}

/* Reload */
async function handleReload() {
    const btn = document.getElementById('reload-btn');
    btn.disabled = true;
    btn.textContent = 'Reloading...';

    try {
        const res = await fetch('/api/reload', { method: 'POST' });
        const data = await res.json();
        await Promise.all([loadSessions(), loadTimeline()]);
        btn.textContent = `Reloaded (${data.sessions_loaded} new)`;
        setTimeout(() => { btn.textContent = 'Reload'; btn.disabled = false; }, 2000);
    } catch (err) {
        btn.textContent = 'Error';
        setTimeout(() => { btn.textContent = 'Reload'; btn.disabled = false; }, 2000);
    }
}

/* Upload */
function handleUploadInput(e) {
    const files = e.target.files;
    if (files.length > 0) uploadFiles(files);
    e.target.value = '';
}

function initDragDrop() {
    const overlay = document.getElementById('drop-overlay');
    let dragCounter = 0;

    document.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dragCounter++;
        overlay.classList.remove('hidden');
    });

    document.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dragCounter--;
        if (dragCounter <= 0) { dragCounter = 0; overlay.classList.add('hidden'); }
    });

    document.addEventListener('dragover', (e) => e.preventDefault());

    document.addEventListener('drop', (e) => {
        e.preventDefault();
        dragCounter = 0;
        overlay.classList.add('hidden');
        if (e.dataTransfer.files.length > 0) uploadFiles(e.dataTransfer.files);
    });
}

async function uploadFiles(fileList) {
    const btn = document.getElementById('upload-btn');
    btn.disabled = true;
    btn.textContent = 'Uploading...';

    const formData = new FormData();
    for (const f of fileList) {
        formData.append('files', f);
    }

    try {
        const res = await fetch('/api/upload', { method: 'POST', body: formData });
        const data = await res.json();
        if (!res.ok) {
            btn.textContent = data.error || 'Error';
            setTimeout(() => { btn.textContent = 'Upload'; btn.disabled = false; }, 2000);
            return;
        }

        const msg = [];
        if (data.saved.length > 0) msg.push(`${data.saved.length} uploaded`);
        if (data.skipped.length > 0) msg.push(`${data.skipped.length} skipped`);
        if (data.sessions_loaded > 0) msg.push(`${data.sessions_loaded} new sessions`);
        btn.textContent = msg.join(', ') || 'Done';

        if (data.sessions_loaded > 0) {
            await Promise.all([loadSessions(), loadTimeline()]);
        }

        setTimeout(() => { btn.textContent = 'Upload'; btn.disabled = false; }, 3000);
    } catch (err) {
        btn.textContent = 'Error';
        setTimeout(() => { btn.textContent = 'Upload'; btn.disabled = false; }, 2000);
    }
}

/* Helpers */
function formatDate(dateStr) {
    const [y, m, d] = dateStr.split('-');
    const months = ['', 'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December'];
    return `${months[parseInt(m)]} ${parseInt(d)}, ${y}`;
}

function formatDuration(seconds) {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function debounce(fn, ms) {
    let timer;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), ms);
    };
}
