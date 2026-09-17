/* hqz-supervision 前端：登录 / 工作簿列表 / 单页编辑（搜索栏+两列表单自动保存）+ 拍照轨迹 */
'use strict';

const $ = (s) => document.querySelector(s);
const el = (h) => { const d = document.createElement('div'); d.innerHTML = h.trim(); return d.firstChild; };

let cur = null;           // 当前工作簿 {id, headers, rows, config}
let allRows = [];         // 全量行（编辑后同步）
let curIdx = -1;          // 当前小班行索引（详情页上下文：拍照/轨迹/保存）
let formGrid = null;      // 两列表单 jspreadsheet 实例
let loggedIn = false;

/* ── 工具 ── */
function toast(msg, isErr) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.toggle('error', !!isErr);
  t.classList.remove('hidden');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add('hidden'), 2600);
}

/* ── 长期令牌（v0.15）：localStorage 持久，抗 WebView 进程被相机等挤掉 ── */
function getToken() {
  try { return localStorage.getItem('hqz_sup_token') || ''; } catch (e) { return ''; }
}
function setToken(t) {
  try { t ? localStorage.setItem('hqz_sup_token', t) : localStorage.removeItem('hqz_sup_token'); } catch (e) {}
}
/* 需要浏览器直接打开（下载/导出）的链接：把令牌挂到 query，服务端同样认 */
function withToken(url) {
  const t = getToken();
  if (!t) return url;
  return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(t);
}

async function api(url, opt) {
  // 401：会话过期/被清 → 明确提示（页面重载后会自动回到登录页，登录后 hash 仍在，会回到原小班）
  const tok = getToken();
  if (tok) {
    opt = opt || {};
    opt.headers = Object.assign({}, opt.headers || {}, { 'X-Sup-Token': tok });
  }
  const r = await fetch((window.SUP_BASE || '') + url, opt);
  if (r.status === 401) { toast('登录已过期，请重新登录', true); setToken(''); }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/* ── Hash 路由：#/ 工作簿列表 · #/wb/<id> 单页编辑（默认上次小班） · #/wb/<id>/r/<idx> 指定小班 ── */
function nav(hash) { if (location.hash !== hash) location.hash = hash; }

async function route() {
  if (!loggedIn) { show('login'); return; }
  await flushRowSave();     // 任何路由切换前先落库（含手势返回/前进后退/切行）
  let m = location.hash.match(/^#\/wb\/(\d+)\/r\/(\d+)$/);
  if (m) {
    if (!await ensureWb(+m[1])) return;
    openDetail(+m[2]);
    return;
  }
  m = location.hash.match(/^#\/wb\/(\d+)$/);
  if (m) {
    if (!await ensureWb(+m[1])) return;
    const last = loadLast();
    const idx = (last && last.wb === cur.id && allRows[last.idx]) ? last.idx : 0;
    openDetail(idx);
    return;
  }
  const rc = getReturnContext();
  if (rc) {                       // 刚从相机/相册返回（页面被重载）：回到原来那一行
    nav(`#/wb/${rc.wb}/r/${rc.idx || 0}`);
    return;
  }
  show('list');
  await loadList();
}

async function ensureWb(id) {
  if (cur && cur.id === id) return true;
  try { cur = await api(`/api/workbooks/${id}`); }
  catch (e) { toast('打开工作簿失败：' + e.message, true); nav('#/'); return false; }
  allRows = cur.rows.map((r) => r.map((v) => (v == null ? '' : String(v))));
  return true;
}

window.addEventListener('hashchange', route);

/* ── 视图切换 ── */
function show(view) {
  $('#viewLogin').classList.toggle('hidden', view !== 'login');
  $('#viewMain').classList.toggle('hidden', view === 'login');
  $('#viewList').classList.toggle('hidden', view !== 'list');
  $('#viewDetail').classList.toggle('hidden', view !== 'detail');
  $('#btnBack').classList.toggle('hidden', view === 'list');
  $('#pageName').textContent =
    view === 'detail' ? (cur ? `${keyVal() || '详情'} · ${cur.name}` : '') : '工作簿';
  $('#btnBack').onclick = async () => {
    if (trackWatch !== null) autoStopTrackIfRecording('返回列表');   // 互斥（F2）
    await flushRowSave();            // 离场前先落库，否则节流窗口内的编辑会丢
    nav('#/');
  };
}

/* ── 登录 ── */
$('#loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    const r = await api('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('#loginUser').value, password: $('#loginPass').value }),
    });
    // 关键：登录不刷新页面，data-user 必须在此回写（否则验收联动/拍照人拿空用户名）
    $('#whoami').dataset.user = r.user || $('#loginUser').value.trim();
    if (r.token) setToken(r.token);      // 长期令牌（抗进程被杀；cookie 仍作后备）
    // 从 /admin 被弹回登录页的（URL 带 ?next=admin）：登录后送回后台，别停在 App 首页
    if (new URLSearchParams(location.search).get('next') === 'admin') {
      if (r.role === 'admin') { location.href = (window.SUP_BASE || '') + '/admin'; return; }
      history.replaceState(null, '', location.pathname + location.hash);   // 非管理员：去掉 next
    }
    boot();
  } catch (err) {
    $('#loginErr').textContent = err.message;
  }
});

$('#btnLogout').addEventListener('click', async () => {
  await api('/api/logout', { method: 'POST' }).catch(() => {});
  setToken('');                        // 令牌一并清除
  // 退出后必须留在**本应用内**。跳 '/' 是网关首页（所有应用的入口），
  // 用户会掉进别的应用、拿本应用账号反复试密码（2026-09-14 何明星实测踩到此坑）
  location.href = (window.SUP_BASE || '') + '/';
});

/* ── 缓存：记住上次编辑位置（同 hqz-survey last_project 惯例） ── */
function rememberLast() {
  try {
    localStorage.setItem('hqz_sup_last', JSON.stringify(
      { wb: cur.id, idx: curIdx, name: cur.name, xh: keyVal() }));
  } catch (e) {}
}

/* ── 相机返回上下文（v0.17）：相机是独立 Activity，系统可能重载 WebView（URL 里的 hash 会丢），
     用 localStorage 记下"在哪个工作簿/哪一行"，回来后自动回到详情页并补记录像结果。 ── */
function markReturnContext() {
  try {
    localStorage.setItem('hqz_sup_return', JSON.stringify({ wb: cur && cur.id, idx: curIdx, t: Date.now() }));
  } catch (e) {}
}
function getReturnContext() {
  try {
    const o = JSON.parse(localStorage.getItem('hqz_sup_return') || 'null');
    if (!o || !o.wb || Date.now() - (o.t || 0) > 10 * 60 * 1000) return null;
    return o;
  } catch (e) { return null; }
}
function clearReturnContext() {
  try { localStorage.removeItem('hqz_sup_return'); } catch (e) {}
}

function loadLast() {
  try { return JSON.parse(localStorage.getItem('hqz_sup_last') || 'null'); } catch (e) { return null; }
}

function rowVal(key) {
  const i = cur.headers.indexOf(key);
  return (curIdx >= 0 && i >= 0) ? String(allRows[curIdx][i] || '').trim() : '';
}

/* 唯一键列名（v0.23，C11）：参数「unique-key」声明 → 后端 key_column → 兜底「小班号」。
   全链路（详情标题/拍照归档/录像记录/轨迹命名/本地拍摄记录）都用它当行标识，
   不再硬编码「小班号」，以便同一个 App 装不同列的 Excel。 */
function keyCol() {
  if (!cur) return '小班号';
  return (cur.key_column || (cur.config && cur.config['unique-key']) || '小班号').trim();
}

/* 当前行的唯一键值（即"是哪个小班"） */
function keyVal() {
  return rowVal(keyCol());
}

/* ── ① 工作簿列表 ── */
async function loadList() {
  const data = await api('/api/workbooks');
  const box = $('#wbList');
  box.innerHTML = '';
  const last = loadLast();
  $('#lastChip').innerHTML = (last && data.workbooks.some((w) => w.id === last.wb))
    ? `<div class="wb-item last-chip" id="lastItem">
         <span class="wb-name">↩ 上次编辑：${escapeHtml(last.name)} · 小班 ${escapeHtml(last.xh || '-')}</span>
         <span class="spacer"></span><span class="btn primary">继续</span></div>`
    : '';
  const li = $('#lastItem');
  if (li) li.addEventListener('click', () => nav(`#/wb/${last.wb}/r/${last.idx}`));
  if (!data.workbooks.length) {
    box.appendChild(el(`<div class="muted" style="padding:12px">还没有工作簿，请在管理后台上传 Excel。</div>`));
  }
  for (const w of data.workbooks) {
    const n = el(`
      <div class="wb-item" data-id="${w.id}">
        <span class="wb-name">${escapeHtml(w.name)}</span>
        <span class="muted">${escapeHtml(w.sheet_name)} · ${escapeHtml(w.uploaded_at)}</span>
        <span class="spacer"></span>
        <span class="btn ghost wb-export">⬇ 导出</span>
        <span class="btn primary">打开</span>
      </div>`);
    n.addEventListener('click', () => nav(`#/wb/${w.id}`));
    // 列表页直接导出（按上传模板回填）；阻止冒泡避免触发「打开」
    n.querySelector('.wb-export').addEventListener('click', (e) => {
      e.stopPropagation();
      exportWorkbook(w.id, `${w.name.replace(/\.xls[xm]$/i, '')}_导出.xlsx`);
    });
    box.appendChild(n);
  }
}

/* ── 参数取值：分号拆列表 ── */
function cfgList(key) {
  return (cur.config[key] || '').split(';').map((s) => s.trim()).filter(Boolean);
}

/* ── ② 单页编辑：顶部搜索栏（参数「搜索选项」驱动：字段|select 下拉 / 字段|search 文本唯一选中）──
   选中即在本页下方直接切换编辑表单，无列表跳转页。 */
let selState = {};        // 搜索栏当前值 {字段名: value}（search 字段存的是已选中的值）
let selCfg = [];          // 解析后的控件配置 [{field, type}]

