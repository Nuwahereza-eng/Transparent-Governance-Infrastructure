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
  if (level === null || level === undefined) return '<span class="badge bg-slate-100 text-slate-500">unscored</span>';
  const cls = level === 'High' ? 'risk-high' : level === 'Medium' ? 'risk-medium' : 'risk-low';
  return `<span class="badge ${cls}">${level} · ${score?.toFixed?.(0) ?? score}</span>`;
}

function statusBadge(status) {
  const map = {
    draft: 'bg-slate-100 text-slate-600',
    pending_department: 'bg-sky-100 text-sky-700',
    pending_financial: 'bg-cyan-100 text-cyan-700',
    pending_compliance: 'bg-purple-100 text-purple-700',
    pending_final: 'bg-indigo-100 text-indigo-700',
    open: 'bg-blue-100 text-blue-700',
    evaluating: 'bg-amber-100 text-amber-700',
    awarded: 'bg-emerald-100 text-emerald-700',
    cancelled: 'bg-slate-200 text-slate-600',
    rejected: 'bg-rose-100 text-rose-700',
  };
  const label = (status || '').replaceAll('_', ' ');
  return `<span class="badge ${map[status] || 'bg-slate-100 text-slate-600'}">${label}</span>`;
}

function reputationBadge(level, score) {
  if (level == null) return '<span class="badge bg-slate-100 text-slate-500">no data</span>';
  const cls = {
    Excellent: 'bg-emerald-100 text-emerald-700',
    Good: 'bg-sky-100 text-sky-700',
    Fair: 'bg-amber-100 text-amber-700',
    Poor: 'bg-orange-100 text-orange-700',
    Critical: 'bg-rose-100 text-rose-700',
  }[level] || 'bg-slate-100 text-slate-600';
  return `<span class="badge ${cls}">${level} · ${Math.round(score)}</span>`;
}

function feedbackStatusBadge(status) {
  const map = {
    pending: 'bg-amber-100 text-amber-700',
    under_review: 'bg-blue-100 text-blue-700',
    resolved: 'bg-emerald-100 text-emerald-700',
    dismissed: 'bg-slate-200 text-slate-600',
  };
  return `<span class="badge ${map[status] || 'bg-slate-100 text-slate-600'}">${(status||'').replace('_',' ')}</span>`;
}

function stageBadge(stage) {
  if (!stage) return '';
  const map = {
    department: 'bg-sky-100 text-sky-700',
    financial: 'bg-cyan-100 text-cyan-700',
    compliance: 'bg-purple-100 text-purple-700',
    final: 'bg-indigo-100 text-indigo-700',
  };
  return `<span class="badge ${map[stage] || 'bg-slate-100 text-slate-600'}">stage: ${stage}</span>`;
}

function renderHeader(target) {
  const user = getUser();
  const showDashboard = user && ['officer','contractor','admin','compliance_officer'].includes(user.role);
  const showApprovals = user && ['officer','compliance_officer','auditor','admin'].includes(user.role);
  const right = user
    ? `<span class="text-sm hidden md:inline">${user.full_name} <span class="text-white/70">(${user.role})</span></span>
       <button id="logout" class="bg-white text-indigo-700 px-3 py-1 rounded font-semibold hover:bg-indigo-50">Logout</button>`
    : `<a href="/login.html" class="bg-white text-indigo-700 px-3 py-1 rounded font-semibold hover:bg-indigo-50">Sign in</a>`;
  target.innerHTML = `
    <div class="bg-gradient-to-r from-indigo-700 to-blue-600 text-white shadow">
      <div class="max-w-6xl mx-auto px-6 py-4 flex flex-wrap items-center justify-between gap-3">
        <a href="/" class="text-xl font-bold flex items-center gap-2">
          <span class="bg-white/20 rounded px-2 py-0.5 text-sm">⛓</span> ProcureChain
        </a>
        <nav class="flex gap-3 text-sm items-center flex-wrap">
          <a href="/transparency.html" class="hover:underline">Projects</a>
          <a href="/analytics.html" class="hover:underline">Analytics</a>
          <a href="/contractors.html" class="hover:underline">Contractors</a>
          <a href="/feedback.html" class="hover:underline">Feedback</a>
          <a href="/audit.html" class="hover:underline">Audit</a>
          <a href="/opendata.html" class="hover:underline">Open Data</a>
          ${showDashboard ? '<a href="/dashboard.html" class="hover:underline font-semibold">Dashboard</a>' : ''}
          ${showApprovals ? '<a href="/approvals.html" class="hover:underline font-semibold">Approvals</a>' : ''}
          ${right}
        </nav>
      </div>
    </div>`;
  const lo = document.getElementById('logout');
  if (lo) lo.onclick = () => { clearSession(); window.location.href = '/'; };
}

function requireAuth(roles) {
  const u = getUser();
  if (!u) { window.location.href = '/login.html'; return null; }
  if (roles && !roles.includes(u.role) && u.role !== 'admin') {
    alert('You do not have access to this page.');
    window.location.href = '/';
    return null;
  }
  return u;
}
