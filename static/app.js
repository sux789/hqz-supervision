/* hqz-supervision 前端：登录 / 工作簿列表 / 参数驱动的网格填表 */
'use strict';

const $ = (s) => document.querySelector(s);
const el = (h) => { const d = document.createElement('div'); d.innerHTML = h.trim(); return d.firstChild; };

let grid = null;          // jspreadsheet 实例
let cur = null;           // 当前工作簿 {id, headers, rows, config}
let allRows = [];         // 未过滤全量行（编辑后同步）

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
  const r = await fetch(url, opt);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}

/* ── 视图切换 ── */
function show(view) {
  $('#viewLogin').classList.toggle('hidden', view !== 'login');
  $('#viewMain').classList.toggle('hidden', view === 'login');
  $('#viewList').classList.toggle('hidden', view !== 'list');
  $('#viewGrid').classList.toggle('hidden', view !== 'grid');
  $('#btnBack').classList.toggle('hidden', view !== 'grid');
  $('#pageName').textContent = view === 'grid' ? (cur ? cur.name : '') : '工作簿';
}

/* ── 登录 ── */
$('#loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await api('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('#loginUser').value, password: $('#loginPass').value }),
    });
    boot();
  } catch (err) {
    $('#loginErr').textContent = err.message;
  }
});

$('#btnLogout').addEventListener('click', async () => {
  await api('/api/logout', { method: 'POST' }).catch(() => {});
  location.href = '/';
});

/* ── 工作簿列表 ── */
async function loadList() {
  const data = await api('/api/workbooks');
  const box = $('#wbList');
  box.innerHTML = '';
  if (!data.workbooks.length) {
    box.appendChild(el(`<div class="muted" style="padding:12px">还没有工作簿，先上传一个 Excel。</div>`));
  }
  for (const w of data.workbooks) {
    box.appendChild(el(`
      <div class="wb-item" data-id="${w.id}">
        <span class="wb-name">${w.name}</span>
        <span class="muted">${w.sheet_name} · ${w.uploaded_at}</span>
        <span class="spacer"></span>
        <span class="btn primary">打开</span>
      </div>`));
  }
  box.querySelectorAll('.wb-item').forEach((n) => {
    n.addEventListener('click', () => openWorkbook(+n.dataset.id));
  });
}

$('#fileInput').addEventListener('change', async (e) => {
  const files = [...e.target.files];
  e.target.value = '';
  for (const f of files) await uploadOne(f, $('#uploadMsg'));
  await loadList();
});

async function uploadOne(f, msgEl) {
  const fd = new FormData();
  fd.append('file', f);
  try {
    const r = await api('/api/workbooks', { method: 'POST', body: fd });
    toast(`已上传「${f.name}」：${r.rows} 行`);
    if (msgEl) msgEl.textContent = '';
  } catch (err) {
    const m = `「${f.name}」上传失败：${err.message}`;
    if (msgEl) msgEl.textContent = m; else toast(m, true);
  }
}

$('#btnBack').addEventListener('click', async () => { show('list'); await loadList(); });

/* ── 打开工作簿：参数驱动渲染 ── */
async function openWorkbook(id) {
  cur = await api(`/api/workbooks/${id}`);
  allRows = cur.rows.map((r) => r.map((v) => (v == null ? '' : String(v))));
  renderSearchBar();
  renderGrid();
  show('grid');
}

function cfgList(key) {           // 参数列表类值拆分
  return (cur.config[key] || '').split(';').map((s) => s.trim()).filter(Boolean);
}

function renderSearchBar() {
  const bar = $('#searchBar');
  bar.innerHTML = '<b style="align-self:center">搜索</b>';
  const items = (cur.config['搜索选项'] || '').split(';').map((s) => s.trim()).filter(Boolean);
  items.forEach((item, i) => {
    const [field, type] = item.split('|').map((s) => s.trim());
    if (type === 'select') {
      const uniq = [...new Set(allRows.map((r) => r[cur.headers.indexOf(field)]))].filter(Boolean).sort();
      const sel = el(`<select data-i="${i}"><option value="">${field}（全部）</option>
        ${uniq.map((v) => `<option>${v}</option>`).join('')}</select>`);
      sel.addEventListener('change', applyFilter);
      bar.appendChild(sel);
    } else {                     // search：前端本地即时过滤（不发服务端请求）
      const inp = el(`<input data-i="${i}" placeholder="${field} 搜索" autocomplete="off">`);
      let h; inp.addEventListener('input', () => { clearTimeout(h); h = setTimeout(applyFilter, 200); });
      bar.appendChild(inp);
    }
  });
}

function applyFilter() {
  const items = (cur.config['搜索选项'] || '').split(';').map((s) => s.trim()).filter(Boolean);
  const conds = [];
  $('#searchBar').querySelectorAll('select,input').forEach((n) => {
    const q = n.value.trim();
    if (!q) return;
    const field = items[+n.dataset.i].split('|')[0].trim();
    conds.push({ col: cur.headers.indexOf(field), q });
  });
  const rows = allRows.filter((r) => conds.every(({ col, q }) =>
    String(r[col] || '').toLowerCase().includes(q.toLowerCase())));
  grid.setData(rows.length ? rows : [allRows[0] || []]);
  $('#gridMeta').textContent = `${rows.length} / ${allRows.length} 行（搜索为前端本地过滤）`;
}

function renderGrid() {
  const hide = new Set(cfgList('不显示列'));
  const editable = new Set(cfgList('可编辑列'));
  const optKey = Object.keys(cur.config).find((k) => k.endsWith('选项'));
  const optCol = optKey ? optKey.slice(0, -2) : '';   // 验收结果选项 → 验收结果
  const options = optCol ? cfgList(optKey) : [];

  const columns = cur.headers.map((h) => {
    const idx = cur.headers.indexOf(h);
    const c = { title: h, width: Math.max(90, Math.min(200, h.length * 22)), readOnly: !editable.has(h) };
    if (hide.has(h)) c.type = 'hidden';
    if (h === optCol && options.length) { c.type = 'dropdown'; c.source = options; }
    return c;
  });

  const el0 = $('#gridEl');
  el0.innerHTML = '';
  if (grid) { try { jspreadsheet.destroy(el0); } catch (e) {} grid = null; }
  grid = jspreadsheet(el0, {
    data: allRows,
    columns,
    minDimensions: [cur.headers.length, 1],
    allowDeleteRow: false, allowInsertRow: false, allowInsertColumn: false, allowDeleteColumn: false,
    allowRenameColumn: false, columnDrag: false, columnSorting: false,
    search: false, toolbar: false, tableOverflow: true, tableWidth: '100%', lazyLoading: true,
    onload: () => { $('#gridMeta').textContent = `${allRows.length} 行 · ${cur.sheet_name}`; },
  });
}

$('#btnSave').addEventListener('click', async () => {
  const data = grid.getData();
  // 搜索过滤状态下只回写当前全量；全量行以 allRows 为准
  const visible = grid.getData();
  try {
    await api(`/api/workbooks/${cur.id}/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rows: visible }),
    });
    allRows = visible.map((r) => r.map((v) => (v == null ? '' : String(v))));
    toast('已保存');
  } catch (err) { toast('保存失败：' + err.message, true); }
});

/* ── 启动 ── */
async function boot() {
  // 探测会话：拉列表成功即已登录，401 则显示登录页
  try {
    await loadList();
    $('#whoami').textContent = $('#whoami').dataset.user || '';
    show('list');
  } catch (e) {
    show('login');
  }
}

boot();