function parseSelCfg() {
  selCfg = cfgList('搜索选项').map((s) => {
    const p = s.split('|');
    return { field: (p[0] || '').trim(), type: (p[1] || 'search').trim() };
  }).filter((c) => c.field && cur.headers.includes(c.field));
  if (!selCfg.length) {   // 兜底：未配置时用「小班号」搜索，再退到第一列
    const f = cur.headers.includes(keyCol()) ? keyCol() : cur.headers[0];
    selCfg = [{ field: f, type: 'search' }];
  }
}

function uniqueVals(field) {
  const ci = cur.headers.indexOf(field);
  const set = new Set();
  for (const r of allRows) { const v = String(r[ci] ?? '').trim(); if (v) set.add(v); }
  return [...set].sort((a, b) => a.localeCompare(b, 'zh'));
}

/* 按搜索栏（select 字段）过滤候选行索引 */
function candidateIdxs() {
  let idxs = allRows.map((_, i) => i);
  for (const c of selCfg) {
    if (c.type !== 'select') continue;
    const v = selState[c.field];
    if (!v) continue;
    const ci = cur.headers.indexOf(c.field);
    idxs = idxs.filter((i) => String(allRows[i][ci] ?? '').trim() === v);
  }
  return idxs;
}

function syncSelectorToRow(idx) {
  for (const c of selCfg) {
    const ci = cur.headers.indexOf(c.field);
    selState[c.field] = String(allRows[idx][ci] ?? '').trim();
  }
  renderSelectorBar();
}

function renderSelectorBar() {
  const bar = $('#selectorBar');
  bar.innerHTML = '';
  for (const c of selCfg) {
    const wrap = el(`<div class="sel-field"><label>${escapeHtml(c.field)}${c.type === 'search' ? '（输入筛选，点击选中）' : ''}</label></div>`);
    if (c.type === 'select') {
      const sel = document.createElement('select');
      sel.add(new Option('全部', ''));
      uniqueVals(c.field).forEach((v) => sel.add(new Option(v, v, false, selState[c.field] === v)));
      sel.addEventListener('change', () => {
        selState[c.field] = sel.value;
        const cands = candidateIdxs();
        if (cands.length === 1) jumpRow(cands[0]);
        else if (cands.length && !cands.includes(curIdx)) jumpRow(cands[0]);
        else updateSelMeta();
      });
      wrap.appendChild(sel);
    } else {
      const inp = document.createElement('input');
      inp.placeholder = `搜索${c.field}…`;
      inp.autocomplete = 'off';
      inp.value = selState[c.field] || '';
      const sug = el(`<div class="sel-suggest hidden"></div>`);
      wrap.appendChild(inp); wrap.appendChild(sug);

      const showSug = () => {
        const q = inp.value.trim().toLowerCase();
        // 候选 = search 字段本身模糊匹配 + 其它 select 字段已选条件
        const pre = candidateIdxs();
        const ci = cur.headers.indexOf(c.field);
        const hits = [];
        for (const i of pre) {
          const v = String(allRows[i][ci] ?? '').trim();
          if (v && (!q || v.toLowerCase().includes(q)) && !hits.some((h) => h.v === v)) hits.push({ v, i });
          if (hits.length >= 50) break;
        }
        sug.innerHTML = '';
        if (!hits.length) {
          sug.appendChild(el(`<div class="sg-empty">无匹配${escapeHtml(c.field)}</div>`));
        } else {
          for (const h of hits) {
            const item = el(`<div class="sg-item">${escapeHtml(h.v)}</div>`);
            item.addEventListener('mousedown', (e) => {   // mousedown 先于 blur
              e.preventDefault();
              jumpRow(h.i);
            });
            sug.appendChild(item);
          }
        }
        sug.classList.remove('hidden');
      };
      inp.addEventListener('input', showSug);
      inp.addEventListener('focus', showSug);
      inp.addEventListener('blur', () => setTimeout(() => sug.classList.add('hidden'), 120));
      inp.addEventListener('keydown', (e) => {        // 回车选中唯一匹配
        if (e.key !== 'Enter') return;
        const q = inp.value.trim().toLowerCase();
        const ci = cur.headers.indexOf(c.field);
        const exact = candidateIdxs().filter((i) => String(allRows[i][ci] ?? '').trim().toLowerCase() === q);
        if (exact.length === 1) { jumpRow(exact[0]); } else showSug();
      });
    }
    bar.appendChild(wrap);
  }
  updateSelMeta();
}

function updateSelMeta() {
  const cands = candidateIdxs();
  const xh = keyVal();
  $('#selMeta').innerHTML = allRows.length
    ? `匹配 ${cands.length} / ${allRows.length} 个小班 · 当前：<span class="cur-xh">${escapeHtml(xh || '(未编号)')}</span>`
    : '该工作簿没有数据行';
}

function jumpRow(idx) {
  if (!allRows[idx]) return;
  if (trackWatch !== null) autoStopTrackIfRecording('切换小班');   // 互斥（F2）
  curIdx = idx;
  syncSelectorToRow(idx);
  renderForm();
  renderShotList();
  rememberLast();
  $('#pageName').textContent = `${keyVal() || '详情'} · ${cur.name}`;
}

function openDetail(idx) {
  if (!allRows[idx]) { nav('#/'); return; }
  curIdx = idx;
  show('detail');
  if (!selCfg.length) parseSelCfg();
  syncSelectorToRow(idx);
  renderForm();
  renderShotList();
  rememberLast();
  const feats = cfgList('功能');
  $('#btnPhoto').classList.toggle('hidden', !feats.includes('拍照'));
  $('#btnAlbum').classList.toggle('hidden', !(feats.includes('拍照') || feats.includes('视频')));
  $('#btnVideo').classList.toggle('hidden', !feats.includes('视频'));
  $('#btnTrack').classList.toggle('hidden', !feats.includes('轨迹'));
  $('#btnTrack').classList.toggle('recording', trackWatch !== null);
}

function setSaveStatus(text, cls) {
  const s = $('#saveStatus');
  s.textContent = text;
  s.className = 'save-status ' + cls;
}

/* ── 保存可靠性（v0.28 重做）────────────────────────────────────────
   用户实测中招两次：①填了备注点返回 ②填了备注切到别的 App 再切回来 —— 备注都没了。
   v0.25 只修了一半，因为**把编辑器的行为想当然了**：
     · 编辑器（jspreadsheet）把值放在它自己的 <input> 里，只在 blur / Enter 触发它
       自己的 onchange 时才把值交出来；
     · **不能假定它会派发能被外部监听的 input 事件**（混淆源码里无法确认，实测在无头
       环境也复现不到）。只要它不派发，任何"逐键捕获"都是死代码。
   所以 v0.28 改成三条不依赖编辑器实现的路径：
     ① 任何离场/切后台时刻，**直接从 DOM 把值读回内存**（syncGridIntoAllRows）；
     ② 文档级捕获监听 input / keyup / focusout，命中值格就同步（捕获阶段，谁也拦不住）；
     ③ 每次改动同步写一份**本地草稿**（localStorage 是同步写入，进程被杀也不丢），
        启动 / 切回前台时若有草稿就自动补交。
   ─────────────────────────────────────────────────────────────────── */

const DRAFT_KEY = 'hqz_sup_draft';
const DRAFT_MAX_AGE = 12 * 3600 * 1000;   // 超 12 小时的草稿不再自动补交（怕覆盖别人后来的修改）

function saveDraft(job) {
  const j = job || pendingSave;
  if (!j) return;
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(Object.assign({ ts: Date.now() }, j)));
  } catch (e) { /* 隐私模式/配额满：忽略，不影响主流程 */ }
}
function clearDraft() { try { localStorage.removeItem(DRAFT_KEY); } catch (e) {} }
function readDraft() {
  try {
    const d = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
    return (d && d.wid && Array.isArray(d.values)) ? d : null;
  } catch (e) { return null; }
}

/* 把「值格」里当前显示/编辑中的内容读回 allRows —— 不依赖编辑器是否提交。

   ⚠️ v0.28.2 修正（v0.28 在这里写坏过线上数据，务必看清）：
   jspreadsheet 渲染的每行是 **3 个格子 = [行号, 字段名, 值]**（不是 2 个！），
   v0.28 我按"2 列"取了 `tr.children[1]`，取到的是**字段名**，于是把字段名当值写库 ——
   用户看到的就是"值变成了 label"。
   现在三重保险：
     ① 值来源优先用官方 API `formGrid.getData()[y][1]`（不猜 DOM 位置）；
     ② 编辑中未提交的值取**最后一个格子**里的 <input>（值列永远在最后），
        不写死下标，首列有没有行号列都不影响；
     ③ **安全闸**：读到的值若等于该行自己的字段名，判定为读错，直接丢弃并告警 ——
        真值恰好等于字段名的可能性可忽略，而这是本次事故的唯一特征。 */
function syncGridIntoAllRows() {
  if (!formGrid || !cur || curIdx < 0 || !allRows[curIdx]) return false;
  let data;
  try { data = formGrid.getData(); } catch (e) { return false; }
  if (!Array.isArray(data)) return false;
  let editable;
  try { editable = new Set(cfgList('可编辑列')); } catch (e) { return false; }
  const trs = document.querySelectorAll('#formEl tbody tr');
  let changed = false;
  cur.headers.forEach((h, y) => {
    if (!editable.has(h) || y >= allRows[curIdx].length) return;
    let v = (data[y] && data[y].length > 1) ? data[y][1] : '';
    const tr = trs[y];
    if (tr) {                                    // 编辑中的值优先
      const cells = tr.children;
      const vcell = cells[cells.length - 1];     // 值列永远在最后一格
      const ed = vcell && vcell.querySelector('input, textarea, [contenteditable="true"]');
      if (ed) v = (ed.value !== undefined && ed.value !== null) ? ed.value : ed.textContent;
    }
    const nv = v == null ? '' : String(v);
    if (nv === h) {                              // 安全闸：字段名绝不会是值
      console.warn('[syncGrid] 读到字段名而非值，已忽略：', h);
      return;
    }
    if (nv !== String(allRows[curIdx][y] == null ? '' : allRows[curIdx][y])) {
      allRows[curIdx][y] = nv;
      changed = true;
    }
  });
  return changed;
}

/* 该行与"服务器已保存的版本"是否不同 —— onchange 压根没触发时靠它决定要不要存 */
function rowDiffersFromSaved(ridx, values) {
  const base = (cur && cur.rows && cur.rows[ridx]) || [];
  for (let i = 0; i < values.length; i++) {
    if (String(values[i] == null ? '' : values[i]) !== String(base[i] == null ? '' : base[i])) return true;
  }
  return false;
}

