"""本地号池面板 —— 表格式，一条一条看每个 FutureSearch 号的状态。

挂在同一个 HTTP 服务上：

    GET  /panel                              面板页面
    GET  /panel/api/state                    号池 + 配置概览
    POST /panel/api/accounts/{email}/check   可用性探测（见下）
    POST /panel/api/accounts/{email}/pause   暂停/启用
    POST /panel/api/accounts/{email}/delete  删除
    POST /panel/api/check-all                一键刷新所有号
    POST /panel/api/replenish                手动补号

「实测」列 = probe_ok / probe_total（每次探测累加）。探测逻辑见 pool.check：
  有 key  → 打上游 /billing，200 即可用
  没 key  → 是 pending 号，重新登录查 cc_user_activations，若上游已放行就顺手补 key

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
<title>FutureSearch Box · 号池</title>
<style>
  :root{--bg:#f6f7f9;--line:#e5e7eb;--txt:#111827;--mut:#6b7280;--brand:#2563eb}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif}
  header{background:#fff;border-bottom:1px solid var(--line);padding:12px 22px;display:flex;
         align-items:center;gap:12px;flex-wrap:wrap;position:sticky;top:0;z-index:9}
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
  .bar{background:#fff;border-bottom:1px solid var(--line);padding:10px 22px;display:flex;
       gap:10px;align-items:center;flex-wrap:wrap;font-size:13px;color:var(--mut)}
  .wrap{padding:16px 22px;max-width:1500px;margin:0 auto}
  table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);
        border-radius:10px;overflow:hidden}
  th{text-align:left;font-weight:600;color:#374151;font-size:13px;padding:11px 14px;
     background:#f9fafb;border-bottom:1px solid var(--line);white-space:nowrap}
  td{padding:10px 14px;border-bottom:1px solid #f1f3f5;vertical-align:middle}
  tr:last-child td{border-bottom:none}
  tbody tr:hover{background:#fafbfc}
  tr.busy{opacity:.5}
  .num{color:var(--mut);font-variant-numeric:tabular-nums}
  .mail{font-weight:600;word-break:break-all}
  .mail:before{content:"\1F310 ";opacity:.55}
  .uuid{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;color:#4b5563;
        word-break:break-all}
  .money{font-weight:650;white-space:nowrap}
  .money small{color:var(--mut);font-weight:400;margin-left:2px}
  .probe{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;white-space:nowrap}
  .probe.ok{color:#15803d}
  .probe.bad{color:#b91c1c}
  .probe.none{color:var(--mut)}
  .st{font-size:12px;border-radius:6px;padding:2px 8px;font-weight:600;white-space:nowrap}
  .st-active{background:#dcfce7;color:#166534}
  .st-pending{background:#fef3c7;color:#92400e}
  .st-paused{background:#e5e7eb;color:#374151}
  .st-dead,.st-exhausted{background:#fee2e2;color:#991b1b}
  .st-checking{background:#dbeafe;color:#1e40af}
  .act{display:flex;gap:6px;justify-content:flex-end}
  .act button{width:30px;height:30px;padding:0;display:grid;place-items:center;font-size:14px}
  .empty{padding:60px;text-align:center;color:var(--mut);background:#fff;
         border:1px solid var(--line);border-radius:10px}
  .toast{position:fixed;right:18px;bottom:18px;background:#111827;color:#fff;
         padding:10px 16px;border-radius:8px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:99}
  .toast.show{opacity:1}
  .login{max-width:420px;margin:80px auto;background:#fff;border:1px solid var(--line);
         border-radius:12px;padding:24px}
  .login input{width:100%;padding:9px 11px;border-radius:8px;font:inherit}
  footer{text-align:center;color:var(--mut);font-size:13px;padding:22px}
  footer a{color:var(--brand);font-weight:600;text-decoration:none}
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
    <option value="credits-desc">余额 ↓</option>
    <option value="credits-asc">余额 ↑</option>
    <option value="status">状态</option>
    <option value="email">邮箱</option>
    <option value="created-desc">最近注册</option>
  </select>
  <span>状态</span>
  <select id="filter">
    <option value="">全部</option>
    <option value="active">active</option>
    <option value="pending">pending</option>
    <option value="paused">paused</option>
    <option value="dead">dead</option>
    <option value="exhausted">exhausted</option>
  </select>
  <input id="q" placeholder="搜索邮箱 / 用户身份…" style="min-width:220px">
  <span id="count"></span>
</div>
<div class="wrap"><div id="app"></div></div>
<footer>
  💡 想找更多免费 API、公益站、羊毛资源？去
  <a href="https://baipiao.org/" target="_blank" rel="noopener">baipiao.org</a> 看看
</footer>
<div class="toast" id="toast"></div>
<script>
const $ = s => document.querySelector(s);
let KEY = localStorage.getItem('fsbox_key') || '';
let ALL = [];
const busy = new Set();

function toast(m){
  const t = $('#toast'); t.textContent = m; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2200);
}
async function api(p, o={}){
  o.headers = Object.assign({'Authorization':'Bearer '+KEY}, o.headers||{});
  const r = await fetch(p, o);
  if (r.status === 401) throw new Error('API Key 不对');
  return r.json();
}
function esc(s){
  return String(s==null?'':s).replace(/[&<>"]/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}
const credits = a => Math.round((Number(a.balance)||0) * 100);

/* 「实测」= probe_ok / probe_total */
function probe(a){
  if (!a.probe_total) return {cls:'none', text:'—', title:'还没测过'};
  const cls = a.probe_code === 200 ? 'ok' : 'bad';
  return {cls, text:a.probe_ok + ' / ' + a.probe_total,
          title:'最近一次 HTTP ' + (a.probe_code == null ? '?' : a.probe_code)
                + ' · ' + (a.probe_note || '')};
}
function statusCell(a){
  const st = busy.has(a.email) ? 'checking' : (a.status || 'active');
  return '<span class="st st-' + esc(st) + '">' + esc(st) + '</span>';
}

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
  rows.sort({
    'credits-desc': (a,b) => credits(b) - credits(a),
    'credits-asc':  (a,b) => credits(a) - credits(b),
    'status':       (a,b) => String(a.status).localeCompare(String(b.status)) ||
                             credits(b) - credits(a),
    'email':        (a,b) => String(a.email).localeCompare(String(b.email)),
    'created-desc': (a,b) => (b.created_at||0) - (a.created_at||0),
  }[by]);
  return rows;
}

function render(){
  const rows = view();
  $('#count').textContent = '显示 ' + rows.length + ' / ' + ALL.length + ' 个';
  if (!rows.length){
    $('#app').innerHTML = '<div class="empty">'
      + (ALL.length ? '没有符合筛选条件的号' : '号池是空的 —— 点右上角「补号」开始注册')
      + '</div>';
    return;
  }
  const body = rows.map((a, i) => {
    const p = probe(a);
    const dis = busy.has(a.email) ? 'disabled' : '';
    const bd  = busy.has(a.email) ? 'busy' : '';
    const e   = esc(a.email);
    const bal = (a.balance == null) ? '—'
              : a.balance + '<small>美元</small>';
    return '<tr class="' + bd + '">'
      + '<td class="num">' + (i+1) + '</td>'
      + '<td class="mail">' + e + '</td>'
      + '<td class="uuid" title="' + esc(a.user_id) + '">' + esc(a.user_id || '—') + '</td>'
      + '<td class="money">' + bal + '</td>'
      + '<td class="probe ' + p.cls + '" title="' + esc(p.title) + '">' + esc(p.text) + '</td>'
      + '<td>' + statusCell(a) + '</td>'
      + '<td><div class="act">'
      +   '<button title="检查可用性" ' + dis + ' onclick="act(\'check\',\'' + e + '\')">✓</button>'
      +   '<button title="暂停/启用" ' + dis + ' onclick="act(\'pause\',\'' + e + '\')">'
      +      (a.status === 'paused' ? '▶' : '⏸') + '</button>'
      +   '<button title="删除" ' + dis + ' onclick="act(\'delete\',\'' + e + '\')">✕</button>'
      + '</div></td>'
      + '</tr>';
  }).join('');
  $('#app').innerHTML = '<table><thead><tr>'
    + '<th style="width:52px">#</th>'
    + '<th>邮箱</th>'
    + '<th style="width:34%">用户身份</th>'
    + '<th style="width:110px">余额</th>'
    + '<th style="width:110px">实测</th>'
    + '<th style="width:100px">状态</th>'
    + '<th style="width:140px"></th>'
    + '</tr></thead><tbody>' + body + '</tbody></table>';
}

async function load(){
  let d;
  try { d = await api('/panel/api/state'); }
  catch(e){ return renderLogin(e.message); }
  ALL = d.accounts;
  const p = d.pool;
  $('#s1').innerHTML = '可用 <b>' + p.usable + '</b> / 共 <b>' + p.total
    + '</b> · 余额合计 <b>$' + p.balance_usd + '</b>'
    + (p.registering ? ' · 补号中 <b>' + p.registering + '</b>' : '');
  render();
}
function renderLogin(err){
  $('#app').innerHTML = '<div class="login">'
    + '<h2 style="margin-top:0;font-size:16px">填入 API Key</h2>'
    + '<p style="color:#6b7280;font-size:13px">在 data/config.json 的 <code>api_key</code> '
    + '字段' + (err ? '（' + esc(err) + '）' : '') + '</p>'
    + '<input id="k" placeholder="sk-fsbox-...">'
    + '<div style="margin-top:12px"><button class="primary" onclick="saveKey()">进入</button></div>'
    + '</div>';
}
function saveKey(){
  const v = $('#k').value.trim(); if (!v) return;
  KEY = v; localStorage.setItem('fsbox_key', v); load();
}

async function act(kind, email){
  try{
    if (kind === 'delete' && !confirm('删除 ' + email + ' ？')) return;
    if (kind === 'check'){ busy.add(email); render(); }
    const r = await api('/panel/api/accounts/' + encodeURIComponent(email) + '/' + kind,
                        {method:'POST'});
    toast(kind === 'check'
      ? (r.ok ? '可用 (HTTP ' + r.code + ')' : '不可用：' + (r.note || r.code))
      : kind === 'pause' ? '已切换状态' : '已删除');
    busy.delete(email); load();
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
  const b = $('#btn-checkall'); b.disabled = true; b.textContent = '检查中…';
  ALL.forEach(a => busy.add(a.email)); render();
  try{
    const r = await api('/panel/api/check-all', {method:'POST'});
    toast('检查完 ' + r.checked + ' 个，' + r.ok + ' 个可用');
  }catch(e){ toast(String(e.message||e)); }
  busy.clear(); b.disabled = false; b.textContent = '一键刷新所有号'; load();
};
['sort','filter','q'].forEach(id => {
  $('#'+id).addEventListener('input', render);
  $('#'+id).addEventListener('change', render);
});
load();
setInterval(()=>{ if (KEY && !busy.size) load(); }, 15000);
</script>
</body>
</html>
"""


def page() -> bytes:
    return _PAGE.encode("utf-8")


def api_state(gw) -> dict:
    """号池 + 配置概览（面板用）。"""
    accts = sorted(gw.pool.accounts,
                   key=lambda a: (a.get("status") not in ("active", "pending"),
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
            "has_key": bool(a.get("api_key")),
            "probe_ok": a.get("probe_ok") or 0,
            "probe_total": a.get("probe_total") or 0,
            "probe_code": a.get("probe_code"),
            "probe_note": a.get("probe_note") or "",
            "ref_code": a.get("ref_code") or "",
            "ref_parent": a.get("ref_parent") or "",
            "created_at": a.get("created_at"),
            "last_check": a.get("last_check"),
        } for a in accts],
    }
