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
    view === 'detail' ? (cur ? `${rowVal('小班号') || '详情'} · ${cur.name}` : '') : '工作簿';
  $('#btnBack').onclick = () => nav('#/');
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
    boot();
  } catch (err) {
    $('#loginErr').textContent = err.message;
  }
});

$('#btnLogout').addEventListener('click', async () => {
  await api('/api/logout', { method: 'POST' }).catch(() => {});
  setToken('');                        // 令牌一并清除
  location.href = '/';
});

/* ── 缓存：记住上次编辑位置（同 hqz-survey last_project 惯例） ── */
function rememberLast() {
  try {
    localStorage.setItem('hqz_sup_last', JSON.stringify(
      { wb: cur.id, idx: curIdx, name: cur.name, xh: rowVal('小班号') }));
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
      window.open(withToken((window.SUP_BASE || '') + `/api/workbooks/${w.id}/export`), '_blank');
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
    const f = cur.headers.includes('小班号') ? '小班号' : cur.headers[0];
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
  const xh = rowVal('小班号');
  $('#selMeta').innerHTML = allRows.length
    ? `匹配 ${cands.length} / ${allRows.length} 个小班 · 当前：<span class="cur-xh">${escapeHtml(xh || '(未编号)')}</span>`
    : '该工作簿没有数据行';
}

function jumpRow(idx) {
  if (!allRows[idx]) return;
  curIdx = idx;
  syncSelectorToRow(idx);
  renderForm();
  renderShotList();
  rememberLast();
  $('#pageName').textContent = `${rowVal('小班号') || '详情'} · ${cur.name}`;
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

let saveTimer = null, saveSeq = 0;
function scheduleRowSave() {
  setSaveStatus('⏳ 保存中…', 'saving');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(doRowSave, 800);   // 输入停顿 0.8s 自动落库（onchange 本身含 blur 时机）
}

async function doRowSave() {
  const seq = ++saveSeq;
  try {
    await api(`/api/workbooks/${cur.id}/rows/${curIdx}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: allRows[curIdx] }),
    });
    if (seq === saveSeq) setSaveStatus('✓ 已保存', 'ok');
  } catch (e) {
    if (seq === saveSeq) setSaveStatus('✗ 保存失败（改动仍在页面，重新编辑即重试）', 'err');
  }
}

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
$('#btnExport').addEventListener('click', () => {
  if (!cur) return;
  window.open(withToken((window.SUP_BASE || '') + `/api/workbooks/${cur.id}/export`), '_blank');
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
    syncAcceptCols(oy, v);            // 内置规则：选结果→自动填验收人+验收日期；选空→三字段全清
    scheduleRowSave();
  });
  sel.addEventListener('blur', () => { cell.textContent = allRows[curIdx][oy] || ''; });
});

/* 内置联动规则（通用，非本模板硬编码）：
   「*选项」下拉列选中非空值 → 验收人=当前登录用户、验收日期（或验收时间）=今天（列存在才填，覆盖旧值）
   选中空（视为未验收）     → 验收人 / 验收日期（或验收时间）/ 验收备注 全部清空（列存在才清） */
function syncAcceptCols(resultColIdx, v) {
  const user = ($('#whoami').dataset.user || '').trim();
  const today = new Date().toLocaleDateString('sv-SE');
  let di = cur.headers.indexOf('验收日期');
  if (di < 0) di = cur.headers.indexOf('验收时间');
  const dateCol = di >= 0 ? cur.headers[di] : null;

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
    if (user && setCol('验收人', user)) changedNames.push('验收人');
    if (setCol(dateCol, today)) changedNames.push(dateCol || '验收日期');
    changed = changedNames.length > 0;
    if (changed) toast('已自动填入：' + changedNames.join('、'));
  } else {
    changed = setCol('验收人', '') || changed;
    changed = setCol(dateCol, '') || changed;
    changed = setCol('验收备注', '') || changed;
    if (changed) toast('验收结果已清空：验收人/验收日期/验收备注 一并清空');
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
function renderTpl(tpl, row, now) {
  return (tpl || '').replace(/\{\{(.+?)\}\}/g, (_, key) => {
    key = key.trim();
    if (key === 'sheet名称') return cur.sheet_name;
    if (key === '时间') return now;
    if (key === '拍照人') return ($('#whoami').dataset.user || '').trim();
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
  const xh = rowVal('小班号');
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
    const xh = rowVal('小班号');

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
        recordShot(dispPath, rowVal('小班号'), 'video');
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
    recordShot(saved || `${fname}（${(file.size / 1048576).toFixed(1)} MB）`, rowVal('小班号'), 'video');
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
    recordShot(dispPath, rowVal('小班号'), 'video');
    if (ext !== 'mp4') {
      toast(`本机只能录 ${ext.toUpperCase()}（相册常不支持），已切换系统相机，请重录一次（MP4 保证可播）`, true);
      recordShot(`${dispPath}｜${ext.toUpperCase()}（可能无法播放）`, rowVal('小班号'), 'video');
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

/* ── 轨迹（B3）：watchPosition 采集 → GPX 生成 → 上传后台 ── */
let trackWatch = null;
let trackPts = [];

$('#btnTrack').addEventListener('click', () => (trackWatch === null ? startTrack() : stopTrack()));

function startTrack() {
  if (!navigator.geolocation) { toast('当前环境不支持定位', true); return; }
  trackPts = [];
  trackWatch = navigator.geolocation.watchPosition((p) => {
    trackPts.push({
      lat: p.coords.latitude, lng: p.coords.longitude,
      ele: p.coords.altitude == null ? null : p.coords.altitude,
      t: new Date(p.timestamp).toISOString().replace(/\.\d+Z$/, 'Z'),
    });
    $('#btnTrack').textContent = `● 记录中 ${trackPts.length} 点`;
  }, (err) => toast('定位失败：' + err.message, true),
    { enableHighAccuracy: true, maximumAge: 2000, timeout: 15000 });
  $('#btnTrack').classList.add('recording');
  $('#btnTrack').textContent = '● 记录中 0 点';
  toast('轨迹记录已开始，走完点「◎ 轨迹」停止并上传');
}

function stopTrack() {
  navigator.geolocation.clearWatch(trackWatch);
  trackWatch = null;
  $('#btnTrack').classList.remove('recording');
  $('#btnTrack').textContent = '◎ 轨迹';
  const n = trackPts.length;
  if (n < 2) { trackPts = []; toast('有效定位点不足 2 个，未上传', true); return; }
  const gpx = buildGpx(trackPts);
  const cls = rowVal('小班号') || '无小班';
  const name = `轨迹_${sanitizeSeg(cls)}_${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '')}.gpx`;
  const fd = new FormData();
  fd.append('file', new Blob([gpx], { type: 'application/gpx+xml' }), name);
  api('/api/track', { method: 'POST', body: fd })
    .then((r) => toast(`轨迹已上传：${r.file}（${n} 点）`))
    .catch((err) => toast('轨迹上传失败：' + err.message, true));
  trackPts = [];
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
    await recoverPendingVideo();          // 相机返回导致页面重载时，补记录像结果
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

/* 相机返回后页面若被系统重载 → 从原生取回"最近一次录像"，补进拍摄记录（v0.17） */
async function recoverPendingVideo() {
  const rc = getReturnContext();
  const plugin = nativePlugin();
  if (!rc || !plugin || typeof plugin.getLastVideo !== 'function') { if (rc) clearReturnContext(); return; }
  try {
    const r = await plugin.getLastVideo({ consume: true });
    const v = (r && r.video) || null;
    if (v && v.path) {
      const dispPath = `Pictures/${v.subdir || '验收照片'}/${v.name || ''}`;
      recordShot(dispPath, rowVal('小班号'), 'video');
      renderShotList();
      toast(`视频已保存：${dispPath}${videoSizeNote(v)}`);
    }
  } catch (e) { /* 取不到就算了（可能用户取消了录制） */ }
  clearReturnContext();
}

/* 启动时一次性申请权限（v0.15）：把弹窗集中到"刚进 App"这一步，
   之后拍照/录像/轨迹都不再中途弹权限（Android 不允许安装即授权，只能首次运行时申请）。 */
let permsAsked = false;
async function ensurePermissionsUpfront() {
  if (permsAsked) return;
  permsAsked = true;
  const plugin = (isNativeApp() && window.Capacitor && window.Capacitor.Plugins)
    ? window.Capacitor.Plugins.AppPermissions : null;
  if (!plugin) return;                    // 浏览器内不做原生申请
  try {
    if (typeof plugin.ensureMedia === 'function') await plugin.ensureMedia();   // 相机 + 麦克风
  } catch (e) {}
  try {
    if (typeof plugin.request === 'function') await plugin.request({ type: 'location' });   // 轨迹/水印坐标
  } catch (e) {}
}

boot();