/* 组装"待落库任务"：优先用已排队的，其次看 DOM 里是否与已存版本有差异 */
function buildSaveJob() {
  if (pendingSave) return pendingSave;
  if (cur && curIdx >= 0 && allRows[curIdx] && rowDiffersFromSaved(curIdx, allRows[curIdx])) {
    return { wid: cur.id, ridx: curIdx, values: allRows[curIdx].slice() };
  }
  return null;
}

let saveTimer = null, saveSeq = 0, pendingSave = null;

/* 记下"待落库的行"，并**快照 wid/ridx/values**。
   必须快照：节流窗口内用户可能已切到别的工作簿或别的行，若延后到那时才按
   cur/curIdx 取数据，会把 A 小班的内容写进 B（数据错位，比丢数据更糟）。 */
function scheduleRowSave() {
  if (!cur || curIdx < 0 || !allRows[curIdx]) return;
  setSaveStatus('⏳ 保存中…', 'saving');
  pendingSave = { wid: cur.id, ridx: curIdx, values: allRows[curIdx].slice() };
  saveDraft();                                 // 同步写本地草稿（防进程被杀）
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushRowSave, 800);   // 输入停顿 0.8s 自动落库
}

/* 立即把待保存的行落库（可 await）。**任何离场前都必须调用**：点返回、路由切换、
   切后台、关页 —— 否则节流窗口（800ms）内的编辑会丢。 */
async function flushRowSave() {
  clearTimeout(saveTimer); saveTimer = null;
  syncGridIntoAllRows();                        // ← 先把 DOM/编辑器里的最新值收进内存
  const job = buildSaveJob();
  pendingSave = null;
  if (!job) return;
  const seq = ++saveSeq;
  try {
    await api(`/api/workbooks/${job.wid}/rows/${job.ridx}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: job.values }),
    });
    if (cur && cur.rows && cur.rows[job.ridx]) cur.rows[job.ridx] = job.values.slice();
    clearDraft();
    if (seq === saveSeq) setSaveStatus('✓ 已保存', 'ok');
  } catch (e) {
    if (seq === saveSeq) setSaveStatus('✗ 保存失败（改动仍在页面，重新编辑即重试）', 'err');
  }
}

/* 页面可能被卸载时的最后一搏：keepalive 请求在页面销毁后仍会发出。
   不用 sendBeacon —— 它无法携带 X-Sup-Token 头，只能把令牌塞进 URL（会进网关访问日志）。
   同时**先把草稿写盘**：万一这个请求也没发出去，下次启动/回到前台还能补交。 */
function flushRowSaveUnloading() {
  clearTimeout(saveTimer); saveTimer = null;
  syncGridIntoAllRows();
  const job = buildSaveJob();
  pendingSave = null;
  if (!job) return;
  saveDraft(job);                               // 先落草稿，再尝试发送
  const tok = getToken();
  const headers = { 'Content-Type': 'application/json' };
  if (tok) headers['X-Sup-Token'] = tok;
  try {
    fetch((window.SUP_BASE || '') + `/api/workbooks/${job.wid}/rows/${job.ridx}`, {
      method: 'POST', headers, keepalive: true,
      body: JSON.stringify({ values: job.values }),
    }).then((r) => { if (r && r.ok) clearDraft(); }).catch(() => {});
  } catch (e) {}
}

/* 启动 / 切回前台时补交上次没送出去的草稿 */
let draftSubmitting = false;
async function submitDraftIfAny() {
  if (draftSubmitting) return;
  const d = readDraft();
  if (!d) return;
  if (Date.now() - (d.ts || 0) > DRAFT_MAX_AGE) { clearDraft(); return; }   // 太旧，丢弃
  draftSubmitting = true;
  try {
    await api(`/api/workbooks/${d.wid}/rows/${d.ridx}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: d.values }),
    });
    clearDraft();
    toast('已补交上次未保存的修改');
  } catch (e) {
    /* 网络/鉴权问题：草稿留着，下次启动或回到前台再试 */
  } finally {
    draftSubmitting = false;
  }
}

document.addEventListener('pagehide', flushRowSaveUnloading);
window.addEventListener('beforeunload', flushRowSaveUnloading);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    syncGridIntoAllRows();        // ★ 顺序很重要：切后台时编辑器往往还没提交，先把值收回来
    flushRowSaveUnloading();
  } else {
    submitDraftIfAny();           // 回到前台：补交可能没发出去的草稿
  }
});

/* 文档级捕获（v0.28）：不再假设编辑器会派发 input 事件 —— 用**捕获阶段**监听
   input / keyup / focusout，只要命中值格就先把值同步进 allRows。
   捕获阶段意味着不管编辑器自己怎么处理事件，我们都能先拿到。
   ⚠️ v0.28.2：值格是**最后一个**格子（首列是 jspreadsheet 的行号列），
   原来写死 `x !== 1` 判成了字段格，导致真实输入根本收不到（也顺带踩过数据写坏的坑）。 */
function captureCellEvent(e) {
  if (!cur || curIdx < 0 || !allRows[curIdx]) return;
  const el = e.target;
  if (!el || typeof el.closest !== 'function' || !el.closest('#formEl')) return;
  const td = el.closest('td'), tr = td && td.closest('tr');
  if (!td || !tr || !tr.parentNode) return;
  const y = [...tr.parentNode.children].indexOf(tr);   // 行号 = 字段在表头中的下标
  const x = [...tr.children].indexOf(td);
  if (x !== tr.children.length - 1 || y < 0 || y >= allRows[curIdx].length) return;  // 只认值格
  const h = cur.headers[y];
  if (!h || !cfgList('可编辑列').includes(h)) return;             // 只收「可编辑列」
  const v = (el.value !== undefined && el.value !== null) ? el.value : el.textContent;
  const nv = v == null ? '' : String(v);
  if (nv === h) return;                                           // 安全闸：字段名绝不是值
  if (nv === String(allRows[curIdx][y] == null ? '' : allRows[curIdx][y])) return;
  allRows[curIdx][y] = nv;
  scheduleRowSave();
}
document.addEventListener('input', captureCellEvent, true);
document.addEventListener('keyup', captureCellEvent, true);
document.addEventListener('focusout', captureCellEvent, true);

function renderForm() {
  const editable = new Set(cfgList('可编辑列'));

  const data = cur.headers.map((h, i) => [h, allRows[curIdx][i] || '']);
  const el0 = $('#formEl');
  el0.innerHTML = '';
  if (formGrid) { try { jspreadsheet.destroy(el0); } catch (e) {} formGrid = null; }

  formGrid = jspreadsheet(el0, {
    data,
    columns: [
      { title: '字段', width: 150, readOnly: true },
      { title: '值', width: Math.max(200, Math.min(420, window.innerWidth - 220)), type: 'text' },
    ],
    contextMenu: false, allowInsertRow: false, allowManualInsertRow: false, allowDeleteRow: false,
    allowInsertColumn: false, allowDeleteColumn: false, allowRenameColumn: false,
    columnDrag: false, columnSorting: false, search: false, toolbar: false,
    tableOverflow: false, tableWidth: '100%',
    onbeforechange: (inst, cell, x, y, value) => {
      // 非可编辑字段：直接阻止编辑（4.15 的 setReadOnly('B{n}') 字符串签名会误锁整列，弃用）
      if (x === 1 && !editable.has(cur.headers[y])) return false;
      return value;
    },
    onchange: (inst, cell, x, y, value) => {
      if (x !== 1) return;
      const h = cur.headers[y];
      if (!editable.has(h)) return;                    // 双保险
      allRows[curIdx][y] = value == null ? '' : String(value);
      scheduleRowSave();
    },
    onload: () => {
      // 非可编辑行值格：灰底视觉标识（拦截逻辑在 onbeforechange）
      const resultName = resultOptKey() ? resultOptKey().slice(0, -2) : '';
      el0.querySelectorAll('tbody tr').forEach((tr, y) => {
        if (!editable.has(cur.headers[y])) {
          const td = tr.querySelectorAll('td')[1];
          if (td) td.classList.add('locked');
        }
        // 验收结果行 label 突出显示（★ + 绿底加粗）；注意 td[0] 是 jss 行号列，字段名格是 data-x=0
        if (cur.headers[y] === resultName) {
          const td0 = tr.querySelector('td[data-x="0"]');
          if (td0) td0.classList.add('result-label');
        }
      });
    },
  });
  // 让容器自然撑开、外层滚动（同 hqz-survey）
  el0.style.overflow = 'visible';
  const jss = el0.querySelector('.jss');
  if (jss) { jss.style.overflow = 'visible'; jss.style.maxHeight = 'none'; jss.style.height = 'auto'; }
  const tbl = el0.querySelector('table');
  if (tbl) tbl.style.height = 'auto';
  const hasVideo = cfgList('功能').includes('视频');
  const cp = compressParams && compressParams.photo ? compressParams.photo : null;
  const effSide = parseInt(cur.config['压缩最长边'], 10) || (cp && cp.max_side) || PHOTO_MAX_SIDE;
  const effQ = parseFloat(cur.config['压缩质量']) || (cp && cp.quality) || PHOTO_QUALITY;
  $('#photoHint').textContent = (isNativeApp()
    ? '📁 拍照仅存本机系统相册 Pictures/' + (rowSubdir() || '(参数未配置目录)')
    : '📁 拍照仅下载到本机（服务器不留存）')
    + (hasVideo ? '；🎬 视频本地录制（不上传）' : '')
    + `；压缩 ${effSide}px/${effQ}`;
}

/* ── 导出 Excel（按上传模板回填） ── */
/* ── 导出 Excel（v0.22）：**不能用 window.open** ──
   App 壳里强制"所有导航留在 WebView 内"（MainActivity 覆写 shouldOverrideUrlLoading），
   而 WebView 没有下载能力 → 点导出静默无反应（2026-09-14 实机反馈）。
   改为：fetch 取文件流 → 原生 saveFile 落到「下载/验收导出」（mime xlsx 正确）→
   浏览器环境回退 <a download>。 */
