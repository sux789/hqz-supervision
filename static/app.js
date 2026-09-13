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

async function api(url, opt) {
  const r = await fetch((window.SUP_BASE || '') + url, opt);
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
    boot();
  } catch (err) {
    $('#loginErr').textContent = err.message;
  }
});

$('#btnLogout').addEventListener('click', async () => {
  await api('/api/logout', { method: 'POST' }).catch(() => {});
  location.href = '/';
});

/* ── 缓存：记住上次编辑位置（同 hqz-survey last_project 惯例） ── */
function rememberLast() {
  try {
    localStorage.setItem('hqz_sup_last', JSON.stringify(
      { wb: cur.id, idx: curIdx, name: cur.name, xh: rowVal('小班号') }));
  } catch (e) {}
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
      window.open((window.SUP_BASE || '') + `/api/workbooks/${w.id}/export`, '_blank');
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
  $('#photoHint').textContent = (isNativeApp()
    ? '📁 拍照仅存本机系统相册 Pictures/' + (rowSubdir() || '(参数未配置目录)')
    : '📁 拍照仅下载到本机（服务器不留存）')
    + (hasVideo ? '；🎬 视频上传后由服务器压缩并同步云盘（本地不留视频）' : '');
}

/* ── 导出 Excel（按上传模板回填） ── */
$('#btnExport').addEventListener('click', () => {
  if (!cur) return;
  window.open((window.SUP_BASE || '') + `/api/workbooks/${cur.id}/export`, '_blank');
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
    // 压缩参数：参数 sheet 可下发「压缩最长边/压缩质量」，缺省 1440/0.80（方案 A）
    const maxSide = parseInt(cur.config['压缩最长边'], 10) || PHOTO_MAX_SIDE;
    const quality = Math.min(1, Math.max(0.1, parseFloat(cur.config['压缩质量']) || PHOTO_QUALITY));
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

/* ── 视频（v0.10.1）：手机端 MediaRecorder 边录边压 为主，选择已有视频走服务器压缩为辅 ──
   浏览器/WebView 无法转码已有视频，故"压缩"在手机上以"录制时按目标码率编码"实现；
   录制参数（最长边/码率/帧率/最长时长）全部来自后台「视频压缩」设置。 */
let recStream = null, recRecorder = null, recChunks = [], recTimer = null;
let recStartAt = 0, recParams = null, recUploading = false;

function videoApiReady() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
}

function pickRecMime() {
  const cands = ['video/mp4;codecs=avc1.42E01E,mp4a.40.2', 'video/mp4',
                 'video/webm;codecs=h264', 'video/webm;codecs=vp8', 'video/webm'];
  for (const m of cands) {
    try { if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) return m; } catch (e) {}
  }
  return '';
}

const mimeExt = (mime) => (/mp4/.test(mime) ? 'mp4' : 'webm');

function fmtSec(n) {
  return String(Math.floor(n / 60)).padStart(2, '0') + ':' + String(n % 60).padStart(2, '0');
}

async function loadVideoParams() {
  if (recParams) return recParams;
  recParams = await api('/api/video/params');
  return recParams;
}

$('#btnVideo').addEventListener('click', async () => {
  if (curIdx < 0 || !allRows[curIdx]) { toast('请先选择小班', true); return; }
  let p = null;
  try { p = await loadVideoParams(); } catch (e) { p = null; }
  const canRec = p && p.rec === '1' && videoApiReady();
  if (!p || !canRec) {
    // 后台关闭录制或环境不支持 → 直接走文件选择（服务器压缩）
    $('#videoInput').click();
    return;
  }
  $('#vmNote').textContent = `录制参数：${p.max_height}p · ${(p.bitrate_k / 1000).toFixed(1)} Mbps · `
    + `${p.fps}fps · 最长 ${p.max_seconds}s（后台可调）`;
  $('#videoMenuMask').classList.remove('hidden');
});

$('#vmCancel').addEventListener('click', () => $('#videoMenuMask').classList.add('hidden'));
$('#videoMenuMask').addEventListener('click', (e) => {
  if (e.target === $('#videoMenuMask')) $('#videoMenuMask').classList.add('hidden');
});
$('#vmPick').addEventListener('click', () => {
  $('#videoMenuMask').classList.add('hidden');
  $('#videoInput').click();
});
$('#vmRec').addEventListener('click', () => {
  $('#videoMenuMask').classList.add('hidden');
  startRecording().catch((e) => {
    toast('无法启动录制：' + e.message + '（已切换为选择已有视频）', true);
    $('#videoInput').click();
  });
});

