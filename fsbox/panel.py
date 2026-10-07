"""本地号池面板 —— 卡片式，看每个 FutureSearch 号的积分/UID/状态，可逐个或批量操作。

挂在同一个 HTTP 服务上：

    GET  /panel                              面板页面
    GET  /panel/api/state                    号池 + 配置概览
    POST /panel/api/accounts/{email}/check   单个健康检查（真打上游 /billing）
    POST /panel/api/accounts/{email}/pause   暂停/启用
    POST /panel/api/accounts/{email}/delete  删除
    POST /panel/api/check-all                一键刷新所有号
    POST /panel/api/replenish                手动补号

鉴权：面板 API 要 `Authorization: Bearer <config.api_key>`（页面首次让你填一次，
存 localStorage）。服务默认只绑 127.0.0.1，但别把它暴露到公网。


💡 想找更多免费 API、公益站、羊毛资源？→ https://baipiao.org/
"""

# 1 积分 = 1 美分（上游只给美元余额，没有独立积分字段；1837≈$18.37 印证此换算）
CREDITS_PER_DOLLAR = 100

_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FutureSearch Box · 号池面板</title>
<style>
  :root{--bg:#f6f7f9;--card:#fff;--line:#e5e7eb;--txt:#111827;--mut:#6b7280;
        --brand:#2563eb;--ok:#16a34a;--bad:#dc2626;--warn:#d97706}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"PingFang SC","Microsoft YaHei",sans-serif}
  header{background:#fff;border-bottom:1px solid var(--line);padding:12px 22px;
         display:flex;align-items:center;gap:12px;flex-wrap:wrap;position:sticky;top:0;z-index:9}
  h1{font-size:16px;margin:0;font-weight:650;white-space:nowrap}
  .stat{color:var(--mut);font-size:13px;white-space:nowrap}
  .stat b{color:var(--txt);font-size:15px}
  .spacer{flex:1}
  button,select,input{font:inherit;cursor:pointer;border:1px solid var(--line);
         background:#fff;border-radius:8px;padding:6px 12px;color:var(--txt)}
  select,input{cursor:auto;padding:6px 9px}
  button:hover{background:#f3f4f6}
  button.primary{background:var(--brand);border-color:var(--brand);color:#fff}
  button.primary:hover{filter:brightness(.94)}
  button:disabled{opacity:.5;cursor:not-allowed}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));
        gap:14px;padding:18px 22px;max-width:1400px;margin:0 auto}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;
        transition:opacity .15s}
  .card.busy{opacity:.55}
  .row{display:flex;align-items:center;gap:10px}
  .badge{background:#4f46e5;color:#fff;border-radius:6px;padding:2px 9px;font-size:12px;
         font-weight:600;white-space:nowrap}
  .email{font-weight:650;font-size:15px;word-break:break-all}
  .actions{margin-left:auto;display:flex;gap:6px}
  .actions button{width:34px;height:34px;padding:0;display:grid;place-items:center;font-size:15px}
  .uid{color:var(--mut);font-size:12.5px;margin-top:12px;font-family:ui-monospace,Menlo,Consolas,monospace}
  .bottom{display:flex;align-items:baseline;gap:10px;margin-top:14px;flex-wrap:wrap}
  .credits{font-size:26px;font-weight:700}
  .credits small{font-size:13px;font-weight:500;color:var(--mut);margin-left:6px}
  .cap{margin-left:auto;color:var(--mut);background:#f3f4f6;border-radius:6px;
       padding:2px 9px;font-size:12px}
  .st{font-size:12px;border-radius:6px;padding:2px 8px;font-weight:600}
  .st-active{background:#dcfce7;color:#166534}
  .st-paused{background:#fef3c7;color:#92400e}
  .st-dead,.st-exhausted{background:#fee2e2;color:#991b1b}
  .st-checking{background:#dbeafe;color:#1e40af}
  .empty{padding:60px 22px;text-align:center;color:var(--mut)}
  .toast{position:fixed;right:18px;bottom:18px;background:#111827;color:#fff;
         padding:10px 16px;border-radius:8px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:99}
  .toast.show{opacity:1}
  .login{max-width:420px;margin:80px auto;background:#fff;border:1px solid var(--line);
         border-radius:12px;padding:24px}
  .login input{width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:8px;font:inherit}
  .bar{background:#fff;border-bottom:1px solid var(--line);padding:10px 22px;display:flex;
       gap:10px;align-items:center;flex-wrap:wrap;font-size:13px;color:var(--mut)}
</style>
</head>
<body>
<header>
  <h1>FutureSearch Box</h1>
  <span class="stat" id="s1">—</span>
  <span class="spacer"></span>
  <button id="btn-refresh">刷新</button>
  <button id="btn-checkall">一键刷新所有号</button>
  <button id="btn-add" class="primary">补号</button>
</header>
<div class="bar">
  <span>排序</span>
  <select id="sort">
    <option value="credits-desc">积分 ↓（多→少）</option>
    <option value="credits-asc">积分 ↑（少→多）</option>
    <option value="status">状态</option>
    <option value="email">邮箱</option>
    <option value="created-desc">最近注册</option>
  </select>
  <span>状态</span>
  <select id="filter">
    <option value="">全部</option>
    <option value="active">active</option>
    <option value="paused">paused</option>
    <option value="dead">dead</option>
    <option value="exhausted">exhausted</option>
  </select>
  <input id="q" placeholder="搜索邮箱 / UID…" style="min-width:220px">
  <span id="count"></span>
</div>
<div id="app"></div>
<div class="toast" id="toast"></div>
<footer style="text-align:center;color:#6b7280;font-size:13px;padding:26px 20px 40px">
  💡 想找更多免费 API、公益站、羊毛资源？去
  <a href="https://baipiao.org/" target="_blank" rel="noopener"
     style="color:#2563eb;font-weight:600;text-decoration:none">baipiao.org</a>
  看看
</footer>
<script>
const $ = s => document.querySelector(s);
let KEY = localStorage.getItem('fsbox_key') || '';
let ALL = [];                       // 最近一次拉到的全部账号
const busy = new Set();             // 正在检查的邮箱，用于置灰

function toast(msg){
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2200);
}
async function api(path, opt={}){
  opt.headers = Object.assign({'Authorization':'Bearer '+KEY}, opt.headers||{});
  const r = await fetch(path, opt);
  if(r.status === 401) throw new Error('API Key 不对');
  return r.json();
}
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function uid(a){
  const src = a.user_id || a.email || '';
  return src.length > 14 ? src.slice(0,6)+'…'+src.slice(-4) : src;
}
const credits = a => Math.round((Number(a.balance)||0) * 100);

/* ---- 排序 + 筛选 ---- */
function view(){
  const q = $('#q').value.trim().toLowerCase();
  const st = $('#filter').value;
  let rows = ALL.filter(a => {
    if (st && a.status !== st) return false;
    if (q && !((a.email||'').toLowerCase().includes(q) ||
               (a.user_id||'').toLowerCase().includes(q))) return false;
    return true;
  });
  const by = $('#sort').value;
  const cmp = {
    'credits-desc': (a,b) => credits(b)-credits(a),
    'credits-asc':  (a,b) => credits(a)-credits(b),
    'status':       (a,b) => String(a.status).localeCompare(String(b.status)) ||
                             credits(b)-credits(a),
    'email':        (a,b) => String(a.email).localeCompare(String(b.email)),
    'created-desc': (a,b) => (b.created_at||0)-(a.created_at||0),
  }[by];
  rows.sort(cmp);
  return rows;
}

function card(a){
  const st = busy.has(a.email) ? 'checking' : (a.status || 'active');
  const dis = busy.has(a.email) ? 'disabled' : '';
  const cd = busy.has(a.email) ? 'busy' : '';
  return `<div class="card ${cd}" data-email="${esc(a.email)}">
    <div class="row">
      <span class="badge">FutureSearch</span>
      <span class="email">${esc(a.email)}</span>
      <div class="actions">
        <button title="健康检查" ${dis} onclick="act('check','${esc(a.email)}')">✓</button>
        <button title="刷新余额" ${dis} onclick="act('check','${esc(a.email)}')">↻</button>
        <button title="暂停/启用" ${dis} onclick="act('pause','${esc(a.email)}')">${a.status==='paused'?'▶':'⏸'}</button>
        <button title="删除" ${dis} onclick="act('delete','${esc(a.email)}')">✕</button>
      </div>
    </div>
    <div class="uid">UID: ${esc(uid(a))}</div>
    <div class="bottom">
      <span class="credits">${credits(a)}<small>可用积分</small></span>
      <span class="st st-${esc(st)}">${esc(st)}</span>
      <span class="cap">${a.email_source==='vip215'?'真收件箱':'本地地址'}</span>
    </div>
  </div>`;
}

function render(){
  const rows = view();
  $('#count').textContent = `显示 ${rows.length} / ${ALL.length} 个`;
  $('#app').innerHTML = rows.length
    ? `<div class="grid">${rows.map(card).join('')}</div>`
    : (ALL.length ? `<div class="empty">没有符合筛选条件的号</div>`
                  : `<div class="empty">号池是空的 —— 点右上角「补号」开始注册</div>`);
}

async function load(){
  let d;
  try { d = await api('/panel/api/state'); }
  catch(e){ return renderLogin(e.message); }
  ALL = d.accounts;
  const p = d.pool;
  $('#s1').innerHTML = `可用 <b>${p.usable}</b> / 共 <b>${p.total}</b> · 积分合计 <b>${Math.round(p.balance_usd*100)}</b>`
                     + (p.registering ? ` · 补号中 <b>${p.registering}</b>` : '');
  render();
}
function renderLogin(err){
  $('#app').innerHTML = `<div class="login">
    <h2 style="margin-top:0;font-size:16px">填入 API Key</h2>
    <p style="color:#6b7280;font-size:13px">在 data/config.json 的 <code>api_key</code> 字段${err?' （'+esc(err)+'）':''}</p>
    <input id="k" placeholder="sk-fsbox-..." />
    <div style="margin-top:12px"><button class="primary" onclick="saveKey()">进入</button></div>
  </div>`;
}
function saveKey(){
  const v = $('#k').value.trim(); if(!v) return;
  KEY = v; localStorage.setItem('fsbox_key', v); load();
}

async function act(kind, email){
  try{
    if(kind === 'delete' && !confirm('删除 ' + email + '？')) return;
    if(kind === 'check'){ busy.add(email); render(); }
    await api(`/panel/api/accounts/${encodeURIComponent(email)}/${kind}`, {method:'POST'});
    toast(kind === 'check' ? '已刷新' : kind === 'pause' ? '已切换状态' : '已删除');
    busy.delete(email);
    load();
  }catch(e){ busy.delete(email); toast(String(e.message||e)); render(); }
}

$('#btn-refresh').onclick = load;
$('#btn-add').onclick = async () => {
  toast('开始补号…');
  try{ await api('/panel/api/replenish', {method:'POST'}); toast('补号任务已启动'); }
  catch(e){ toast(String(e.message||e)); }
  setTimeout(load, 1500);
};
$('#btn-checkall').onclick = async () => {
  const btn = $('#btn-checkall'); btn.disabled = true; btn.textContent = '检查中…';
  ALL.forEach(a => busy.add(a.email)); render();
  try{
    const r = await api('/panel/api/check-all', {method:'POST'});
    toast(`检查完 ${r.checked} 个，${r.ok} 个正常`);
  }catch(e){ toast(String(e.message||e)); }
  busy.clear(); btn.disabled = false; btn.textContent = '一键刷新所有号';
  load();
};

['sort','filter','q'].forEach(id => {
  $('#'+id).addEventListener('input', render);
  $('#'+id).addEventListener('change', render);
});
load();
setInterval(()=>{ if(KEY && !busy.size) load(); }, 15000);
</script>
</body>
</html>
"""


def page() -> bytes:
    return _PAGE.encode("utf-8")


def api_state(gw) -> dict:
    """号池 + 配置概览（面板用）。"""
    accts = sorted(gw.pool.accounts,
                   key=lambda a: (a.get("status") != "active",
                                  -float(a.get("balance") or 0)))
    return {
        "pool": gw.pool.status(),
        "model": gw.model,
        "credits_per_dollar": CREDITS_PER_DOLLAR,
        "accounts": [{
            "email": a.get("email"),
            "user_id": a.get("user_id"),
            "status": a.get("status"),
            "balance": a.get("balance"),
            "email_source": a.get("email_source", "local"),
            "created_at": a.get("created_at"),
            "last_check": a.get("last_check"),
        } for a in accts],
    }