async function exportWorkbook(id, fallbackName) {
  if (!id) { toast('请先选择工作簿', true); return; }
  toast('正在生成 Excel…');
  try {
    const url = withToken((window.SUP_BASE || '') + `/api/workbooks/${id}/export`);
    const tok = getToken();
    const resp = await fetch(url, tok ? { headers: { 'X-Sup-Token': tok } } : {});
    if (!resp.ok) throw new Error('服务端返回 ' + resp.status);
    const ctype = resp.headers.get('Content-Type') || '';
    // 网关/登录页拦截时会返回 HTML（线上首次或会话失效），必须识别出来，别存成假的 xlsx
    if (!/spreadsheet|octet-stream/i.test(ctype)) {
      throw new Error(ctype.includes('html') ? '登录状态失效，请重新登录后再导出' : ('返回类型异常：' + ctype));
    }
    const blob = await resp.blob();
    if (!blob.size) throw new Error('文件为空');
    // 文件名优先取服务端 Content-Disposition（含中文、带"导出"后缀）
    let fname = fallbackName || '导出.xlsx';
    const cd = resp.headers.get('Content-Disposition') || '';
    const m = /filename\*=UTF-8''([^;]+)/i.exec(cd) || /filename="?([^";]+)"?/i.exec(cd);
    if (m) {
      try { fname = decodeURIComponent(m[1].trim()); } catch (e) { fname = m[1].trim(); }
    }
    if (!/\.xls[xm]$/i.test(fname)) fname += '.xlsx';
    const plugin = nativePlugin();
    if (plugin && typeof plugin.saveFile === 'function') {
      const b64 = await blobToBase64(blob);
      const r = await plugin.saveFile({ base64: b64, name: fname });
      toast(`已保存：${(r && r.path) || ('下载/验收导出/' + fname)}`);
      return;
    }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = fname;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    toast('已下载：' + fname);
  } catch (err) {
    toast('导出失败：' + err.message, true);
  }
}

$('#btnExport').addEventListener('click', () => {
  if (!cur) return;
  exportWorkbook(cur.id, `${cur.name.replace(/\.xls[xm]$/i, '')}_导出.xlsx`);
});

/* 解析下拉选项键：形如「XX选项」且 XX 是真实表头列（排除「搜索选项」控件映射键）。
   坑：简单 endsWith('选项') 会先命中「搜索选项」（v0.7 遗留 bug，下拉从未弹出） */
function resultOptKey() {
  return Object.keys(cur.config).find((k) =>
    k.endsWith('选项') && k !== '搜索选项' && cur.headers.includes(k.slice(0, -2)));
}

/* 下拉字段（验收结果选项 → 验收结果）：点击值格弹原生 select，change 即触发自动保存链 */
document.addEventListener('click', (e) => {
  if (!formGrid || !cur || curIdx < 0) return;
  const optKey = resultOptKey();
  if (!optKey) return;
  const oy = cur.headers.indexOf(optKey.slice(0, -2));
  if (oy < 0) return;
  const cell = formGrid.getCell(`B${oy + 1}`);
  if (!cell || !cell.contains(e.target) || cell.querySelector('select')) return;
  const options = cfgList(optKey);
  const old = allRows[curIdx][oy];
  const sel = document.createElement('select');
  sel.style.width = '100%';
  sel.add(new Option('', ''));
  options.forEach((o) => sel.add(new Option(o, o, false, o === old)));
  cell.textContent = '';
  cell.appendChild(sel);
  sel.focus();
  sel.addEventListener('change', () => {
    const v = sel.value;
    cell.textContent = v;
    allRows[curIdx][oy] = v;
    try { formGrid.setValueFromCoords(1, oy, v); } catch (e) {}   // 同步内部数据（否则网格数据与 allRows 脱节）
    syncAcceptCols(oy, v);            // 联动规则：选结果→自动填「人」和「日期」；选空→一并清空
    scheduleRowSave();
  });
  sel.addEventListener('blur', () => { cell.textContent = allRows[curIdx][oy] || ''; });
});

/* 审计/联动字段（v0.23，C11）：由后端按参数「日志字段」算好后随工作簿下发
   （/api/workbooks/<id> 的 log_fields），前端不再写死「验收人/验收日期/验收时间/验收备注」。
   老工作簿（后端未下发）时用同一组默认列做兜底。 */
const LINK_FALLBACK = ['验收人', '验收日期', '验收时间', '验收结果', '验收备注'];

function linkFields() {
  if (!cur) return [];
  const f = cur.log_fields || [];
  return (f.length ? f : LINK_FALLBACK).filter((h) => cur.headers.includes(h));
}

/* 联动规则（通用，非本模板硬编码）：
   「*选项」下拉列选中非空值 → 「人」列（列名含 人/员）=当前登录用户、「日期」列（含 日期，无则含 时间）=今天
   选中空（视为未处理）     → 除该下拉列外的联动字段全部清空 */
function syncAcceptCols(resultColIdx, v) {
  const user = ($('#whoami').dataset.user || '').trim();
  const today = new Date().toLocaleDateString('sv-SE');
  const resultCol = cur.headers[resultColIdx];
  const fields = linkFields().filter((h) => h !== resultCol);
  const userCol = fields.find((h) => /[人员]/.test(h)) || null;
  const dateCol = fields.find((h) => /日期/.test(h)) || fields.find((h) => /时间/.test(h)) || null;

  const setCol = (colName, val) => {
    if (!colName) return false;
    const ci = cur.headers.indexOf(colName);
    if (ci < 0 || ci === resultColIdx) return false;
    if (allRows[curIdx][ci] === val) return false;
    allRows[curIdx][ci] = val;
    try { formGrid.setValueFromCoords(1, ci, val); } catch (e) {}
    // 双保险：setValueFromCoords 后强制刷新单元格 DOM（历史问题：程序赋值后界面未更新）
    try {
      const cellEl = formGrid.getCell('B' + (ci + 1));
      if (cellEl && cellEl.textContent !== String(val)) cellEl.textContent = String(val);
    } catch (e) {}
    return true;
  };

  let changed = false, changedNames = [];
  if (v) {
    if (user && setCol(userCol, user)) changedNames.push(userCol);
    if (setCol(dateCol, today)) changedNames.push(dateCol);
    changed = changedNames.length > 0;
    if (changed) toast('已自动填入：' + changedNames.join('、'));
  } else {
    for (const h of fields) if (setCol(h, '')) changed = true;
    if (changed) toast(`${resultCol || '结果'}已清空：${fields.join('/')} 一并清空`);
  }
}

