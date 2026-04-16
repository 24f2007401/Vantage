// ── Vantage API Foundation ───────────────────────────────────────────────────
const API_BASE   = "";
const TOKEN_KEY  = "vantage_token";
const REFRESH_KEY = "vantage_refresh";
const ROLE_KEY   = "vantage_role";
const USER_KEY   = "vantage_user";

let authToken = sessionStorage.getItem(TOKEN_KEY) || null;
let authRole  = sessionStorage.getItem(ROLE_KEY)  || null;

function setAuth(token, role, refreshToken, user) {
    authToken = token;
    authRole  = role;
    sessionStorage.setItem(TOKEN_KEY, token);
    sessionStorage.setItem(ROLE_KEY,  role);
    if (refreshToken) sessionStorage.setItem(REFRESH_KEY, refreshToken);
    if (user) sessionStorage.setItem(USER_KEY, JSON.stringify(user));
}

function clearAuth() {
    authToken = null;
    authRole  = null;
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(REFRESH_KEY);
    sessionStorage.removeItem(ROLE_KEY);
    sessionStorage.removeItem(USER_KEY);
}

function getUser() {
    try { return JSON.parse(sessionStorage.getItem(USER_KEY)); }
    catch { return null; }
}

// ── Core fetch wrapper ───────────────────────────────────────────────────────
async function apiFetch(path, options = {}) {
    const headers = { "Content-Type": "application/json" };
    if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
    if (options.headers) Object.assign(headers, options.headers);

    let res;
    try {
        res = await fetch(`${API_BASE}${path}`, { ...options, headers });
    } catch (err) {
        showToast("No connection. Check your network.", "error");
        return null;
    }

    switch (res.status) {
        case 401: {
            const refreshToken = sessionStorage.getItem(REFRESH_KEY);
            if (refreshToken && !options._isRefresh) {
                const refreshRes = await fetch(`${API_BASE}/api/auth/refresh`, {
                    method: "POST",
                    headers: { "Authorization": `Bearer ${refreshToken}` }
                }).catch(() => null);
                if (refreshRes && refreshRes.ok) {
                    const refreshData = await refreshRes.json().catch(() => null);
                    if (refreshData && refreshData.access_token) {
                        setAuth(refreshData.access_token, authRole, refreshData.refresh_token || null, null);
                        return apiFetch(path, { ...options, _isRefresh: true });
                    }
                }
            }
            clearAuth();
            window.location.href = "/login";
            return;
        }
        case 403: {
            const body403 = await res.json().catch(() => ({}));
            const reasons403 = body403.reasons && body403.reasons.length
                ? ' ' + body403.reasons.slice(0, 2).join(' • ')
                : '';
            showToast((body403.error || "Access Denied — you don't have permission for this action.") + reasons403, "error");
            return null;
        }
        case 404:
            return null;
        case 409: {
            const body = await res.json().catch(() => ({}));
            showToast(body.error || "Conflict — this action has already been taken.", "warning");
            return null;
        }
        case 422: {
            const body = await res.json().catch(() => ({}));
            showToast(body.error || "Validation failed — check your inputs.", "warning");
            throw { status: 422, body };
        }
        case 500:
            console.error("[Vantage] 500 on", path);
            showToast("Something went wrong on our end. Try again shortly.", "error");
            return null;
        default:
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                showToast(body.error || `Error ${res.status}`, "warning");
                return null;
            }
            return res.json();
    }
}

// FormData version — for T-Sheet file uploads
async function apiUpload(path, formData) {
    const headers = {};
    if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
    let res;
    try {
        res = await fetch(`${API_BASE}${path}`, {
            method: "POST", headers, body: formData
        });
    } catch (err) {
        showToast("Upload failed — check your network.", "error");
        return null;
    }
    if (res.status === 401) { clearAuth(); window.location.href = "/login"; return; }
    return res.json();
}

