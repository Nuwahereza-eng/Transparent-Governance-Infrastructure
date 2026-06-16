// Shared JS helpers.

const TOKEN_KEY = 'pc_token';
const USER_KEY  = 'pc_user';

function getToken() { return localStorage.getItem(TOKEN_KEY); }
function getUser()  { try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; } }
function setSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}
function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

async function api(url, opts = {}) {
  const headers = opts.headers || {};
  if (getToken()) headers['Authorization'] = 'Bearer ' + getToken();
  if (opts.body && !(opts.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    if (typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(url, { ...opts, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    // Auto-recover from stale / invalid sessions. Happens when the server's
    // SECRET_KEY changed (e.g. fresh Render deploy) or the user was deleted
    // after a reseed. Clear the dead token and send the user to /login.
    if (res.status === 401 && getToken()) {
      const wasInvalid = /invalid token|could not validate|expired/i.test(detail);
      clearSession();
      if (wasInvalid && !location.pathname.endsWith('/login.html')) {
        location.href = '/login.html?expired=1';
        // Hang the promise so callers don't render an error before redirect.
        await new Promise(() => {});
      }
    }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function fmt(n) {
  if (n === null || n === undefined) return '—';
  if (typeof n !== 'number') return n;
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function fmtMoney(n) {
  if (n === null || n === undefined) return '—';
  return '$' + fmt(n);
}

function fmtDate(s) {
  if (!s) return '—';
  const d = new Date(s);
  return d.toLocaleString();
}

function riskBadge(level, score) {
  if (level === null || level === undefined) return '<span class="badge no-dot bg-slate-100 text-slate-500 border-slate-200">unscored</span>';
  const cls = level === 'High' ? 'risk-high' : level === 'Medium' ? 'risk-medium' : 'risk-low';
  return `<span class="badge ${cls}">${level} · ${score?.toFixed?.(0) ?? score}</span>`;
}

function statusBadge(status) {
  const map = {
    draft:              'bg-slate-100 text-slate-600 border-slate-200',
    pending_department: 'bg-slate-100 text-slate-700 border-slate-200',
    pending_financial:  'bg-slate-100 text-slate-700 border-slate-200',
    pending_compliance: 'bg-slate-100 text-slate-700 border-slate-200',
    pending_final:      'bg-slate-100 text-slate-700 border-slate-200',
    open:               'bg-slate-100 text-slate-700 border-slate-200',
    evaluating:         'bg-amber-50 text-amber-700 border-amber-200',
    in_progress:        'bg-slate-100 text-slate-700 border-slate-200',
    awarded:            'bg-emerald-50 text-emerald-700 border-emerald-200',
    completed:          'bg-emerald-50 text-emerald-700 border-emerald-200',
    cancelled:          'bg-slate-100 text-slate-500 border-slate-200',
    rejected:           'bg-rose-50 text-rose-700 border-rose-200',
  };
  const label = (status || '').replaceAll('_', ' ');
  return `<span class="badge ${map[status] || 'bg-slate-100 text-slate-600 border-slate-200'}">${label}</span>`;
}

function reputationBadge(level, score) {
  if (level == null) return '<span class="badge no-dot bg-slate-100 text-slate-500 border-slate-200">no data</span>';
  const cls = {
    Excellent: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    Good:      'bg-emerald-50 text-emerald-700 border-emerald-200',
    Fair:      'bg-amber-50 text-amber-700 border-amber-200',
    Poor:      'bg-amber-50 text-amber-700 border-amber-200',
    Critical:  'bg-rose-50 text-rose-700 border-rose-200',
  }[level] || 'bg-slate-100 text-slate-600 border-slate-200';
  return `<span class="badge ${cls}">${level} · ${Math.round(score)}</span>`;
}

function feedbackStatusBadge(status) {
  const map = {
    pending:      'bg-amber-50 text-amber-700 border-amber-200',
    under_review: 'bg-slate-100 text-slate-700 border-slate-200',
    resolved:     'bg-emerald-50 text-emerald-700 border-emerald-200',
    dismissed:    'bg-slate-100 text-slate-500 border-slate-200',
  };
  return `<span class="badge ${map[status] || 'bg-slate-100 text-slate-600 border-slate-200'}">${(status||'').replace('_',' ')}</span>`;
}

function stageBadge(stage) {
  if (!stage) return '';
  return `<span class="badge bg-slate-100 text-slate-700 border-slate-200">stage: ${stage}</span>`;
}

/* ---------- Header / nav ---------- */
const NAV_LINKS = [
  { href: '/transparency.html', label: 'Projects' },
  { href: '/analytics.html',    label: 'Analytics' },
  { href: '/contractors.html',  label: 'Contractors' },
  { href: '/feedback.html',     label: 'Feedback' },
  { href: '/audit.html',        label: 'Audit' },
  { href: '/opendata.html',     label: 'Open Data' },
];

function _isActive(href) {
  const path = location.pathname;
  if (href === '/' && (path === '/' || path === '/index.html')) return true;
  return path === href;
}

function _initials(name) {
  return (name || '?').trim().split(/\s+/).map(s => s[0]).slice(0, 2).join('').toUpperCase();
}

function renderHeader(target) {
  const user = getUser();
  const showDashboard = user && ['officer','contractor','admin','compliance_officer','auditor'].includes(user.role);
  const showApprovals = user && ['officer','compliance_officer','auditor','admin'].includes(user.role);

  const navLinks = (extraClass='') => {
    const links = [...NAV_LINKS];
    if (showDashboard) links.push({ href: '/dashboard.html', label: 'Dashboard', strong: true });
    if (showApprovals) links.push({ href: '/approvals.html', label: 'Approvals', strong: true });
    return links.map(l =>
      `<a href="${l.href}" class="pc-nav-link ${extraClass} ${_isActive(l.href)?'is-active':''} ${l.strong?'font-semibold':''}">${l.label}</a>`
    ).join('');
  };

  const right = user
    ? `<span class="pc-userpill" title="${user.email} (${user.role})">
         <span class="avatar">${_initials(user.full_name || user.email)}</span>
         <span class="hidden xl:inline">${user.full_name || user.email}</span>
         <span class="text-white/70 hidden xl:inline">· ${user.role.replace('_',' ')}</span>
       </span>
       <button id="logout" class="btn btn-sm btn-secondary" style="background:rgba(255,255,255,.95)">Sign out</button>`
    : `<a href="/login.html" class="btn btn-sm" style="background:#fff;color:var(--brand-700)">Sign in</a>`;

  target.innerHTML = `
    <header class="pc-header">
      <div class="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between gap-3">
        <a href="/" class="text-xl font-bold flex items-center gap-2 tracking-tight">
          <span class="pc-logo-mark" aria-hidden="true">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>
          </span>
          <span>GovTrust</span>
        </a>
        <nav class="pc-nav-desktop flex gap-1 text-sm items-center">
          ${navLinks()}
        </nav>
        <div class="flex items-center gap-2">
          ${right}
          <button class="pc-mobile-toggle btn btn-sm btn-ghost text-white" id="pc-menu-btn" aria-label="Menu" style="color:#fff">Menu</button>
        </div>
      </div>
      <div class="pc-nav-mobile" id="pc-nav-mobile">
        ${navLinks()}
      </div>
    </header>`;

  const lo = document.getElementById('logout');
  if (lo) lo.onclick = () => { clearSession(); window.location.href = '/'; };

  const mb = document.getElementById('pc-menu-btn');
  if (mb) mb.onclick = () => document.getElementById('pc-nav-mobile').classList.toggle('is-open');
}

function requireAuth(roles) {
  const u = getUser();
  if (!u) { window.location.href = '/login.html'; return null; }
  if (roles && !roles.includes(u.role) && u.role !== 'admin') {
    toast('You do not have access to this page.', 'error');
    setTimeout(() => { window.location.href = '/'; }, 600);
    return null;
  }
  return u;
}

/* ---------- Toasts ---------- */
function _toastStack() {
  let s = document.getElementById('pc-toast-stack');
  if (!s) {
    s = document.createElement('div');
    s.id = 'pc-toast-stack';
    s.className = 'pc-toast-stack';
    document.body.appendChild(s);
  }
  return s;
}
function toast(message, type = 'info', duration = 3800) {
  const stack = _toastStack();
  const el = document.createElement('div');
  el.className = 'pc-toast ' + type;
  el.innerHTML = `<div>${message}</div>`;
  stack.appendChild(el);
  setTimeout(() => {
    el.classList.add('leaving');
    el.addEventListener('animationend', () => el.remove(), { once: true });
  }, duration);
}

/* ---------- Confirm dialog ---------- */
function confirmDialog({ title = 'Are you sure?', message = '', confirmLabel = 'Confirm', danger = false } = {}) {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'pc-modal-backdrop';
    wrap.innerHTML = `
      <div class="pc-modal" role="dialog" aria-modal="true">
        <h2 class="text-lg font-bold mb-1">${title}</h2>
        ${message ? `<p class="text-sm text-slate-600 mb-4">${message}</p>` : ''}
        <div class="flex justify-end gap-2 mt-2">
          <button class="btn btn-secondary" data-act="cancel">Cancel</button>
          <button class="btn ${danger?'btn-danger':'btn-primary'}" data-act="ok">${confirmLabel}</button>
        </div>
      </div>`;
    const close = (val) => { wrap.remove(); document.removeEventListener('keydown', onKey); resolve(val); };
    const onKey = (e) => { if (e.key === 'Escape') close(false); };
    wrap.addEventListener('click', (e) => { if (e.target === wrap) close(false); });
    wrap.querySelector('[data-act=cancel]').onclick = () => close(false);
    wrap.querySelector('[data-act=ok]').onclick     = () => close(true);
    document.addEventListener('keydown', onKey);
    document.body.appendChild(wrap);
    wrap.querySelector('[data-act=ok]').focus();
  });
}

/* ---------- Skeletons ---------- */
function skeletonRows(cols, rows = 4) {
  let html = '';
  for (let r = 0; r < rows; r++) {
    html += '<tr class="border-t">';
    for (let c = 0; c < cols; c++) html += '<td class="p-3"><span class="skeleton h-3 w-3/4"></span></td>';
    html += '</tr>';
  }
  return html;
}
function skeletonLines(n = 3) {
  return Array.from({ length: n }, (_, i) => `<span class="skeleton h-3 mb-2" style="width:${[90,75,60][i%3]}%"></span>`).join('');
}

/* ---------- Utility ---------- */
function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

/* Replace native confirm with our themed one for consistency.
   We DON'T override window.confirm to avoid breaking sync callers. */