/* 该小班的相片子目录（参数「目录」模板渲染 + 与后端 _safe_segments 同规则清洗） */
function sanitizeSeg(s) {
  return s.replace(/[\\/:*?"<>|]+/g, '_').replace(/^[. ]+|[. ]+$/g, '');
}

function rowSubdir() {
  const row = allRows[curIdx];
  if (!row) return '';
  return (cur.config['目录'] || '').split('/')
    .map((s) => sanitizeSeg(renderTpl(s, row, ''))).filter(Boolean).join('/');
}

/* 模板渲染：{{列名}}→行值；{{sheet名称}}→当前sheet；{{时间}}→YYYYMMDD_HHMMSS；
   {{拍照人}}→当前登录用户（C02 保留字） */
/* 模板渲染：{{列名}}→行值；{{sheet名称}}→当前sheet；{{时间}}→YYYYMMDD_HHMMSS；
   {{拍照人}}→当前登录用户；{{工作簿id}}/{{工作簿名}}/{{工作簿文件名}}→工作簿级（v0.29.3，C02 保留字） */
function renderTpl(tpl, row, now) {
  return (tpl || '').replace(/\{\{(.+?)\}\}/g, (_, key) => {
    key = key.trim();
    if (key === 'sheet名称') return cur.sheet_name;
    if (key === '时间') return now;
    if (key === '拍照人') return ($('#whoami').dataset.user || '').trim();
    if (key === '工作簿id') return cur ? String(cur.id) : '';
    if (key === '工作簿名') return cur ? String(cur.name || '').replace(/\.[^.]+$/, '') : '';  // 不含扩展名
    if (key === '工作簿文件名') return cur ? String(cur.name || '') : '';                      // 含扩展名
    const i = cur.headers.indexOf(key);
    return i >= 0 ? String(row[i] == null ? '' : row[i]).trim() : '';
  });
}

/* ── 拍摄记录（C07：相片不落服务器，仅本地记录完整文件 path 提示，无预览） ──
   localStorage 按 workbook 记录 {文件名, 小班号, 时间}，页面只提示「已拍过什么」，不提供预览。 */
function getShots() {
  try { return JSON.parse(localStorage.getItem('hqz_sup_shots_' + cur.id) || '[]'); }
  catch (e) { return []; }
}

function recordShot(name, xh, kind) {
  const list = getShots();
  list.unshift({ n: name, xh: xh || '', k: kind || 'photo',
                 t: new Date().toLocaleString('sv-SE') });
  try { localStorage.setItem('hqz_sup_shots_' + cur.id, JSON.stringify(list.slice(0, 500))); } catch (e) {}
}

/* 拍摄记录条目：照片与视频样式区分（图标 + 类型标签 + 配色） */
function shotItemHtml(s) {
  const isVideo = s.k === 'video';
  const name = escapeHtml(s.n);
  return isVideo
    ? `<div class="shot-item video"><span>🎬 ${name}</span><span class="spacer"></span>`
      + `<span class="k-tag">视频</span><span class="t">${escapeHtml(s.t)}</span></div>`
    : `<div class="shot-item"><span>📷 ${name}</span><span class="spacer"></span>`
      + `<span class="k-tag photo">照片</span><span class="t">${escapeHtml(s.t)}</span></div>`;
}

/* 当前小班的拍摄提示列表（按小班号过滤，无小班号列时显示全部） */
function renderShotList() {
  const g = $('#rowPhotos');
  const xh = keyVal();
  const shots = getShots().filter((s) => (s.xh || '') === (xh || ''));
  if (!shots.length) {
    g.innerHTML = '<span class="muted">该小班还没有拍摄记录，点「📷 拍照」开始。</span>';
    return;
  }
  g.innerHTML = '<div class="shot-list">' + shots.map(shotItemHtml).join('') + '</div>';
}

/* ── 拍摄记录弹层（本工作簿全部记录，按小班分组展示） ── */
$('#btnAlbum').addEventListener('click', openShots);
$('#albumClose').addEventListener('click', () => $('#albumMask').classList.add('hidden'));
$('#albumMask').addEventListener('click', (e) => { if (e.target === $('#albumMask')) $('#albumMask').classList.add('hidden'); });

function openShots() {
  const mask = $('#albumMask');
  mask.classList.remove('hidden');
  const shots = getShots();
  const nV = shots.filter((x) => x.k === 'video').length;
  $('#albumMeta').textContent = `${shots.length} 条拍摄记录（照片 ${shots.length - nV} · 视频 ${nV}，仅本机）`;
  const g = $('#shotAll');
  g.innerHTML = '';
  if (!shots.length) { g.innerHTML = '<span class="muted">本机还没有拍摄记录。</span>'; return; }
  const groups = new Map();
  for (const s of shots) {
    const k = s.xh || '(未编号)';
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(s);
  }
  for (const [xh, items] of groups) {
    g.appendChild(el(`<div class="shot-group"><b>小班 ${escapeHtml(xh)}</b> <span class="muted">${items.length} 张</span></div>`));
    const box = el(`<div class="shot-list"></div>`);
    for (const s of items) {
      box.appendChild(el(shotItemHtml(s)));
    }
    g.appendChild(box);
  }
}

/* ── 拍照（B2）：参数化模板 + 固定日期水印 + 上传到参数目录（照片保留本地） ── */
function getCoords() {
  return new Promise((res) => {
    if (!navigator.geolocation) return res(null);
    const t = setTimeout(() => res(null), 2500);
    navigator.geolocation.getCurrentPosition(
      (p) => { clearTimeout(t); res(`${p.coords.latitude.toFixed(5)},${p.coords.longitude.toFixed(5)}`); },
      () => { clearTimeout(t); res(null); },
      { enableHighAccuracy: true, timeout: 2500 });
  });
}

// 水印（半透明白底块 + 实心黑字，左下角）；压缩默认 1440px / 0.80，可由参数 sheet「压缩最长边/压缩质量」下发（doc/007 §6）
// 日期行固定输出（C06：水印日期不参数化）
const PHOTO_MAX_SIDE = 1440;
const PHOTO_QUALITY = 0.80;

async function drawWatermark(file, remark, coords, opts) {
  const maxSide = (opts && opts.maxSide) || PHOTO_MAX_SIDE;
  const quality = (opts && opts.quality) || PHOTO_QUALITY;
  let bmp;
  try { bmp = await createImageBitmap(file, { imageOrientation: 'from-image' }); }
  catch (e) { bmp = await createImageBitmap(file); }
  const canvas = document.createElement('canvas');
  const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
  canvas.width = Math.max(1, Math.round(bmp.width * scale));
  canvas.height = Math.max(1, Math.round(bmp.height * scale));
  const ctx = canvas.getContext('2d');
  ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
  bmp.close && bmp.close();

  // 首行：完整时间戳 yyyy-MM-dd HH:mm:ss（本地时区；C06：水印时间不参数化）
  const now = new Date();
  const p2 = (n) => String(n).padStart(2, '0');
  const stamp = `${now.getFullYear()}-${p2(now.getMonth() + 1)}-${p2(now.getDate())}`
    + ` ${p2(now.getHours())}:${p2(now.getMinutes())}:${p2(now.getSeconds())}`;
  const lines = [`时间：${stamp}`];
  if (coords) lines.push(`坐标：${coords}`);
  for (const [i, seg] of (remark.match(/[\s\S]{1,26}/g) || []).entries()) {
    lines.push((i === 0 ? '备注：' : '') + seg);
  }

  // 水印版式（省比特版式）：半透明白底块 + 实心黑字。
  // 旧版「白描边黑字」的描边是高频边缘，JPEG 要为它花大量比特；改成底块后
  // 同等质量下体积更小、文字更清晰（无彩边/马赛克）。
  // 字号与留白：底块内边距/行距给足呼吸空间；若行数过多导致底块过高
  //（>画面 55%），整体按比例缩字号（下限 14px），保证文字完整不被裁切
  let fs = Math.max(18, Math.round(canvas.width / 34));
  let lh = Math.round(fs * 1.45);
  let padX = Math.round(fs * 0.7);
  let padY = Math.round(fs * 0.5);
  let margin = Math.round(fs * 0.55);
  const maxH = canvas.height * 0.55;
  let boxH = lines.length * lh + padY * 2;
  if (boxH > maxH) {
    const k = maxH / boxH;
    fs = Math.max(14, Math.round(fs * k));
    lh = Math.round(fs * 1.45); padX = Math.round(fs * 0.7);
    padY = Math.round(fs * 0.5); margin = Math.round(fs * 0.55);
    boxH = lines.length * lh + padY * 2;
  }
  ctx.font = `${fs}px system-ui,'PingFang SC','Microsoft YaHei',sans-serif`;
  ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';

  // ① 底块尺寸 = 最长行宽 + 内边距；贴左下角（与旧版位置一致）
  let textW = 0;
  for (const ln of lines) textW = Math.max(textW, ctx.measureText(ln).width);
  const boxW = Math.max(Math.round(canvas.width * 0.3),
    Math.min(canvas.width - margin * 2, Math.ceil(textW) + padX * 2));
  const boxX = margin;
  const boxY = Math.max(margin, canvas.height - boxH - margin);

  // ② 半透明白底块（alpha 0.62：保证压住深色背景又不切断背景内容）
  const r = Math.round(fs * 0.35);
  ctx.fillStyle = 'rgba(255,255,255,.62)';
  ctx.beginPath();
  ctx.moveTo(boxX + r, boxY);
  ctx.lineTo(boxX + boxW - r, boxY); ctx.quadraticCurveTo(boxX + boxW, boxY, boxX + boxW, boxY + r);
  ctx.lineTo(boxX + boxW, boxY + boxH - r); ctx.quadraticCurveTo(boxX + boxW, boxY + boxH, boxX + boxW - r, boxY + boxH);
  ctx.lineTo(boxX + r, boxY + boxH); ctx.quadraticCurveTo(boxX, boxY + boxH, boxX, boxY + boxH - r);
  ctx.lineTo(boxX, boxY + r); ctx.quadraticCurveTo(boxX, boxY, boxX + r, boxY);
  ctx.closePath();
  ctx.fill();

  // ③ 实心黑字（单色 = 低熵，压缩友好）
  ctx.fillStyle = '#111';
  for (let i = lines.length - 1; i >= 0; i--) {
    const y = boxY + boxH - padY - (lines.length - 1 - i) * lh;
    ctx.fillText(lines[i], boxX + padX, y);
  }
  return new Promise((res, rej) =>
    canvas.toBlob((b) => (b ? res(b) : rej(new Error('水印编码失败'))), 'image/jpeg', quality));
}

$('#btnPhoto').addEventListener('click', () => {
  if (curIdx < 0 || !allRows[curIdx]) { toast('请先选择小班', true); return; }
  $('#photoInput').click();
});

/* App 壳判定与 blob→base64（供原生 savePhoto 写相册，同 hqz-survey） */
function isNativeApp() {
  return !!(window.Capacitor && typeof window.Capacitor.isNativePlatform === 'function'
    && window.Capacitor.isNativePlatform());
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',')[1] || '');
    r.onerror = reject;
    r.readAsDataURL(blob);
  });
}

$('#photoInput').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file || curIdx < 0) return;
  const row = allRows[curIdx];
  toast('正在处理水印…');
  try {
    const now = new Date();
    const ts = now.getFullYear() + String(now.getMonth() + 1).padStart(2, '0') +
      String(now.getDate()).padStart(2, '0') + '_' + String(now.getHours()).padStart(2, '0') +
      String(now.getMinutes()).padStart(2, '0') + String(now.getSeconds()).padStart(2, '0');
    const coords = await getCoords();
    const remark = renderTpl(cur.config['相片备注'] || '', row, ts);
    // 压缩参数优先级（v0.16）：参数 sheet「压缩最长边/压缩质量」→ 后台「图片压缩」默认 → 内置兜底
    const cp = (await loadCompressParams()).photo || {};
    const maxSide = parseInt(cur.config['压缩最长边'], 10) || cp.max_side || PHOTO_MAX_SIDE;
    const quality = Math.min(1, Math.max(0.1,
      parseFloat(cur.config['压缩质量']) || cp.quality || PHOTO_QUALITY));
    const blob = await drawWatermark(file, remark, coords, { maxSide, quality });
    const filename = sanitizeSeg(renderTpl(cur.config['相片文件名'] || '', row, ts)) || 'photo';
    const subdir = rowSubdir();
    const xh = keyVal();

    // ① App 内：原生 MediaStore 存入系统相册 Pictures/{参数「目录」}/（多级子目录，相册立即可见）
    //    Android 10+ 自有媒体免存储权限；C07：不经过服务器。失败时回退为浏览器下载，不丢图
    let savedWhere = '';
    if (isNativeApp()) {
      try {
        const plugin = window.Capacitor.Plugins.AppPermissions;
        if (plugin && plugin.savePhoto) {
          const r = await plugin.savePhoto({
            base64: await blobToBase64(blob),
            name: filename + '.jpg',
            subdir: subdir || '验收照片',
          });
          savedWhere = (r && r.path) ? `已存入相册：${r.path}` : '已存入系统相册';
        }
      } catch (err) { savedWhere = ''; }
    }
    if (!savedWhere) {
      // ② 浏览器端 / App 相册保存失败回退：水印压缩后直接下载到本机
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename + '.jpg';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      savedWhere = `已下载：${filename}.jpg（${(blob.size / 1024).toFixed(0)} KB）`;
    }

    // ③ 仅记录完整文件路径提示（本机 localStorage，不上传；C07：无预览，只看 path）
    const dispPath = isNativeApp()
      ? `Pictures/${subdir || '验收照片'}/${filename}.jpg`
      : `${filename}.jpg`;
    recordShot(dispPath, xh);

    // ④ 云同步中转（doc/007）：后台开关开启时 → POST /api/photo（七牛暂存→百度网盘）。
    //    本地保存永远是第一优先，云同步失败只提示，不丢图
    let syncMsg = '';
    try {
      const st = await api('/api/sync/enabled');
      if (st.enabled) {
        const fd = new FormData();
        fd.append('file', blob, filename + '.jpg');
        fd.append('workbook_id', cur.id);
        fd.append('filename', filename + '.jpg');
        fd.append('subdir', subdir || '');
        fd.append('xiaoban', xh || '');
        const r = await api('/api/photo', { method: 'POST', body: fd });
        syncMsg = (r.state === 'baidu_ok') ? '；已同步百度网盘'
          : (r.state === 'qiniu_ok') ? '；已暂存云端（待推送）' : '；云同步失败：' + (r.error || '稍后自动重试');
      }
    } catch (err) { syncMsg = '；云同步失败：' + err.message; }

    toast(savedWhere + syncMsg);
    renderShotList();
  } catch (err) { toast('拍照处理失败：' + err.message, true); }
});