// ── Toast notification (brand-compliant) ─────────────────────────────────────
function showToast(message, type = "info") {
    let container = document.getElementById("vantage-toast-container");
    if (!container) {
        container = document.createElement("div");
        container.id = "vantage-toast-container";
        container.style.cssText = `
            position:fixed; bottom:24px; right:24px; z-index:9999;
            display:flex; flex-direction:column; gap:8px;`;
        document.body.appendChild(container);
    }
    const colors = {
        info:    { bg:"#e8f0fe", text:"#005bc2", icon:"ℹ" },
        success: { bg:"#e6f4ea", text:"#1e7e34", icon:"✓" },
        warning: { bg:"#fff8e1", text:"#c77700", icon:"⚠" },
        error:   { bg:"#fce8e6", text:"#c5221f", icon:"✕" }
    };
    const c = colors[type] || colors.info;
    const toast = document.createElement("div");
    toast.style.cssText = `
        background:${c.bg}; color:${c.text}; padding:12px 20px; border-radius:12px;
        font-family:Inter,sans-serif; font-size:14px; font-weight:500;
        box-shadow:0 4px 20px rgba(46,51,58,0.10);
        display:flex; align-items:center; gap:10px;
        animation:vantageSlideIn .3s cubic-bezier(0.175,0.885,0.32,1.275) both;
        max-width:360px;`;
    toast.innerHTML = `<span style="font-size:16px">${c.icon}</span><span>${message}</span>`;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

// ── Skeleton loader helper ───────────────────────────────────────────────────
function skeletonCard(height = 80) {
    return `<div style="background:linear-gradient(90deg,#f2f3fa 25%,#e8e9f0 50%,#f2f3fa 75%);
        background-size:200% 100%; border-radius:14px; height:${height}px;
        animation:vantageShimmer 1.4s infinite linear;"></div>`;
}

// ── Inject required CSS animations once ─────────────────────────────────────
(function injectCSS() {
    if (document.getElementById("vantage-anim-css")) return;
    const s = document.createElement("style");
    s.id = "vantage-anim-css";
    s.textContent = `
        @keyframes vantageSlideIn {
            from { opacity:0; transform:translateY(16px) scale(.95); }
            to   { opacity:1; transform:translateY(0)    scale(1);   }
        }
        @keyframes vantageShimmer {
            0%   { background-position: 200% 0; }
            100% { background-position:-200% 0; }
        }`;
    document.head.appendChild(s);
})();

// ── URL param helper ─────────────────────────────────────────────────────────
function getParam(name) {
    return new URLSearchParams(window.location.search).get(name);
}

// ── Auth guard (call at top of protected pages) ──────────────────────────────
function requireAuth(allowedRole) {
    if (!authToken) {
        window.location.href = "/login";
        return false;
    }
    if (allowedRole && authRole !== allowedRole) {
        showToast("You don't have access to this area.", "error");
        const redirectMap = { student: "/student", admin: "/admin", recruiter: "/recruiter" };
        window.location.href = redirectMap[authRole] || "/login";
        return false;
    }
    return true;
}

// ── Logout ───────────────────────────────────────────────────────────────────
async function doLogout() {
    await apiFetch("/api/auth/logout", { method: "POST" });
    clearAuth();
    window.location.href = "/login";
}

// ── Time ago helper ──────────────────────────────────────────────────────────
function _timeAgo(isoStr) {
    if (!isoStr) return '';
    const diff = Math.floor((Date.now() - new Date(isoStr)) / 1000);
    if (diff < 60)   return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
}

// ── Live search helper (debounced, dropdown) ─────────────────────────────────
function initLiveSearch(inputEl, fetchUrl, onResult) {
    if (!inputEl) return;
    let timer;
    let dropdownEl = null;

    function closeDropdown() {
        if (dropdownEl) { dropdownEl.remove(); dropdownEl = null; }
    }

    inputEl.addEventListener('input', () => {
        clearTimeout(timer);
        const q = inputEl.value.trim();
        if (q.length < 2) { closeDropdown(); return; }
        timer = setTimeout(async () => {
            const data = await apiFetch(`${fetchUrl}?q=${encodeURIComponent(q)}`);
            closeDropdown();
            if (!data) return;

            const rows = onResult(data, q);
            if (!rows.length) {
                rows.push({
                    icon: 'search_off',
                    primary: `No results for "${q}"`,
                    secondary: '',
                    onClick: null,
                });
            }

            dropdownEl = document.createElement('div');
            dropdownEl.style.cssText = `
                position:absolute;background:#fff;border-radius:14px;
                box-shadow:0 8px 32px rgba(26,28,31,0.12);z-index:9999;
                max-height:300px;overflow-y:auto;min-width:320px;`;

            const rect = inputEl.getBoundingClientRect();
            dropdownEl.style.top = `${rect.bottom + window.scrollY + 6}px`;
            dropdownEl.style.left = `${rect.left + window.scrollX}px`;

            rows.forEach(r => {
                const row = document.createElement('div');
                row.style.cssText = `padding:10px 16px;display:flex;align-items:center;gap:12px;cursor:${r.onClick ? 'pointer' : 'default'};border-bottom:1px solid #f3f3f8;`;
                row.innerHTML = `
                    <span class="material-symbols-outlined" style="font-size:18px;color:#727784;">${r.icon}</span>
                    <div>
                        <div style="font-size:13px;font-weight:600;color:#1a1c1f;">${r.primary}</div>
                        ${r.secondary ? `<div style="font-size:11px;color:#727784;">${r.secondary}</div>` : ''}
                    </div>`;
                if (r.onClick) {
                    row.addEventListener('click', () => { closeDropdown(); r.onClick(); });
                    row.addEventListener('mouseenter', () => row.style.background = '#f3f3f8');
                    row.addEventListener('mouseleave', () => row.style.background = '');
                }
                dropdownEl.appendChild(row);
            });

            document.body.appendChild(dropdownEl);
        }, 300);
    });

    document.addEventListener('click', e => {
        if (dropdownEl && !dropdownEl.contains(e.target) && e.target !== inputEl) closeDropdown();
    });
    inputEl.addEventListener('keydown', e => { if (e.key === 'Escape') closeDropdown(); });
}

// ── Admin Header: shared notifications + profile panel ───────────────────────

async function initAdminHeader() {
    // Populate avatar initials
    const user = getUser();
    const avatarEl = document.getElementById('admin-avatar');
    if (avatarEl && user) {
        const initials = (user.full_name || user.email || 'A')
            .split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase();
        avatarEl.textContent = initials;
    }
    if (avatarEl) avatarEl.addEventListener('click', openProfilePanel);

    // Load unread count for badge
    const data = await apiFetch('/api/admin/notifications');
    const badge = document.getElementById('notif-badge');
    if (data && badge) {
        if (data.unread_count > 0) {
            badge.textContent = data.unread_count > 9 ? '9+' : data.unread_count;
            badge.style.display = 'flex';
        } else {
            badge.style.display = 'none';
        }
    }

    const bellBtn = document.getElementById('admin-bell-btn');
    if (bellBtn) bellBtn.addEventListener('click', openNotificationsPanel);
}

function openProfilePanel() {
    document.getElementById('vantage-profile-panel')?.remove();
    const user = getUser();
    const initials = (user?.full_name || user?.email || 'A')
        .split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase();
    const lastLogin = user?.last_login
        ? new Date(user.last_login).toLocaleString() : 'This session';

    const panel = document.createElement('div');
    panel.id = 'vantage-profile-panel';
    panel.style.cssText = 'position:fixed;top:0;right:0;height:100vh;width:320px;' +
        'background:#fff;box-shadow:-20px 0 60px -15px rgba(0,0,0,0.12);z-index:9100;' +
        'transform:translateX(100%);transition:transform .3s cubic-bezier(.4,0,.2,1);' +
        'display:flex;flex-direction:column;';
    panel.innerHTML = `
        <div style="padding:20px 24px;border-bottom:1px solid #ededf2;display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:14px;font-weight:700;color:#1a1c1f;">My Profile</span>
            <button onclick="document.getElementById('vantage-profile-panel').remove()"
                style="width:32px;height:32px;border-radius:50%;border:none;background:#f3f3f8;
                cursor:pointer;font-size:18px;color:#424753;display:flex;align-items:center;justify-content:center;">×</button>
        </div>
        <div style="padding:24px;flex:1;overflow-y:auto;">
            <div style="text-align:center;margin-bottom:24px;">
                <div style="width:64px;height:64px;border-radius:50%;background:linear-gradient(135deg,#004495,#005bc2);
                    display:flex;align-items:center;justify-content:center;color:#fff;
                    font-size:22px;font-weight:700;margin:0 auto 12px;">${initials}</div>
                <div style="font-size:16px;font-weight:700;color:#1a1c1f;">${user?.full_name || 'Admin'}</div>
                <div style="font-size:12px;color:#727784;margin-top:4px;">${user?.email || ''}</div>
                <span style="display:inline-block;margin-top:8px;padding:3px 12px;background:#d8e2ff;
                    color:#004495;border-radius:999px;font-size:10px;font-weight:700;
                    text-transform:uppercase;letter-spacing:.05em;">${user?.role || 'admin'}</span>
            </div>
            <div style="background:#f3f3f8;border-radius:12px;padding:16px;margin-bottom:16px;">
                <div style="font-size:10px;font-weight:700;color:#727784;text-transform:uppercase;
                    letter-spacing:.08em;margin-bottom:6px;">Last Login</div>
                <div style="font-size:12px;color:#1a1c1f;">${lastLogin}</div>
            </div>
            <button onclick="doLogout()"
                style="width:100%;padding:12px;background:#ba1a1a;color:#fff;border:none;
                border-radius:12px;font-size:14px;font-weight:600;cursor:pointer;">Logout</button>
        </div>`;
    document.body.appendChild(panel);
    requestAnimationFrame(() => { panel.style.transform = 'translateX(0)'; });

    // Close on outside click
    setTimeout(() => {
        document.addEventListener('click', function closePanel(e) {
            if (!panel.contains(e.target) && e.target.id !== 'admin-avatar') {
                panel.remove();
                document.removeEventListener('click', closePanel);
            }
        });
    }, 100);
}

async function openNotificationsPanel() {
    document.getElementById('vantage-notif-panel')?.remove();

    const panel = document.createElement('div');
    panel.id = 'vantage-notif-panel';
    panel.style.cssText = 'position:fixed;top:0;right:0;height:100vh;width:380px;' +
        'background:#fff;box-shadow:-20px 0 60px -15px rgba(0,0,0,0.12);z-index:9100;' +
        'transform:translateX(100%);transition:transform .3s cubic-bezier(.4,0,.2,1);' +
        'display:flex;flex-direction:column;';
    panel.innerHTML = `
        <div style="padding:20px 24px;border-bottom:1px solid #ededf2;display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:14px;font-weight:700;color:#1a1c1f;">Notifications</span>
            <button onclick="document.getElementById('vantage-notif-panel').remove()"
                style="width:32px;height:32px;border-radius:50%;border:none;background:#f3f3f8;
                cursor:pointer;font-size:18px;color:#424753;display:flex;align-items:center;justify-content:center;">×</button>
        </div>
        <div id="vantage-notif-list" style="flex:1;overflow-y:auto;padding:16px;">
            <p style="text-align:center;color:#727784;font-size:13px;padding:32px 0;">Loading…</p>
        </div>`;
    document.body.appendChild(panel);
    requestAnimationFrame(() => { panel.style.transform = 'translateX(0)'; });

    const typeColors = { system: '#ba1a1a', pipeline: '#004495', recruiter: '#4b5e87', admin: '#7e2e00' };

    const data = await apiFetch('/api/admin/notifications');
    const listEl = document.getElementById('vantage-notif-list');
    if (!listEl) return;

    if (!data || !data.notifications || !data.notifications.length) {
        listEl.innerHTML = '<p style="text-align:center;color:#727784;font-size:13px;padding:32px 0;">No notifications</p>';
        return;
    }

    listEl.innerHTML = data.notifications.map(n => `
        <div style="padding:12px 14px;border-radius:12px;margin-bottom:8px;
            background:${n.is_read ? '#f9f9fe' : '#e8f0fe'};
            border-left:3px solid ${typeColors[n.type] || '#004495'};">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
                <span style="font-size:11px;font-weight:700;color:#1a1c1f;">${n.title || n.type}</span>
                <span style="font-size:10px;color:#727784;white-space:nowrap;">
                    ${_timeAgo(n.created_at)}</span>
            </div>
            <p style="margin:6px 0 0;font-size:13px;color:#424753;line-height:1.45;">${n.message}</p>
        </div>`).join('');

    // Mark all as read and hide badge
    apiFetch('/api/admin/notifications/mark-read', { method: 'PATCH', body: JSON.stringify({ all: true }) });
    const badge = document.getElementById('notif-badge');
    if (badge) badge.style.display = 'none';

    // Close on outside click
    setTimeout(() => {
        document.addEventListener('click', function closePanel(e) {
            if (!panel.contains(e.target) && e.target.id !== 'admin-bell-btn') {
                panel.remove();
                document.removeEventListener('click', closePanel);
            }
        });
    }, 100);
}

// ── Recruiter Header (same UI as admin, different notifications URL) ───────────

async function initRecruiterHeader() {
    const user = getUser();
    const avatarEl = document.getElementById('admin-avatar');
    if (avatarEl && user) {
        const initials = (user.full_name || user.email || 'R')
            .split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase();
        avatarEl.textContent = initials;
    }
    if (avatarEl) avatarEl.addEventListener('click', openProfilePanel);

    const data = await apiFetch('/api/recruiter/notifications');
    const badge = document.getElementById('notif-badge');
    if (data && badge) {
        if (data.unread_count > 0) {
            badge.textContent = data.unread_count > 9 ? '9+' : data.unread_count;
            badge.style.display = 'flex';
        } else {
            badge.style.display = 'none';
        }
    }

    const bellBtn = document.getElementById('admin-bell-btn');
    if (bellBtn) bellBtn.addEventListener('click', () => openRecruiterNotificationsPanel());
}

// ── Student Header (notifications + profile avatar) ───────────────────────────

async function initStudentHeader() {
    const user = getUser();
    const avatarEl = document.getElementById('student-avatar');
    if (avatarEl && user) {
        const initials = (user.full_name || user.email || 'S')
            .split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase();
        avatarEl.textContent = initials;
    }
    if (avatarEl) avatarEl.addEventListener('click', () => window.location.href = '/student/profile');

    const data = await apiFetch('/api/student/notifications');
    const badge = document.getElementById('student-notif-badge');
    if (data && badge) {
        if (data.unread_count > 0) {
            badge.textContent = data.unread_count > 9 ? '9+' : data.unread_count;
            badge.style.display = 'flex';
        } else {
            badge.style.display = 'none';
        }
    }
}

async function openStudentNotificationsPanel() {
    document.getElementById('vantage-notif-panel')?.remove();
    const panel = document.createElement('div');
    panel.id = 'vantage-notif-panel';
    panel.style.cssText = 'position:fixed;top:0;right:0;height:100vh;width:380px;' +
        'background:#fff;box-shadow:-20px 0 60px -15px rgba(0,0,0,0.12);z-index:9100;' +
        'transform:translateX(100%);transition:transform .3s cubic-bezier(.4,0,.2,1);' +
        'display:flex;flex-direction:column;';
    panel.innerHTML = `
        <div style="padding:20px 24px;border-bottom:1px solid #ededf2;display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:14px;font-weight:700;color:#1a1c1f;">Notifications</span>
            <button onclick="document.getElementById('vantage-notif-panel').remove()"
                style="width:32px;height:32px;border-radius:50%;border:none;background:#f3f3f8;
                cursor:pointer;font-size:18px;color:#424753;display:flex;align-items:center;justify-content:center;">×</button>
        </div>
        <div id="vantage-notif-list" style="flex:1;overflow-y:auto;padding:16px;">
            <p style="text-align:center;color:#727784;font-size:13px;padding:32px 0;">Loading…</p>
        </div>`;
    document.body.appendChild(panel);
    requestAnimationFrame(() => { panel.style.transform = 'translateX(0)'; });

    const typeColors = { verification: '#1e7e34', system: '#ba1a1a', recruiter: '#4b5e87' };
    const data = await apiFetch('/api/student/notifications');
    const listEl = document.getElementById('vantage-notif-list');
    if (!listEl) return;

    if (!data || !data.notifications || !data.notifications.length) {
        listEl.innerHTML = '<p style="text-align:center;color:#727784;font-size:13px;padding:32px 0;">No notifications</p>';
        return;
    }

    listEl.innerHTML = data.notifications.map(n => `
        <div style="padding:12px 14px;border-radius:12px;margin-bottom:8px;
            background:${n.is_read ? '#f9f9fe' : '#e8f0fe'};
            border-left:3px solid ${typeColors[n.type] || '#004495'};">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
                <span style="font-size:11px;font-weight:700;color:#1a1c1f;">${n.title || n.type}</span>
                <span style="font-size:10px;color:#727784;white-space:nowrap;">
                    ${_timeAgo(n.created_at)}</span>
            </div>
            <p style="margin:6px 0 0;font-size:13px;color:#424753;line-height:1.45;">${n.message}</p>
        </div>`).join('');

    apiFetch('/api/student/notifications/mark-read', { method: 'PATCH', body: JSON.stringify({ all: true }) });
    const badge = document.getElementById('student-notif-badge');
    if (badge) badge.style.display = 'none';

    setTimeout(() => {
        document.addEventListener('click', function closePanel(e) {
            if (!panel.contains(e.target) && e.target.id !== 'student-bell-btn') {
                panel.remove();
                document.removeEventListener('click', closePanel);
            }
        });
    }, 100);
}

// ── Student SSE — connect and react to live events ────────────────────────────

function connectStudentSSE() {
    if (!authToken) return;
    let es;
    function connect() {
        es = new EventSource(`/api/student/events?token=${encodeURIComponent(authToken)}`);
        es.onmessage = function(e) {
            try {
                const ev = JSON.parse(e.data);
                handleStudentEvent(ev);
            } catch (_) {}
        };
        es.onerror = function() {
            es.close();
            setTimeout(connect, 5000); // Reconnect after 5s
        };
    }
    connect();
}

function handleStudentEvent(ev) {
    if (!ev || !ev.type) return;
    switch (ev.type) {
        case 'notification_created': {
            const badge = document.getElementById('student-notif-badge');
            if (badge && ev.data && ev.data.unread_count > 0) {
                badge.textContent = ev.data.unread_count > 9 ? '9+' : ev.data.unread_count;
                badge.style.display = 'flex';
            }
            break;
        }
        case 'verification_updated': {
            // Update verification badge on dashboard
            const notice = document.getElementById('verification-notice');
            const vtext = document.getElementById('verification-text');
            if (ev.data && ev.data.is_verified) {
                if (notice) notice.className = notice.className
                    .replace('bg-tertiary-fixed text-on-tertiary-fixed-variant', 'bg-green-50 text-green-700');
                if (vtext) vtext.textContent = '✓ Your profile is verified.';
            }
            break;
        }
        case 'application_updated': {
            // Trigger reload of applications list if on that page
            if (typeof loadApplications === 'function') loadApplications();
            break;
        }
        case 'drive_created': {
            // Trigger reload of drives list if on drives page
            if (typeof loadDrives === 'function') loadDrives();
            break;
        }
    }
}

async function openRecruiterNotificationsPanel() {
    document.getElementById('vantage-notif-panel')?.remove();
    const panel = document.createElement('div');
    panel.id = 'vantage-notif-panel';
    panel.style.cssText = 'position:fixed;top:0;right:0;height:100vh;width:380px;' +
        'background:#fff;box-shadow:-20px 0 60px -15px rgba(0,0,0,0.12);z-index:9100;' +
        'transform:translateX(100%);transition:transform .3s cubic-bezier(.4,0,.2,1);' +
        'display:flex;flex-direction:column;';
    panel.innerHTML = `
        <div style="padding:20px 24px;border-bottom:1px solid #ededf2;display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:14px;font-weight:700;color:#1a1c1f;">Notifications</span>
            <button onclick="document.getElementById('vantage-notif-panel').remove()"
                style="width:32px;height:32px;border-radius:50%;border:none;background:#f3f3f8;
                cursor:pointer;font-size:18px;color:#424753;display:flex;align-items:center;justify-content:center;">×</button>
        </div>
        <div id="vantage-notif-list" style="flex:1;overflow-y:auto;padding:16px;">
            <p style="text-align:center;color:#727784;font-size:13px;padding:32px 0;">Loading…</p>
        </div>`;
    document.body.appendChild(panel);
    requestAnimationFrame(() => { panel.style.transform = 'translateX(0)'; });

    const typeColors = { admin: '#004495', recruiter: '#4b5e87', system: '#ba1a1a' };
    const data = await apiFetch('/api/recruiter/notifications');
    const listEl = document.getElementById('vantage-notif-list');
    if (!listEl) return;

    if (!data || !data.notifications || !data.notifications.length) {
        listEl.innerHTML = '<p style="text-align:center;color:#727784;font-size:13px;padding:32px 0;">No notifications</p>';
        return;
    }

    listEl.innerHTML = data.notifications.map(n => `
        <div style="padding:12px 14px;border-radius:12px;margin-bottom:8px;
            background:${n.is_read ? '#f9f9fe' : '#e8f0fe'};
            border-left:3px solid ${typeColors[n.type] || '#004495'};">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
                <span style="font-size:11px;font-weight:700;color:#1a1c1f;">${n.title || n.type}</span>
                <span style="font-size:10px;color:#727784;white-space:nowrap;">
                    ${_timeAgo(n.created_at)}</span>
            </div>
            <p style="margin:6px 0 0;font-size:13px;color:#424753;line-height:1.45;">${n.message}</p>
        </div>`).join('');

    apiFetch('/api/recruiter/notifications/mark-read', { method: 'PATCH', body: JSON.stringify({ all: true }) });
    const badge = document.getElementById('notif-badge');
    if (badge) badge.style.display = 'none';

    setTimeout(() => {
        document.addEventListener('click', function closePanel(e) {
            if (!panel.contains(e.target) && e.target.id !== 'admin-bell-btn') {
                panel.remove();
                document.removeEventListener('click', closePanel);
            }
        });
    }, 100);
}

// ── Admin SSE ─────────────────────────────────────────────────────────────────

function connectAdminSSE() {
    if (!authToken) return;
    let es;
    function connect() {
        es = new EventSource(`/api/admin/events?token=${encodeURIComponent(authToken)}`);
        es.onmessage = function(e) {
            try {
                const ev = JSON.parse(e.data);
                handleAdminEvent(ev);
            } catch (_) {}
        };
        es.onerror = function() {
            es.close();
            setTimeout(connect, 5000);
        };
    }
    connect();
}

function handleAdminEvent(ev) {
    if (!ev || !ev.type) return;
    switch (ev.type) {
        case 'notification_created': {
            const badge = document.getElementById('notif-badge');
            if (badge && ev.data && ev.data.unread_count > 0) {
                badge.textContent = ev.data.unread_count > 9 ? '9+' : ev.data.unread_count;
                badge.style.display = 'flex';
            }
            break;
        }
        case 'drive_created':
        case 'drive_updated':
            if (typeof loadDrives === 'function') loadDrives();
            break;
        case 'application_created':
        case 'application_updated':
            if (typeof loadApplications === 'function') loadApplications();
            if (typeof loadDashboard === 'function') loadDashboard();
            break;
        case 'verification_updated':
            if (typeof loadDashboard === 'function') loadDashboard();
            break;
        case 'tsheet_batch_updated':
            if (typeof loadBatches === 'function') loadBatches();
            break;
    }
}

// ── Recruiter SSE ─────────────────────────────────────────────────────────────

function connectRecruiterSSE() {
    if (!authToken) return;
    let es;
    function connect() {
        es = new EventSource(`/api/recruiter/events?token=${encodeURIComponent(authToken)}`);
        es.onmessage = function(e) {
            try {
                const ev = JSON.parse(e.data);
                handleRecruiterEvent(ev);
            } catch (_) {}
        };
        es.onerror = function() {
            es.close();
            setTimeout(connect, 5000);
        };
    }
    connect();
}

function handleRecruiterEvent(ev) {
    if (!ev || !ev.type) return;
    switch (ev.type) {
        case 'notification_created': {
            const badge = document.getElementById('notif-badge');
            if (badge && ev.data && ev.data.unread_count > 0) {
                badge.textContent = ev.data.unread_count > 9 ? '9+' : ev.data.unread_count;
                badge.style.display = 'flex';
            }
            break;
        }
        case 'drive_updated':
            if (typeof loadDrives === 'function') loadDrives();
            break;
        case 'application_created':
            if (typeof loadDashboard === 'function') loadDashboard();
            if (typeof loadDrives === 'function') loadDrives();
            break;
    }
}