async function startRecording() {
  const p = recParams || {};
  const maxH = parseInt(p.max_height || 720, 10);
  const fps = parseInt(p.fps || 30, 10);
  const bps = (parseInt(p.bitrate_k || 2500, 10)) * 1000;
  const mime = pickRecMime();
  recStream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: 'environment' },
             height: { ideal: maxH }, frameRate: { ideal: fps } },
    audio: false,   // 当前 APK 未声明 RECORD_AUDIO，先无声录制（有声需更新 APK）
  });
  const opts = { videoBitsPerSecond: bps };
  if (mime) opts.mimeType = mime;
  recChunks = [];
  try {
    recRecorder = new MediaRecorder(recStream, opts);
  } catch (e) {
    recRecorder = new MediaRecorder(recStream);   // 兜底：不指定码率
  }
  recRecorder.ondataavailable = (ev) => { if (ev.data && ev.data.size) recChunks.push(ev.data); };
  recRecorder.onstop = () => finishRecording();
  const prev = $('#recPreview');
  prev.srcObject = recStream;
  $('#recMask').classList.remove('hidden');
  $('#recHint').textContent = `${maxH}p · ${(bps / 1000000).toFixed(1)} Mbps · 无声`;
  recStartAt = Date.now();
  recRecorder.start(1000);
  recTimer = setInterval(() => {
    const sec = Math.round((Date.now() - recStartAt) / 1000);
    $('#recTime').textContent = fmtSec(sec);
    if (sec >= parseInt(p.max_seconds || 60, 10)) stopRecording();   // 到时长上限自动停
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

async function finishRecording() {
  const mime = (recRecorder && recRecorder.mimeType) || 'video/webm';
  const blob = new Blob(recChunks, { type: mime });
  releaseRec();
  $('#recMask').classList.add('hidden');
  if (!blob.size) { toast('录制失败：没有数据', true); return; }
  await uploadVideo(blob, mimeExt(mime), true);
}

/* 文件选择（已有视频）→ 服务器压缩后同步 */
$('#videoInput').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file || curIdx < 0) return;
  const ext = (file.name.split('.').pop() || 'mp4').toLowerCase();
  await uploadVideo(file, ['mp4', 'webm', 'mov'].includes(ext) ? ext : 'mp4', false);
});

/* 统一上传：precompressed=1 表示手机端已压缩（录制），服务器不再转码 */
async function uploadVideo(blob, ext, precompressed) {
  if (recUploading) { toast('已有视频正在上传，请稍候', true); return; }
  const row = allRows[curIdx];
  const mb = blob.size / 1048576;
  if (mb > 400) { toast(`视频过大（${mb.toFixed(0)} MB），请分段录制`, true); return; }
  recUploading = true;
  toast(`视频上传中（${mb.toFixed(1)} MB）…`);
  try {
    const st = await api('/api/sync/enabled');
    if (!st.enabled) { toast('视频需先开启云同步（后台「同步设置」）', true); return; }
    const now = new Date();
    const ts = now.getFullYear() + String(now.getMonth() + 1).padStart(2, '0') +
      String(now.getDate()).padStart(2, '0') + '_' + String(now.getHours()).padStart(2, '0') +
      String(now.getMinutes()).padStart(2, '0') + String(now.getSeconds()).padStart(2, '0');
    const base = sanitizeSeg(renderTpl(cur.config['相片文件名'] || '', row, ts)) || 'video';
    const subdir = rowSubdir();
    const xh = rowVal('小班号');
    const fd = new FormData();
    fd.append('file', blob, `${base}_视频.${ext}`);
    fd.append('workbook_id', cur.id);
    fd.append('filename', base);
    fd.append('subdir', subdir || '');
    fd.append('xiaoban', xh || '');
    fd.append('ext', ext);
    if (precompressed) fd.append('precompressed', '1');
    await api('/api/video', { method: 'POST', body: fd });
    recordShot(subdir ? `${subdir}/${base}_视频.${ext}` : `${base}_视频.${ext}`, xh, 'video');
    toast(`视频已上传（${mb.toFixed(1)} MB），云端同步中`);
    renderShotList();
  } catch (err) {
    toast('视频上传失败：' + err.message, true);
  } finally {
    recUploading = false;
  }
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
    await api('/api/workbooks');
    loggedIn = true;
    $('#whoami').textContent = $('#whoami').dataset.user || '';
    await route();
  } catch (e) {
    show('login');
  }
}

boot();