/* ── 修改密码（v0.21）：点右上角用户名打开；仅能改自己（管理员改他人走后台） ── */
function openPwdDialog() {
  const who = $('#whoami').dataset.user || '';
  if (!who) { toast('请先登录', true); return; }
  $('#pwdWho').textContent = who;
  ['#pwdOld', '#pwdNew', '#pwdNew2'].forEach((sel) => { $(sel).value = ''; });
  $('#pwdMsg').textContent = '';
  $('#pwdMask').classList.remove('hidden');
  $('#pwdOld').focus();
}

$('#whoami').addEventListener('click', openPwdDialog);
$('#pwdCancel').addEventListener('click', () => $('#pwdMask').classList.add('hidden'));
$('#pwdMask').addEventListener('click', (e) => {
  if (e.target === $('#pwdMask')) $('#pwdMask').classList.add('hidden');
});

async function submitPwdChange() {
  const oldPwd = $('#pwdOld').value;
  const n1 = $('#pwdNew').value.trim();
  const n2 = $('#pwdNew2').value.trim();
  const msg = $('#pwdMsg');
  msg.textContent = '';
  if (!oldPwd) { msg.textContent = '请输入原密码'; return; }
  if (n1.length < 4) { msg.textContent = '新密码至少 4 位'; return; }
  if (n1 !== n2) { msg.textContent = '两次输入的新密码不一致'; return; }
  if (n1 === oldPwd) { msg.textContent = '新密码不能与原密码相同'; return; }
  try {
    const r = await api('/api/password', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_password: oldPwd, new_password: n1 }),
    });
    if (r.token) setToken(r.token);      // 本机换用新令牌：无需重新登录
    $('#pwdMask').classList.add('hidden');
    toast('密码已修改（本机保持登录；其他同事不受影响）');
  } catch (err) {
    msg.textContent = err.message;
  }
}

$('#pwdSave').addEventListener('click', submitPwdChange);
['#pwdOld', '#pwdNew', '#pwdNew2'].forEach((sel) =>
  $(sel).addEventListener('keydown', (e) => { if (e.key === 'Enter') submitPwdChange(); }));

/* ── 视频（v0.11）：手机端边录边压 + **只存手机本地（不上传、不入云）** ──
   点「🎬 视频」直接开始录制；录音权限未在 APK 声明，故无声；停止后保存到手机并记本机提示。
   保存链：原生 saveVideo（写 Movies/ 相册，需新版 APK）→ 原生 saveFile（下载目录）→ 浏览器下载。 */
let recStream = null, recRecorder = null, recChunks = [], recTimer = null;
let recStartAt = 0, recParams = null, recSaving = false;
let compressParams = null;          // /api/compress/params 缓存（图片+视频压缩参数）

function videoApiReady() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
}

/* 录制容器：优先 MP4/H.264（兼容性最好，相册与播放器都认）。
   注意「video/mp4」可能落到 HEVC/AV1，故首选显式 avc1（H.264）。 */
const MP4_MIMES = ['video/mp4;codecs=avc1.42E01E', 'video/mp4;codecs=avc1', 'video/mp4'];

function mp4RecSupported() {
  if (!window.MediaRecorder) return false;
  return MP4_MIMES.some((m) => {
    try { return MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m); } catch (e) { return false; }
  });
}

function pickRecMime() {
  for (const m of MP4_MIMES) {
    try { if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) return m; } catch (e) {}
  }
  return '';   // 不支持 MP4 → 交给 startRecording 决策（换系统相机）
}

const mimeExt = (mime) => (/mp4/.test(mime) ? 'mp4' : 'webm');

function fmtSec(n) {
  return String(Math.floor(n / 60)).padStart(2, '0') + ':' + String(n % 60).padStart(2, '0');
}

/* 压缩参数（v0.16）：与「同步」解耦——photo=图片压缩默认、video=视频录制/转码参数 */
async function loadCompressParams(force) {
  if (compressParams && !force) return compressParams;
  try { compressParams = await api('/api/compress/params'); } catch (e) { compressParams = compressParams || {}; }
  return compressParams;
}

async function loadVideoParams(force) {
  // 每次点击都实时拉取（几百毫秒，用户无感）：后台改了「录制方式」立即生效，避免缓存误导
  if (recParams && !force) return recParams;
  const all = await loadCompressParams(force);
  recParams = all.video || recParams || {};
  return recParams;
}

/* 原生录像阶段提示：转码中 / 转码失败回退原片（插件 notifyListeners 推送） */
(function bindVideoStage() {
  const tryBind = () => {
    const plugin = nativePlugin();
    if (!plugin || typeof plugin.addListener !== 'function') return false;
    plugin.addListener('videoStage', (d) => {
      if (!d || !d.stage) return;
      if (d.stage === 'transcoding') toast('正在压缩视频（保留声音，稍候）…');
      if (d.stage === 'transcode_failed') toast('本机压缩失败，已保存原始视频（体积较大）', true);
    });
    return true;
  };
  if (!tryBind()) setTimeout(tryBind, 1500);   // 原生桥可能就绪较晚
})();

$('#btnVideo').addEventListener('click', async () => {
  if (curIdx < 0 || !allRows[curIdx]) { toast('请先选择小班', true); return; }
  const p = await loadVideoParams(true);
  const mode = p.rec_mode || 'system';
  if (mode === 'inapp') {
    if (!videoApiReady()) { useSystemCamera('当前环境不支持页面录制'); return; }
    if (!mp4RecSupported()) { useSystemCamera('本机不支持录制 MP4'); return; }
    startRecording().catch((e) => useSystemCamera('页面录制启动失败：' + e.message));
    return;
  }
  // 默认：系统相机录制（硬件 H.264 → MP4，任何相册/播放器都能播）
  useSystemCamera('');
});

/* 系统相机录制（保证 MP4/H.264）：录完按同一命名规则另存到手机 */
async function useSystemCamera(why) {
  if (trackWatch !== null) await autoStopTrackIfRecording('开始录像');   // 互斥：先停轨迹并保存
  toast(why ? `${why} → 用系统相机录制（MP4，保证可播放）` : '用系统相机录制（MP4，保证可播放）');
  markReturnContext();                 // 记下当前工作簿/行：相机返回即使页面重载也能回到详情页
  const plugin = nativePlugin();
  // 新版 APK：原生录像（startActivityForResult → 复制到 Pictures/{目录}/），不经过 WebView 文件回传，
  // 避免"相机顶掉 WebView → 页面重载 → 文件结果丢失"
  if (plugin && typeof plugin.recordVideo === 'function') {
    try {
      const row = allRows[curIdx];
      const p = await loadVideoParams(true);
      const base = sanitizeSeg(renderTpl(cur.config['相片文件名'] || '', row, tsStamp())) || 'video';
      const r = await plugin.recordVideo({
        name: `${base}_视频.mp4`,
        subdir: rowSubdir() || '验收照片',
        maxSeconds: parseInt(p.max_seconds || 60, 10),
        // 原生转码压缩（v0.14）：缩放到 video_max_height、码率 video_maxrate_k，保留声音
        transcode: p.transcode === '0' ? 0 : 1,
        maxHeight: parseInt(p.max_height || 1080, 10),
        bitrateK: parseInt(p.bitrate_k || 4000, 10),
        quality: p.cam_quality === 0 ? 0 : 1,     // 相机录制质量（1=最高，保证源码率）
      });
      if (r && r.path) {
        const dispPath = `Pictures/${rowSubdir() || '验收照片'}/${base}_视频.mp4`;
        recordShot(dispPath, keyVal(), 'video');
        toast(`视频已保存：${dispPath}${videoSizeNote(r)}`);
        renderShotList();
        clearReturnContext();
        return;
      }
    } catch (e) {
      if (/取消/.test(e.message || '')) { toast('已取消录制'); clearReturnContext(); return; }
      // 原生失败 → 回退文件选择
    }
  }
  $('#videoInput').click();
}

