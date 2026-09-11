/* hqz-supervision 前端：登录 / 工作簿列表 / 参数驱动的网格填表 */
'use strict';

const $ = (s) => document.querySelector(s);
const el = (h) => { const d = document.createElement('div'); d.innerHTML = h.trim(); return d.firstChild; };

let grid = null;          // jspreadsheet 实例
let cur = null;           // 当前工作簿 {id, headers, rows, config}
let allRows = [];         // 未过滤全量行（编辑后同步）
let selRow = -1;          // 当前点选的数据行（拍照上下文）

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
    onselection: (inst, x1, y1) => { selRow = y1; },
    onload: () => { $('#gridMeta').textContent = `${allRows.length} 行 · ${cur.sheet_name}`; },
  });

  // 功能开关（参数「功能」）控制工具栏按钮
  const feats = cfgList('功能');
  $('#btnPhoto').classList.toggle('hidden', !feats.includes('拍照'));
  $('#btnAlbum').classList.toggle('hidden', !feats.includes('拍照'));
  $('#btnTrack').classList.toggle('hidden', !feats.includes('轨迹'));
  $('#btnTrack').classList.toggle('recording', trackWatch !== null);
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

/* ── 拍照（B2）：参数化模板 + 固定日期水印 + 上传 ── */
// 模板渲染：{{列名}}→行值；{{sheet名称}}→当前sheet；{{时间}}→YYYYMMDD_HHMMSS（C02 保留字）
function renderTpl(tpl, row, now) {
  return (tpl || '').replace(/\{\{(.+?)\}\}/g, (_, key) => {
    key = key.trim();
    if (key === 'sheet名称') return cur.sheet_name;
    if (key === '时间') return now;
    const i = cur.headers.indexOf(key);
    return i >= 0 ? String(row[i] == null ? '' : row[i]).trim() : '';
  });
}

function sanitizeSeg(s) {          // 与后端 _safe_segments 同规则
  return s.replace(/[\\/:*?"<>|]+/g, '_').replace(/^[. ]+|[. ]+$/g, '');
}

// 定位：best-effort，2.5s 拿不到就跳过（坐标行不画）
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

// 水印绘制（参考 hqz-survey app.js L3131：黑字白边，左下角，JPEG 0.92）
// 日期行固定输出（C06：水印日期不参数化）
async function drawWatermark(file, remark, coords) {
  let bmp;
  try { bmp = await createImageBitmap(file, { imageOrientation: 'from-image' }); }
  catch (e) { bmp = await createImageBitmap(file); }
  const canvas = document.createElement('canvas');
  canvas.width = bmp.width; canvas.height = bmp.height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(bmp, 0, 0);
  bmp.close && bmp.close();

  const lines = [`日期：${new Date().toLocaleDateString('sv-SE')}`];   // sv-SE → YYYY-MM-DD
  if (coords) lines.push(`坐标：${coords}`);
  // 备注长文本按 22 字符折行，首行带前缀
  for (const [i, seg] of (remark.match(/[\s\S]{1,22}/g) || []).entries()) {
    lines.push((i === 0 ? '备注：' : '') + seg);
  }

  const fs = Math.max(18, Math.round(canvas.width / 34));
  const lh = Math.round(fs * 1.35);
  ctx.font = `${fs}px system-ui,'PingFang SC','Microsoft YaHei',sans-serif`;
  ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
  ctx.lineWidth = Math.max(3, Math.round(fs / 8));
  ctx.strokeStyle = 'rgba(255,255,255,.92)'; ctx.fillStyle = '#111';
  let y = canvas.height - Math.round(fs * 0.6);
  for (let i = lines.length - 1; i >= 0; i--) {
    const x = Math.round(fs * 0.6);
    ctx.strokeText(lines[i], x, y); ctx.fillText(lines[i], x, y);
    y -= lh;
  }
  return new Promise((res, rej) =>
    canvas.toBlob((b) => (b ? res(b) : rej(new Error('水印编码失败'))), 'image/jpeg', 0.92));
}

$('#btnPhoto').addEventListener('click', () => {
  if (!cur || selRow < 0 || !allRows[selRow]) { toast('请先在网格中点选一行数据', true); return; }
  $('#photoInput').click();
});

$('#photoInput').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file || !cur) return;
  const row = allRows[selRow];
  if (!row) { toast('请先在网格中点选一行数据', true); return; }
  toast('正在处理水印…');
  try {
    const now = new Date();
    const ts = now.getFullYear() + String(now.getMonth() + 1).padStart(2, '0') +
      String(now.getDate()).padStart(2, '0') + '_' + String(now.getHours()).padStart(2, '0') +
      String(now.getMinutes()).padStart(2, '0') + String(now.getSeconds()).padStart(2, '0');
    const coords = await getCoords();
    const remark = renderTpl(cur.config['相片备注'] || '', row, ts);
    const blob = await drawWatermark(file, remark, coords);
    const filename = sanitizeSeg(renderTpl(cur.config['相片文件名'] || '', row, ts)) || 'photo';
    const subdir = (cur.config['目录'] || '').split('/').map(sanitizeSeg).filter(Boolean).join('/');
    const fd = new FormData();
    fd.append('file', blob, 'photo.jpg');
    fd.append('filename', filename);
    fd.append('subdir', subdir);
    const r = await api(`/api/workbooks/${cur.id}/photos`, { method: 'POST', body: fd });
    toast(`已上传：${r.path}`);
  } catch (err) { toast('拍照上传失败：' + err.message, true); }
});

/* ── 相册 ── */
$('#btnAlbum').addEventListener('click', openAlbum);
$('#albumClose').addEventListener('click', () => $('#albumMask').classList.add('hidden'));
$('#albumMask').addEventListener('click', (e) => { if (e.target === $('#albumMask')) $('#albumMask').classList.add('hidden'); });

async function openAlbum() {
  const mask = $('#albumMask');
  mask.classList.remove('hidden');
  $('#albumZip').href = `/api/workbooks/${cur.id}/photos.zip`;
  $('#albumGrid').innerHTML = '<span class="muted">加载中…</span>';
  try {
    const data = await api(`/api/workbooks/${cur.id}/photos`);
    $('#albumMeta').textContent = `${data.photos.length} 张`;
    const g = $('#albumGrid');
    g.innerHTML = '';
    if (!data.photos.length) { g.innerHTML = '<span class="muted">暂无相片，点「📷 拍照」开始。</span>'; return; }
    for (const p of data.photos) {
      const item = el(`<figure class="album-item" title="${p.path}（${(p.size / 1024).toFixed(0)} KB · ${p.mtime}）">
        <img loading="lazy" src="/api/workbooks/${cur.id}/photos/file/${encodeURIComponent(p.path)}" alt="${p.path}">
        <figcaption>${p.path.split('/').pop()}</figcaption></figure>`);
      item.querySelector('img').addEventListener('click', () =>
        window.open(`/api/workbooks/${cur.id}/photos/file/${encodeURIComponent(p.path)}`, '_blank'));
      g.appendChild(item);
    }
  } catch (err) { $('#albumGrid').innerHTML = `<span class="err">${err.message}</span>`; }
}

/* ── 轨迹（B3）：watchPosition 采集 → GPX 生成 → 上传后台 ── */
let trackWatch = null;    // watchPosition id
let trackPts = [];        // [{lat, lng, ele, t: ISO}]

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
  const row = allRows[selRow];
  const cls = row ? sanitizeSeg(String(row[cur.headers.indexOf('小班号')] || '').trim() || '无小班') : '无小班';
  const name = `轨迹_${cls}_${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '')}.gpx`;
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
