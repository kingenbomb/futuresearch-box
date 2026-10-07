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
<title>FutureSearch Box · 后台</title>
<style>
  :root{--bg:#f5f6f8;--card:#fff;--line:#e6e8ec;--line2:#f1f3f5;--txt:#111827;--mut:#6b7280;
        --brand:#2563eb;--ok:#16a34a;--warn:#d97706;--bad:#dc2626}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif}
  button,select,input{font:inherit;cursor:pointer;border:1px solid var(--line);background:#fff;
       border-radius:8px;padding:6px 12px;color:var(--txt)}
  select,input{cursor:auto;padding:6px 9px}
  button:hover{background:#f3f4f6}
  button.primary{background:var(--brand);border-color:var(--brand);color:#fff}
  button.primary:hover{filter:brightness(.94)}
  button:disabled{opacity:.5;cursor:not-allowed}
  .spacer{flex:1}
  .lbl{color:var(--mut);font-size:13px}
  .muted{color:var(--mut)}
  .num,.cval,.money{font-variant-numeric:tabular-nums}

  header{background:#fff;border-bottom:1px solid var(--line);padding:10px 22px;display:flex;
         align-items:center;gap:10px;flex-wrap:wrap;position:sticky;top:0;z-index:9}
  .brand{display:flex;align-items:baseline;gap:8px}
  .brand h1{font-size:16px;margin:0;font-weight:700;letter-spacing:.2px}
  .brand .sub{font-size:12px;color:var(--mut)}
  .hstat{font-size:13px;color:var(--mut);white-space:nowrap}
  .hstat b{color:var(--txt)}

  main{padding:16px 22px 40px;max-width:1600px;margin:0 auto}

  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:12px;margin-bottom:16px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
  .clabel{font-size:12px;color:var(--mut);margin-bottom:6px}
  .cval{font-size:22px;font-weight:700;letter-spacing:.2px}
  .csub{font-size:11.5px;color:var(--mut);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .card.ok .cval{color:var(--ok)} .card.warn .cval{color:var(--warn)} .card.bad .cval{color:var(--bad)}

  .panelbox{background:var(--card);border:1px solid var(--line);border-radius:12px;
            padding:10px 14px;margin-bottom:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .panelbox .grp{display:flex;gap:8px;align-items:center}
  .cfgline{font-size:12.5px;color:var(--mut);width:100%;margin-top:2px}
  .cfgline b{color:#374151;font-weight:600}

  .tablecard{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
  table{width:100%;border-collapse:collapse}
  th{text-align:left;font-weight:600;color:#374151;font-size:12.5px;padding:11px 14px;
     background:#f9fafb;border-bottom:1px solid var(--line);white-space:nowrap;position:sticky;top:56px}
  td{padding:10px 14px;border-bottom:1px solid var(--line2);vertical-align:middle}
  tr:last-child td{border-bottom:none}
  tbody tr:hover{background:#fafbfc}
  tr.busy{opacity:.5}
  .mail{font-weight:600;word-break:break-all}
  .mail:before{content:"\1F310 ";opacity:.55}
  .uuid{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;color:#4b5563;word-break:break-all}
  .money{font-weight:650;white-space:nowrap}
  .money small{color:var(--mut);font-weight:400;margin-left:2px}
  .money .cr{color:var(--mut);font-weight:400;font-size:11.5px;margin-left:6px}
  .probe{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;white-space:nowrap}
  .probe.ok{color:var(--ok)} .probe.bad{color:var(--bad)} .probe.none{color:var(--mut)}
  .st{font-size:12px;border-radius:6px;padding:2px 8px;font-weight:600;white-space:nowrap}
  .st-active{background:#dcfce7;color:#166534}
  .st-pending{background:#fef3c7;color:#92400e}
  .st-paused{background:#e5e7eb;color:#374151}
  .st-dead,.st-exhausted{background:#fee2e2;color:#991b1b}
  .st-checking{background:#dbeafe;color:#1e40af}
  .act{display:flex;gap:6px;justify-content:flex-end}
  .actbtn{width:30px;height:30px;padding:0;display:grid;place-items:center;font-size:14px}
  .empty{padding:54px;text-align:center;color:var(--mut)}

  .pager{display:flex;gap:6px;align-items:center;flex-wrap:wrap;
         padding:11px 14px;border-top:1px solid var(--line);background:#fcfcfd}
  .pager .pg{min-width:34px;height:32px;padding:0 10px}
  .pager .pgcur{min-width:34px;height:32px;display:grid;place-items:center;padding:0 10px;
         background:var(--brand);border:1px solid var(--brand);color:#fff;border-radius:8px;font-weight:600}
  .pager .pgdots{padding:0 4px;color:var(--mut)}
  .pager .jump{color:var(--mut);font-size:13px}

  .login{max-width:420px;margin:80px auto;background:#fff;border:1px solid var(--line);
         border-radius:12px;padding:24px}
  .login input{width:100%;padding:9px 11px;border-radius:8px;font:inherit}
  footer{text-align:center;color:var(--mut);font-size:13px;padding:22px}
  footer a{color:var(--brand);font-weight:600;text-decoration:none}
  .toast{position:fixed;right:18px;bottom:18px;background:#111827;color:#fff;padding:10px 16px;
         border-radius:8px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:99}
  .toast.show{opacity:1}

  .models{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:12px}
  .mhead{display:flex;align-items:center;gap:10px;padding:11px 14px;background:#f9fafb;
         border-bottom:1px solid var(--line);font-size:13px;flex-wrap:wrap}
  .mlist{max-height:340px;overflow:auto;padding:4px 0}
  .mrow{display:flex;gap:12px;padding:5px 14px;cursor:pointer;font-size:13px}
  .mrow:hover{background:#eff6ff}
  .mrow code{font-family:ui-monospace,Menlo,Consolas,monospace;min-width:290px;color:#1d4ed8}
  .mllm{color:var(--mut);font-size:12px}
  @media (max-width:1100px){ th{position:static} .uuid{max-width:180px} }
</style>
</head>
<body>
<header>
  <div class="brand"><h1>FutureSearch Box</h1><span class="sub">号池后台</span></div>
  <span class="hstat" id="poolLine">—</span>
  <span class="spacer"></span>
  <span class="lbl">档位</span>
  <select id="effort" title="走默认模型时的 effort_level；选具体底层模型时改用 iteration_budget">
    <option value="low">low（免费/快）</option>
    <option value="medium">medium</option>
    <option value="high">high（带引用，约$0.40/次）</option>
  </select>
  <button id="btn-refresh">刷新</button>
</header>

<main>
  <section class="cards" id="cards"></section>

  <div class="panelbox">
    <div class="grp">
      <span class="lbl">补号</span>
      <input id="reg-n" type="number" min="1" max="50" value="5" style="width:74px">
      <span class="lbl">个</span>
      <button id="btn-add" class="primary">开始注册</button>
    </div>
    <div class="grp">
      <button id="btn-checkall">一键刷新所有号</button>
      <button id="btn-rescue">救援 pending</button>
      <button id="btn-models">可选模型</button>
    </div>
    <div class="grp">
      <span class="lbl">数据</span>
      <button id="btn-exp-acc" title="导出全部账号（含 api_key，敏感）">导出账号</button>
      <button id="btn-imp-acc" title="导入账号（JSON / JSONL，按 email 去重）">导入账号</button>
      <button id="btn-exp-inv" title="导出邀请码（激活 token + 折扣码树）">导出邀请码</button>
      <button id="btn-imp-inv" title="导入邀请码">导入邀请码</button>
    </div>
    <div class="cfgline" id="cfgline"></div>
  </div>

  <div class="panelbox">
    <span class="lbl">状态</span>
    <select id="f-status">
      <option value="">全部</option>
      <option value="active">active</option>
      <option value="pending">pending</option>
      <option value="paused">paused</option>
      <option value="dead">dead</option>
      <option value="exhausted">exhausted</option>
    </select>
    <span class="lbl">排序</span>
    <select id="f-sort">
      <option value="credits-desc">余额 ↓</option>
      <option value="credits-asc">余额 ↑</option>
      <option value="status">状态</option>
      <option value="email">邮箱</option>
      <option value="created-desc">最近注册</option>
    </select>
    <span class="lbl">每页</span>
    <select id="f-size">
      <option value="10">10</option>
      <option value="20">20</option>
      <option value="50">50</option>
      <option value="100">100</option>
    </select>
    <input id="f-q" placeholder="搜索邮箱 / 用户身份…" style="min-width:220px">
    <span class="spacer"></span>
    <span class="muted" id="count"></span>
  </div>

  <div id="models" style="display:none"></div>

  <section class="tablecard">
    <table>
      <thead><tr>
        <th style="width:56px">#</th>
        <th>邮箱</th>
        <th style="width:34%">用户身份</th>
        <th style="width:150px">余额 / 积分</th>
        <th style="width:110px">实测</th>
        <th style="width:104px">状态</th>
        <th style="width:140px"></th>
      </tr></thead>
      <tbody id="tbody"></tbody>
    </table>
    <div class="pager" id="pager"></div>
  </section>
</main>

<footer>
  💡 想找更多免费 API、公益站、羊毛资源？去
  <a href="https://baipiao.org/" target="_blank" rel="noopener">baipiao.org</a> 看看
</footer>
<div class="toast" id="toast"></div>

<script>
const $ = s => document.querySelector(s);
let KEY = localStorage.getItem('fsbox_key') || '';
let MODELS = null;
let LOADING = false;
let LAST = null;                 // 最近一次服务端返回
const busy = new Set();
// 当前视图 = 服务端分页/筛选参数（改了就重新拉，不在前端切页）
const VIEW = { page: 1, size: 20, sort: 'credits-desc', status: '', q: '' };

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

/* ---- 拼查询串（分页在服务端）---- */
function qs(){
  const p = new URLSearchParams();
  p.set('page', VIEW.page); p.set('size', VIEW.size); p.set('sort', VIEW.sort);
  if (VIEW.status) p.set('status', VIEW.status);
  if (VIEW.q) p.set('q', VIEW.q);
  return p.toString();
}

/* ---- 统计卡片 ---- */
function card(label, value, sub, tone){
  return '<div class="card ' + (tone||'') + '"><div class="clabel">' + label + '</div>'
       + '<div class="cval">' + value + '</div>'
       + (sub ? '<div class="csub">' + sub + '</div>' : '') + '</div>';
}
function renderCards(s, c){
  const bs = s.by_status || {};
  const bad = (bs.dead||0) + (bs.paused||0) + (bs.exhausted||0);
  $('#cards').innerHTML =
      card('总账号', s.total, '上限 ' + c.max_accounts)
    + card('可用', s.usable, '下限 ' + c.min_accounts + ' · 目标 ' + c.target_accounts, 'ok')
    + card('待激活', bs.pending||0, bs.pending ? '点「救援 pending」' : '无', bs.pending ? 'warn' : '')
    + card('异常', bad, 'dead ' + (bs.dead||0) + ' · 暂停 ' + (bs.paused||0) + ' · 耗尽 ' + (bs.exhausted||0), bad ? 'bad' : '')
    + card('总余额', '$' + Number(s.balance_total_usd||0).toFixed(2), '可用号 $' + Number(s.balance_active_usd||0).toFixed(2))
    + card('总积分', Number(s.credits_total||0).toLocaleString(), '1 积分 = 1 美分')
    + card('邀请池', s.invites_available, '待用邀请 token')
    + card('可选模型', c.n_models, '档位 ' + c.effort_level);
  $('#poolLine').innerHTML = '总 <b>' + s.total + '</b> · 可用 <b>' + s.usable + '</b>'
    + (s.registering ? ' · 补号中 <b>' + s.registering + '</b>' : '');
  $('#cfgline').innerHTML = '模型 <b>' + esc(c.model) + '</b> · 档位 <b>' + esc(c.effort_level)
    + '</b> · 迭代预算 <b>' + c.iteration_budget + '</b>（0=自动）· 带推理 <b>' + (c.include_reasoning?'开':'关')
    + '</b> · 邀请扇出 <b>' + c.invite_fanout + '</b> · 自动补号 <b>' + (c.auto_register?'开':'关')
    + '</b> · 邀请激活 <b>' + (c.invite_activation?'开':'关') + '</b>';
}

/* ---- 表格（只渲染当前页）---- */
function renderTable(rows){
  const pg = LAST.page;
  $('#count').textContent = '共 ' + pg.total_items + ' 条';
  if (!rows.length){
    $('#tbody').innerHTML = '<tr><td colspan="7" class="empty">没有符合条件的号</td></tr>';
    return;
  }
  const base = (pg.index - 1) * pg.size;
  $('#tbody').innerHTML = rows.map((a, i) => {
    const p = probe(a);
    const dis = busy.has(a.email) ? 'disabled' : '';
    const bal = (a.balance == null) ? '—'
              : a.balance + '<small>美元</small><span class="cr">' + (a.credits||0) + '分</span>';
    return '<tr class="' + (busy.has(a.email) ? 'busy' : '') + '">'
      + '<td class="num">' + (base + i + 1) + '</td>'
      + '<td class="mail">' + esc(a.email) + '</td>'
      + '<td class="uuid" title="' + esc(a.user_id) + '">' + esc(a.user_id || '—') + '</td>'
      + '<td class="money">' + bal + '</td>'
      + '<td class="probe ' + p.cls + '" title="' + esc(p.title) + '">' + esc(p.text) + '</td>'
      + '<td>' + statusCell(a) + '</td>'
      + '<td><div class="act">'
      +   '<button class="actbtn" data-act="check" data-email="' + esc(a.email) + '" title="检查可用性" ' + dis + '>✓</button>'
      +   '<button class="actbtn" data-act="pause" data-email="' + esc(a.email) + '" title="暂停/启用" ' + dis + '>' + (a.status === 'paused' ? '▶' : '⏸') + '</button>'
      +   '<button class="actbtn" data-act="delete" data-email="' + esc(a.email) + '" title="删除" ' + dis + '>✕</button>'
      + '</div></td></tr>';
  }).join('');
}

/* ---- 分页控件 ---- */
function renderPager(pg){
  const total = pg.total_pages, cur = pg.index;
  const nums = [];
  const s = Math.max(1, cur - 2), e = Math.min(total, cur + 2);
  if (s > 1){ nums.push(1); if (s > 2) nums.push('…'); }
  for (let i = s; i <= e; i++) nums.push(i);
  if (e < total){ if (e < total - 1) nums.push('…'); nums.push(total); }
  const btn = (label, page, dis) =>
    '<button class="pg" ' + (dis ? 'disabled' : '') + ' data-page="' + page + '">' + label + '</button>';
  $('#pager').innerHTML =
      btn('«', 1, cur <= 1) + btn('上一页', cur - 1, cur <= 1)
    + nums.map(n => n === '…' ? '<span class="pgdots">…</span>'
        : (n === cur ? '<span class="pgcur">' + n + '</span>'
                     : '<button class="pg" data-page="' + n + '">' + n + '</button>')).join('')
    + btn('下一页', cur + 1, cur >= total) + btn('»', total, cur >= total)
    + '<span class="jump">跳至</span>'
    + '<input id="jump" type="number" min="1" max="' + total + '" value="' + cur + '" style="width:66px">'
    + '<span class="jump">页 / 共 ' + total + ' 页</span>';
  $('#pager').querySelectorAll('.pg[data-page]').forEach(b => {
    b.onclick = () => { VIEW.page = parseInt(b.dataset.page, 10) || 1; load(); };
  });
  const j = $('#jump');
  if (j) j.onchange = () => {
    const v = Math.max(1, Math.min(total, parseInt(j.value, 10) || 1));
    VIEW.page = v; load();
  };
}

/* ---- 拉数据并渲染 ---- */
async function load(){
  if (LOADING) return;
  LOADING = true;
  let d;
  try { d = await api('/panel/api/state?' + qs()); }
  catch(e){ LOADING = false; return renderLogin(e.message); }
  LOADING = false;
  LAST = d;
  renderCards(d.stats, d.config);
  renderTable(d.accounts);
  renderPager(d.page);
  VIEW.page = d.page.index; VIEW.size = d.page.size;
  if (document.activeElement !== $('#f-q')) $('#f-q').value = VIEW.q;
  $('#f-status').value = VIEW.status;
  $('#f-sort').value = VIEW.sort;
  $('#f-size').value = String(VIEW.size);
  $('#effort').value = d.config.effort_level;
  const pend = (d.stats.by_status || {}).pending || 0;
  $('#btn-rescue').textContent = pend ? ('救援 pending (' + pend + ')') : '救援 pending';
  $('#btn-models').textContent = '可选模型 (' + d.config.n_models + ')';
}

function renderLogin(err){
  $('#cards').innerHTML = ''; $('#tbody').innerHTML = ''; $('#pager').innerHTML = '';
  $('#app') ; // no-op
  const host = $('#cards').parentElement;
  $('#tbody').innerHTML = '';
  $('main').insertAdjacentHTML('afterbegin',
    '<div class="login" id="loginbox"><h2 style="margin-top:0;font-size:16px">填入 API Key</h2>'
    + '<p style="color:#6b7280;font-size:13px">在 data/config.json 的 <code>api_key</code> 字段'
    + (err ? '（' + esc(err) + '）' : '') + '</p>'
    + '<input id="k" placeholder="sk-fsbox-...">'
    + '<div style="margin-top:12px"><button class="primary" onclick="saveKey()">进入</button></div></div>');
  const old = document.getElementById('loginbox');
  $('#k').onkeydown = e => { if (e.key === 'Enter') saveKey(); };
}
function saveKey(){
  const v = $('#k').value.trim(); if (!v) return;
  KEY = v; localStorage.setItem('fsbox_key', v);
  const lb = document.getElementById('loginbox'); if (lb) lb.remove();
  load();
}

/* ---- 行操作 ---- */
async function act(kind, email){
  try{
    if (kind === 'delete' && !confirm('删除 ' + email + ' ？')) return;
    if (kind === 'check'){ busy.add(email); renderTable(LAST.accounts); }
    const r = await api('/panel/api/accounts/' + encodeURIComponent(email) + '/' + kind,
                        {method:'POST'});
    toast(kind === 'check'
      ? (r.ok ? '可用 (HTTP ' + r.code + ')' : '不可用：' + (r.note || r.code))
      : kind === 'pause' ? '已切换状态' : '已删除');
    busy.delete(email); load();
  }catch(e){ busy.delete(email); toast(String(e.message || e)); load(); }
}
$('#tbody').addEventListener('click', e => {
  const b = e.target.closest('.actbtn');
  if (b) act(b.dataset.act, b.dataset.email);
});

/* ---- 筛选 / 排序 / 每页 / 搜索（服务端分页）---- */
$('#f-status').onchange = () => { VIEW.status = $('#f-status').value; VIEW.page = 1; load(); };
$('#f-sort').onchange   = () => { VIEW.sort = $('#f-sort').value; VIEW.page = 1; load(); };
$('#f-size').onchange   = () => { VIEW.size = parseInt($('#f-size').value, 10) || 20; VIEW.page = 1; load(); };
let qTimer = null;
$('#f-q').oninput = () => {
  clearTimeout(qTimer);
  qTimer = setTimeout(() => { VIEW.q = $('#f-q').value.trim(); VIEW.page = 1; load(); }, 300);
};
$('#btn-refresh').onclick = load;

/* ---- 档位 ---- */
$('#effort').onchange = async () => {
  const v = $('#effort').value;
  try{
    await api('/panel/api/config', {method:'POST', headers:{'Content-Type':'application/json'},
                                    body: JSON.stringify({effort_level: v})});
    toast('档位已设为 ' + v);
  }catch(e){ toast(String(e.message || e)); load(); }
};

/* ---- 救援 ---- */
$('#btn-rescue').onclick = async () => {
  const b = $('#btn-rescue');
  b.disabled = true; b.textContent = '救援中…';
  let r;
  try{ r = await api('/panel/api/rescue', {method:'POST'}); }
  catch(e){ toast(String(e.message || e)); b.disabled = false; b.textContent = '救援 pending'; return; }
  toast(r.pending ? ('开始救援 ' + r.pending + ' 个 pending 号') : '没有 pending 号');
  const t = setInterval(load, 3000);
  setTimeout(()=>{ clearInterval(t); b.disabled = false; b.textContent = '救援 pending'; load(); }, 90000);
};

/* ---- 批量注册 ---- */
$('#btn-add').onclick = async () => {
  const n = Math.max(1, Math.min(50, parseInt($('#reg-n').value, 10) || 5));
  const b = $('#btn-add');
  b.disabled = true; b.textContent = '注册中…';
  toast('开始注册 ' + n + ' 个（下方状态会实时更新）');
  try{
    await api('/panel/api/replenish', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({count:n})});
  }catch(e){ toast(String(e.message || e)); }
  const t = setInterval(load, 3000);
  setTimeout(()=>{ clearInterval(t); b.disabled = false; b.textContent = '开始注册'; load(); },
             n * 40000 + 10000);
};

/* ---- 一键刷新所有号 ---- */
$('#btn-checkall').onclick = async () => {
  const b = $('#btn-checkall'); b.disabled = true; b.textContent = '检查中…';
  (LAST ? LAST.accounts : []).forEach(a => busy.add(a.email)); renderTable(LAST ? LAST.accounts : []);
  try{
    const r = await api('/panel/api/check-all', {method:'POST'});
    toast('检查完 ' + r.checked + ' 个，' + r.ok + ' 个可用');
  }catch(e){ toast(String(e.message || e)); }
  busy.clear(); b.disabled = false; b.textContent = '一键刷新所有号'; load();
};

/* ---- 可选模型 ---- */
async function toggleModels(){
  const box = $('#models');
  if (box.style.display === 'block'){ box.style.display = 'none'; return; }
  box.style.display = 'block';
  if (!MODELS){
    try{ MODELS = (await api('/panel/api/models')).data; }
    catch(e){ box.innerHTML = '<div class="models"><div class="mhead">读不到模型列表：' + esc(e.message) + '</div></div>'; return; }
  }
  renderModels();
}
function renderModels(){
  const q = ($('#mq') ? $('#mq').value : '').trim().toLowerCase();
  const rows = MODELS.filter(m => !q || m.id.toLowerCase().includes(q) ||
                                       (m.llm || '').toLowerCase().includes(q));
  $('#models').innerHTML =
    '<div class="models">'
    + '<div class="mhead"><b>可选底层模型</b>'
    + ' <span class="muted">共 ' + MODELS.length + ' 个 · 点行复制 model 名</span>'
    + '<span class="spacer"></span>'
    + '<input id="mq" placeholder="搜索 opus / gpt / gemini…" value="' + esc(q) + '">'
    + '<button onclick="$(\'#models\').style.display=\'none\'">收起</button></div>'
    + '<div class="mlist">'
    + rows.slice(0, 400).map(m =>
        '<div class="mrow" data-copy="' + esc(m.id) + '" title="点击复制">'
        + '<code>' + esc(m.id) + '</code>'
        + '<span class="mllm">' + esc(m.llm || '(默认)') + '</span>'
        + '</div>').join('')
    + '</div>'
    + '<div class="muted" style="padding:8px 14px">用法：把 model 填成上面任意一个，'
    + '例如 <code>model="claude-opus-5.5"</code>；不填底层模型就用默认 <code>futuresearch-deep</code>。</div>'
    + '</div>';
  if ($('#mq')) $('#mq').oninput = renderModels;
  $('#models').querySelectorAll('.mrow').forEach(r => {
    r.onclick = () => cp(r.dataset.copy);
  });
}
function cp(s){ navigator.clipboard.writeText(s).then(()=>toast('已复制 ' + s)); }
$('#btn-models').onclick = toggleModels;

/* ---- 导出 / 导入（账号信息 / 邀请码）---- */
async function download(what){
  try{
    const r = await fetch('/panel/api/export?what=' + what,
                          {headers:{'Authorization':'Bearer ' + KEY}});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const blob = await r.blob();
    const m = /filename="?([^";]+)"?/.exec(r.headers.get('Content-Disposition') || '');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : ('fsbox-' + what + '.json');
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(()=>URL.revokeObjectURL(a.href), 2000);
    toast('已导出 ' + a.download);
  }catch(e){ toast('导出失败：' + (e.message || e)); }
}
function pickImport(what){
  const inp = document.createElement('input');
  inp.type = 'file'; inp.accept = '.json,.jsonl,.txt,application/json';
  inp.onchange = async () => {
    const f = inp.files && inp.files[0]; if (!f) return;
    const content = await f.text();
    const b = what === 'accounts' ? $('#btn-imp-acc') : $('#btn-imp-inv');
    b.disabled = true;
    try{
      const r = await api('/panel/api/import', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({what: what, content: content})});
      toast('导入完成：新增 ' + (r.added||0) + '，跳过重复 ' + (r.skipped||0));
      load();
    }catch(e){ toast('导入失败：' + (e.message || e)); }
    b.disabled = false;
  };
  inp.click();
}
$('#btn-exp-acc').onclick = () => download('accounts');
$('#btn-imp-acc').onclick = () => pickImport('accounts');
$('#btn-exp-inv').onclick = () => download('invites');
$('#btn-imp-inv').onclick = () => pickImport('invites');

/* 15 秒自动刷新（保持当前页/筛选；请求中不叠加）*/
setInterval(()=>{ if (KEY && !LOADING) load(); }, 15000);

load();
</script>
</body>
</html>
"""


def page() -> bytes:
    return _PAGE.encode("utf-8")


def _credits(a: dict) -> int:
    """号的余额换算成积分（1 积分 = 1 美分）。"""
    return int(round((float(a.get("balance") or 0)) * CREDITS_PER_DOLLAR))


# 排序键（对应面板「排序」下拉；值越小越靠前）
_SORTS = {
    "credits-desc": lambda a: -_credits(a),
    "credits-asc":  lambda a: _credits(a),
    "status":       lambda a: (str(a.get("status") or ""), -_credits(a)),
    "email":        lambda a: str(a.get("email") or ""),
    "created-desc": lambda a: -int(a.get("created_at") or 0),
}


def api_state(gw, params: dict | None = None) -> dict:
    """后台数据：统计 + 分页账号 + 配置概览。

    分页/筛选/排序全在服务端做（前端只传参数、只渲染当前页）。
    params 来自查询串：page(1 起) / size / sort / status / q
    """
    from . import models
    p = params or {}

    def _int(v, d):
        try:
            return int(v)
        except (TypeError, ValueError):
            return d

    page = max(1, _int(p.get("page"), 1))
    size = max(10, min(200, _int(p.get("size"), 20)))
    sort = p.get("sort") if p.get("sort") in _SORTS else "credits-desc"
    status = (p.get("status") or "").strip()
    q = (p.get("q") or "").strip().lower()

    st = gw.pool.status()
    try:
        from .invites import InviteTree, seed_tokens
        invites_available = InviteTree(seed_tokens(gw.cfg)).available()
    except Exception:
        invites_available = 0

    all_accts = list(gw.pool.accounts)

    # ---- 统计（基于全部号，不受筛选/分页影响）----
    bal_total = round(sum(float(a.get("balance") or 0) for a in all_accts), 2)
    bal_active = round(sum(float(a.get("balance") or 0) for a in all_accts
                           if a.get("status") == "active"), 2)
    by_status = {k: 0 for k in ("active", "pending", "paused", "dead", "exhausted")}
    for a in all_accts:
        k = a.get("status") or "?"
        by_status[k] = by_status.get(k, 0) + 1
    stats = {
        "total": len(all_accts),
        "usable": st.get("usable", 0),
        "registering": st.get("registering", 0),
        "by_status": by_status,
        "balance_total_usd": bal_total,
        "balance_active_usd": bal_active,
        "balance_avg_usd": round(bal_total / len(all_accts), 2) if all_accts else 0.0,
        "credits_total": int(round(bal_total * CREDITS_PER_DOLLAR)),
        "credits_per_dollar": CREDITS_PER_DOLLAR,
        "invites_available": invites_available,
    }

    # ---- 筛选 → 搜索 → 排序 → 分页 ----
    rows = all_accts
    if status:
        rows = [a for a in rows if a.get("status") == status]
    if q:
        rows = [a for a in rows
                if q in str(a.get("email") or "").lower()
                or q in str(a.get("user_id") or "").lower()]
    rows = sorted(rows, key=_SORTS[sort])

    total_items = len(rows)
    total_pages = max(1, (total_items + size - 1) // size)
    page = min(page, total_pages)
    page_rows = rows[(page - 1) * size: page * size]

    return {
        "stats": stats,
        "page": {"index": page, "size": size,
                 "total_items": total_items, "total_pages": total_pages},
        "config": {
            "model": gw.model,
            "n_models": len(models.list_models()),
            "effort_level": gw.cfg.get("effort_level", "high"),
            "iteration_budget": int(gw.cfg.get("iteration_budget") or 0),
            "include_reasoning": bool(gw.cfg.get("include_reasoning")),
            "min_accounts": gw.cfg.get("min_accounts"),
            "target_accounts": gw.cfg.get("target_accounts"),
            "max_accounts": gw.cfg.get("max_accounts"),
            "auto_register": bool(gw.cfg.get("auto_register", True)),
            "invite_activation": bool(gw.cfg.get("invite_activation", True)),
            "invite_fanout": gw.cfg.get("invite_fanout", 3),
        },
        "accounts": [{
            "email": a.get("email"),
            "user_id": a.get("user_id"),
            "status": a.get("status"),
            "balance": a.get("balance"),
            "credits": _credits(a),
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
        } for a in page_rows],
    }