/* 时间戳 YYYYMMDD_HHMMSS（文件名占位符 {{时间}} 用） */
function tsStamp() {
  const d = new Date();
  const p2 = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}_${p2(d.getHours())}${p2(d.getMinutes())}${p2(d.getSeconds())}`;
}

$('#videoInput').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file || curIdx < 0) return;
  const extRaw = (file.name.split('.').pop() || 'mp4').toLowerCase();
  const srcExt = ['mp4', 'mov', 'webm', 'm4v', '3gp'].includes(extRaw) ? extRaw : 'mp4';
  const ext = (srcExt === 'm4v' || srcExt === '3gp') ? 'mp4' : srcExt;   // 统一成通用后缀
  await saveExternalVideo(file, ext);
});

/* 把外部视频（系统相机录制/相册选取）按命名规则另存到手机本地 */
async function saveExternalVideo(file, ext) {
  if (recSaving) { toast('正在保存上一个视频，请稍候', true); return; }
  recSaving = true;
  try {
    const row = allRows[curIdx];
    const now = new Date();
    const ts = now.getFullYear() + String(now.getMonth() + 1).padStart(2, '0') +
      String(now.getDate()).padStart(2, '0') + '_' + String(now.getHours()).padStart(2, '0') +
      String(now.getMinutes()).padStart(2, '0') + String(now.getSeconds()).padStart(2, '0');
    const base = sanitizeSeg(renderTpl(cur.config['相片文件名'] || '', row, ts)) || 'video';
    const fname = `${base}_视频.${ext}`;
    const subdir = rowSubdir() || '验收照片';
    toast(`视频保存中（${(file.size / 1048576).toFixed(1)} MB）…`);
    const saved = await saveVideoLocal(file, fname, subdir);
    recordShot(saved || `${fname}（${(file.size / 1048576).toFixed(1)} MB）`, keyVal(), 'video');
    toast(`视频已保存：${saved || fname}`);
    renderShotList();
  } catch (err) {
    toast('视频保存失败：' + err.message, true);
  } finally {
    recSaving = false;
  }
}

/* 权限（v0.11 标准化）：原生端一次性申请「相机 + 麦克风」，
   摄像头被拒 → 中止；麦克风被拒 → 静默降级为无声录制 */
async function ensureMediaPermissions() {
  const plugin = nativePlugin();
  if (!plugin || typeof plugin.ensureMedia !== 'function') return { camera: true, microphone: false };
  try {
    const r = await plugin.ensureMedia();
    return { camera: !!r.camera, microphone: !!r.microphone };
  } catch (e) {
    return { camera: true, microphone: false };
  }
}

function nativePlugin() {
  return (isNativeApp() && window.Capacitor && window.Capacitor.Plugins)
    ? window.Capacitor.Plugins.AppPermissions : null;
}

async function startRecording() {
  const p = await loadVideoParams();
  const perm = await ensureMediaPermissions();
  if (!perm.camera) {
    toast('未获得相机权限：请在系统设置里允许本应用使用相机', true);
    const plugin = nativePlugin();
    if (plugin && plugin.openSettings) plugin.openSettings().catch(() => {});
    return;
  }
  const wantAudio = perm.microphone;
  const maxH = parseInt(p.max_height || 720, 10);
  const fps = parseInt(p.fps || 30, 10);
  const bps = (parseInt(p.bitrate_k || 2500, 10)) * 1000;
  const mime = pickRecMime();
  const vcon = { facingMode: { ideal: 'environment' },
                 height: { ideal: maxH }, frameRate: { ideal: fps } };
  let hasAudio = wantAudio;
  try {
    recStream = await navigator.mediaDevices.getUserMedia({ video: vcon, audio: wantAudio });
  } catch (e) {
    // 麦克风被拒/不可用 → 降级为无声录制（视频照常可拍）
    hasAudio = false;
    recStream = await navigator.mediaDevices.getUserMedia({ video: vcon, audio: false });
  }
  const opts = { videoBitsPerSecond: bps };
  if (mime) opts.mimeType = mime;
  recChunks = [];
  try { recRecorder = new MediaRecorder(recStream, opts); }
  catch (e) { recRecorder = new MediaRecorder(recStream); }
  recRecorder.ondataavailable = (ev) => { if (ev.data && ev.data.size) recChunks.push(ev.data); };
  recRecorder.onstop = () => finishRecording();
  $('#recPreview').srcObject = recStream;
  $('#recMask').classList.remove('hidden');
  const fmtName = /mp4/.test(mime || '') ? 'MP4' : 'WebM';
  $('#recHint').textContent = `${maxH}p · ${(bps / 1000000).toFixed(1)} Mbps · `
    + `${hasAudio ? '有声' : '无声'} · ${fmtName} · 只存手机`;
  $('#recTime').textContent = '00:00';
  recStartAt = Date.now();
  recRecorder.start(1000);
  recTimer = setInterval(() => {
    const sec = Math.round((Date.now() - recStartAt) / 1000);
    $('#recTime').textContent = fmtSec(sec);
    if (sec >= parseInt(p.max_seconds || 60, 10)) stopRecording();   // 到单段上限自动停
  }, 500);
}

function stopRecording() {
  clearInterval(recTimer); recTimer = null;
  if (recRecorder && recRecorder.state !== 'inactive') recRecorder.stop();
  else finishRecording();
}

function cancelRecording() {
  clearInterval(recTimer); recTimer = null;
  recChunks = [];
  if (recRecorder && recRecorder.state !== 'inactive') {
    recRecorder.onstop = null;
    recRecorder.stop();
  }
  releaseRec();
  $('#recMask').classList.add('hidden');
  toast('已取消录制');
}

function releaseRec() {
  if (recStream) recStream.getTracks().forEach((t) => t.stop());
  recStream = null; recRecorder = null;
  $('#recPreview').srcObject = null;
}

$('#recStop').addEventListener('click', stopRecording);
$('#recCancel').addEventListener('click', cancelRecording);

/* 停止 → 保存到手机（不上传） */
async function finishRecording() {
  const mime = (recRecorder && recRecorder.mimeType) || 'video/webm';
  const blob = new Blob(recChunks, { type: mime });
  releaseRec();
  $('#recMask').classList.add('hidden');
  if (!blob.size) { toast('录制失败：没有数据', true); return; }
  if (recSaving) return;
  recSaving = true;
  try {
    const row = allRows[curIdx];
    const now = new Date();
    const ts = now.getFullYear() + String(now.getMonth() + 1).padStart(2, '0') +
      String(now.getDate()).padStart(2, '0') + '_' + String(now.getHours()).padStart(2, '0') +
      String(now.getMinutes()).padStart(2, '0') + String(now.getSeconds()).padStart(2, '0');
    const base = sanitizeSeg(renderTpl(cur.config['相片文件名'] || '', row, ts)) || 'video';
    const ext = mimeExt(mime);
    const fname = `${base}_视频.${ext}`;
    const subdir = rowSubdir() || '验收照片';
    const saved = await saveVideoLocal(blob, fname, subdir);
    const dispPath = saved || `${fname}（${(blob.size / 1048576).toFixed(1)} MB）`;
    recordShot(dispPath, keyVal(), 'video');
    if (ext !== 'mp4') {
      toast(`本机只能录 ${ext.toUpperCase()}（相册常不支持），已切换系统相机，请重录一次（MP4 保证可播）`, true);
      recordShot(`${dispPath}｜${ext.toUpperCase()}（可能无法播放）`, keyVal(), 'video');
      renderShotList();
      setTimeout(() => $('#videoInput').click(), 1200);   // 自动引导系统相机重录
      return;
    }
    toast(`视频已保存：${dispPath}`);
    renderShotList();
  } catch (err) {
    toast('视频保存失败：' + err.message, true);
  } finally {
    recSaving = false;
  }
}

/* 本地保存链：saveVideo（Movies 相册，需新版 APK）→ saveFile（下载目录）→ 浏览器下载 */
async function saveVideoLocal(blob, fname, subdir) {
  // blob 可以是 Blob 或 File（系统相机录制产物）
  const plugin = nativePlugin();
  if (plugin) {
    const b64 = await blobToBase64(blob);
    if (typeof plugin.saveVideo === 'function') {
      try {
        const r = await plugin.saveVideo({ base64: b64, name: fname, subdir });
        // 与照片同目录（Pictures/{参数目录}/），相册里照片与视频同处一个文件夹
        if (r && r.path) return `Pictures/${subdir}/${fname}`;
      } catch (e) { /* 落到下一档 */ }
    }
    if (typeof plugin.saveFile === 'function') {
      try {
        const r = await plugin.saveFile({ base64: b64, name: fname });
        if (r && r.path) return r.path;
      } catch (e) { /* 落到下一档 */ }
    }
  }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = fname;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  return `已下载：${fname}（${(blob.size / 1048576).toFixed(1)} MB）`;
}

/* ── 轨迹（v0.19）：**原生后台定位优先**（BgLocation 插件，仅 GPS_PROVIDER，location 型前台服务 →
   息屏/切后台继续记录），浏览器或插件缺失时回退 WebView watchPosition（仅前台）。
   轨迹点实时存 localStorage：页面被系统重载/进程被杀也不丢已采点位。 ── */
let trackWatch = null;        // 'bg' = 原生插件模式；number = web watchPosition id
let trackBgPlugin = null;
let trackPts = [];
const TRACK_LS_KEY = 'hqz_sup_track_pts';

function bgLocationPlugin() {
  try {
    return (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.BgLocation)
      ? window.Capacitor.Plugins.BgLocation : null;
  } catch (e) { return null; }
}
function saveTrackPts() {
  try { localStorage.setItem(TRACK_LS_KEY, JSON.stringify(trackPts.slice(-5000))); } catch (e) {}
}
function loadSavedTrackPts() {
  try { return JSON.parse(localStorage.getItem(TRACK_LS_KEY) || '[]'); } catch (e) { return []; }
}
function clearSavedTrackPts() { try { localStorage.removeItem(TRACK_LS_KEY); } catch (e) {} }

/* 采集一个点（原生/wweb 两种来源共用；t 为毫秒时间戳） */
function trackPush(lat, lng, ele, t) {
  trackPts.push({
    lat,
    lng,
    ele: (ele == null ? null : ele),
    t: new Date(t || Date.now()).toISOString().replace(/\.\d+Z$/, 'Z'),
  });
  $('#btnTrack').textContent = `● 记录中 ${trackPts.length} 点`;
  saveTrackPts();       // 边录边落盘（页面重载/被杀不丢点）
}

$('#btnTrack').addEventListener('click', () => (trackWatch === null ? startTrack() : stopTrack()));

function startTrack() {
  trackPts = [];
  const plugin = bgLocationPlugin();
  if (plugin && typeof plugin.startWatcher === 'function') {
    trackBgPlugin = plugin;
    trackWatch = 'bg';
    try {
      // 回调式：插件每次定位 resolve 一次
      plugin.startWatcher({
        title: '轨迹记录中',
        message: '正在后台记录验收轨迹（仅 GPS，息屏继续）',
      }, (loc, err) => {
        if (trackWatch !== 'bg') return;              // 已停止 → 丢弃
        if (err) { toast('定位失败：' + (err.message || err), true); return; }
        if (!loc) return;
        trackPush(loc.latitude, loc.longitude, null, loc.time);
      });
      $('#btnTrack').classList.add('recording');
      $('#btnTrack').textContent = '● 记录中 0 点';
      toast('已启用原生后台定位（只收 GPS 卫星定位，息屏/切后台继续记录）');
      return;
    } catch (e) {
      trackBgPlugin = null;
      trackWatch = null;                              // 落回 watchPosition
    }
  }
  if (!navigator.geolocation) { toast('当前环境不支持定位', true); return; }
  trackWatch = navigator.geolocation.watchPosition(
    (p) => trackPush(p.coords.latitude, p.coords.longitude, p.coords.altitude, p.timestamp),
    (err) => toast('定位失败：' + err.message, true),
    { enableHighAccuracy: true, maximumAge: 2000, timeout: 15000 });
  $('#btnTrack').classList.add('recording');
  $('#btnTrack').textContent = '● 记录中 0 点';
  toast('轨迹记录已开始（WebView 模式，仅前台有效）');
}

/* 停止并上传（返回 Promise，便于互斥逻辑 await；口径同 hqz-survey F2） */
async function stopTrack(silent) {
  if (trackWatch === 'bg') {
    try { trackBgPlugin && trackBgPlugin.stopWatcher().catch(() => {}); } catch (e) {}
    trackBgPlugin = null;
  } else if (trackWatch !== null) {
    navigator.geolocation.clearWatch(trackWatch);
  }
  trackWatch = null;
  $('#btnTrack').classList.remove('recording');
  $('#btnTrack').textContent = '◎ 轨迹';
  // 关键：先把本轮点集"取走并清空"，再异步上传 —— 否则用户马上开始新一轮记录时，
  // 上一轮的收尾会把新一轮的点清掉（竞态）
  const pts = trackPts;
  trackPts = [];
  clearSavedTrackPts();
  const n = pts.length;
  if (n < 2) {
    if (!silent) toast('有效定位点不足 2 个，未上传', true);
    return;
  }
  const gpx = buildGpx(pts);
  const cls = keyVal() || '无小班';
  const name = `轨迹_${sanitizeSeg(cls)}_${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '')}.gpx`;
  const fd = new FormData();
  fd.append('file', new Blob([gpx], { type: 'application/gpx+xml' }), name);
  try {
    const r = await api('/api/track', { method: 'POST', body: fd });
    toast(`轨迹已上传：${r.file}（${n} 点）`);
  } catch (err) {
    toast('轨迹上传失败：' + err.message, true);
  }
}

/* 功能互斥（v0.20，口径同 hqz-survey F2）：同一时刻只做一个"长时间记录类"动作。
   开始录像 / 切换小班 / 返回列表 时若轨迹仍在记录 → 先自动停止并保存上一段。 */
async function autoStopTrackIfRecording(reason) {
  if (trackWatch === null) return false;
  const n = trackPts.length;
  toast(`${reason}：已自动停止并保存上一段轨迹（${n} 点）`);
  await stopTrack(true);
  return true;
}

/* 页面被系统重载后：若本机还存着未上传的轨迹点 → 接着录（原生模式则重启 watcher） */
async function restoreTrackIfAny() {
  const saved = loadSavedTrackPts();
  if (!saved || saved.length < 1) return;
  trackPts = saved;
  const plugin = bgLocationPlugin();
  if (plugin && typeof plugin.startWatcher === 'function') {
    try { await plugin.stopWatcher(); } catch (e) {}     // 清掉旧页面残留的 watcher
    trackWatch = null;
    startTrack();
    trackPts = saved;                                   // startTrack 会清空，恢复已采点
    saveTrackPts();
    $('#btnTrack').textContent = `● 记录中 ${trackPts.length} 点`;
    toast(`已恢复未上传的轨迹记录（${saved.length} 点），继续采集中`);
  } else {
    toast(`本机有未上传的轨迹记录（${saved.length} 点），点「◎ 轨迹」可上传`, true);
  }
}

function buildGpx(pts) {
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const seg = pts.map((p) =>
    `      <trkpt lat="${p.lat.toFixed(6)}" lng="${p.lng.toFixed(6)}">` +
    (p.ele != null ? `<ele>${p.ele.toFixed(1)}</ele>` : '') +
    `<time>${p.t}</time></trkpt>`).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="hqz-supervision" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>${esc(cur ? cur.name + ' · ' + cur.sheet_name : '轨迹')}</name>
    <time>${new Date().toISOString().replace(/\.\d+Z$/, 'Z')}</time></metadata>
  <trk><name>${esc(cur ? cur.sheet_name : '')}</name>
    <trkseg>
${seg}
    </trkseg></trk>
</gpx>`;
}

/* ── 启动 ── */
async function boot() {
  try {
    const me = await api('/api/me');      // 令牌或 cookie 任一有效即可
    loggedIn = true;
    $('#whoami').dataset.user = me.user || '';
    $('#whoami').textContent = me.user || '';
    await route();
    ensurePermissionsUpfront();           // 登录后一次性申请权限（见函数注释）
    await submitDraftIfAny();             // v0.28：补交上次没送出去的草稿（进程被杀/切后台丢包）
    await recoverPendingVideo();          // 相机返回导致页面重载时，补记录像结果
    await restoreTrackIfAny();            // 轨迹：页面被重载后接着录（原生在跑则续采）
  } catch (e) {
    setToken('');
    show('login');
  }
}

/* 视频体积/压缩说明文案（v0.17）：区分"已压缩/未压缩/App 版本过旧" */
function videoSizeNote(v) {
  const mb = (n) => (n ? (n / 1048576).toFixed(1) + 'MB' : '');
  if (v.transcoded === true) return `（已压缩 ${mb(v.origSize)} → ${mb(v.size)}）`;
  if (v.transcoded === false) return `（未压缩，原片 ${mb(v.size)}）`;
  return '（当前 App 版本不支持压缩，请安装最新 APK）';
}

/* 相机返回后页面若被系统重载 → 从原生取回"最近一次录像"，补进拍摄记录（v0.17，v0.18 修时序）
   时序坑：boot 里 route() 只是发起导航（工作簿要联网加载），若立刻补记，cur 还是 null 会失败；
   且原生可能**还在转码/写盘**（last_video 尚未产生）。所以：
   ① 等详情页就绪（cur 存在）再取；② pending=true 时短暂重试；③ 先 consume=false 查，确认有结果才清标记。 */
async function recoverPendingVideo() {
  for (let attempt = 0; attempt < 20; attempt++) {
    if (!getReturnContext()) return;                 // 没有"相机返回"标记 → 无事可做
    const plugin = nativePlugin();
    if (!plugin || typeof plugin.getLastVideo !== 'function') { clearReturnContext(); return; }
    const ready = !!(cur && allRows && allRows.length);   // 详情页数据就绪
    if (!ready) { await new Promise((r) => setTimeout(r, 300)); continue; }
    let r = null;
    try { r = await plugin.getLastVideo({ consume: false }); } catch (e) { return; }
    const v = (r && r.video) || null;
    if (v && v.path) {
      const dispPath = `Pictures/${v.subdir || '验收照片'}/${v.name || ''}`;
      recordShot(dispPath, keyVal(), 'video');
      renderShotList();
      toast(`视频已保存：${dispPath}${videoSizeNote(v)}`);
      try { await plugin.getLastVideo({ consume: true }); } catch (e) {}
      clearReturnContext();
      return;
    }
    if (!r || !r.pending) { clearReturnContext(); return; }   // 非"录制中"且无结果 → 用户取消了
    await new Promise((res) => setTimeout(res, 600));         // 转码/写盘还没完 → 稍后重试
  }
}

/* App 回到前台时再补一次（页面没被重载、但回调仍可能丢的情况） */
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && getReturnContext()) {
    setTimeout(() => recoverPendingVideo().catch(() => {}), 400);
  }
});

/* 启动时一次性申请权限（v0.15）：把弹窗集中到"刚进 App"这一步，
   之后拍照/录像/轨迹都不再中途弹权限（Android 不允许安装即授权，只能首次运行时申请）。 */
/* v0.15：把弹窗集中到"刚进 App"这一步，之后拍照/录像/轨迹都不再中途弹权限。
   v0.28.1 修正：**引导改成按"安装"记一次，而不是只记在内存里**。
   原因（用户实测："视频保存后华为弹出应用信息"）：录像走系统相机时，华为省电策略常把页面
   重载，内存里的 permsAsked 随之归零 → 回来 boot() 又跑一遍 ensureBackground() →
   原生里的 openAutostartSettings()/requestIgnoreBatteryOptimizations() 是**无条件**执行的，
   于是每次都再弹一次「电池优化白名单」对话框 + 跳一次华为「自启动/后台运行」页。
   现在用 localStorage 记住（键名带版本号，将来需要重新引导时升版本号即可）。
   需要手动再跑一次：控制台执行 supAskPermsAgain()。 */
const PERMS_ASKED_KEY = 'hqz_sup_perms_asked_v1';
let permsAsked = false;

function permsAlreadyAsked() {
  try { return localStorage.getItem(PERMS_ASKED_KEY) === '1'; } catch (e) { return false; }
}
function supAskPermsAgain() {          // 手动重置（排查/用户反馈"权限没给全"时用）
  try { localStorage.removeItem(PERMS_ASKED_KEY); } catch (e) {}
  permsAsked = false;
  ensurePermissionsUpfront();
  return '已重置，将重新引导一次权限';
}

async function ensurePermissionsUpfront() {
  if (permsAsked || permsAlreadyAsked()) { permsAsked = true; return; }
  permsAsked = true;
  try { localStorage.setItem(PERMS_ASKED_KEY, '1'); } catch (e) {}   // 立刻记上，中途失败也不重复骚扰
  const plugin = (isNativeApp() && window.Capacitor && window.Capacitor.Plugins)
    ? window.Capacitor.Plugins.AppPermissions : null;
  if (!plugin) return;                    // 浏览器内不做原生申请
  try {
    if (typeof plugin.ensureMedia === 'function') await plugin.ensureMedia();   // 相机 + 麦克风
  } catch (e) {}
  try {
    if (typeof plugin.request === 'function') await plugin.request({ type: 'location' });   // 轨迹/水印坐标
  } catch (e) {}
  try {
    // 后台运行相关（v0.18）：通知 + 电池优化白名单 + 厂商自启动页
    // 目的：录像时调用系统相机，本 App 退到后台不会被省电策略杀掉（否则页面被重载、回调丢失）
    if (typeof plugin.ensureBackground === 'function') {
      const bg = await plugin.ensureBackground();
      if (bg && bg.batteryWhitelisted) {
        toast('后台运行已允许（电池优化白名单）');
      } else if (bg && bg.batteryAsked) {
        toast('请在系统弹窗中选择「允许」以保证录像不被中断', false);
      }
    }
  } catch (e) {}
}

boot();
