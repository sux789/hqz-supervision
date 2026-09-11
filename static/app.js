/* hqz-supervision 前端：登录 / 工作簿列表 / 小班列表 / 两列表单（自动保存）+ 拍照轨迹 */
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

/* ── Hash 路由：#/ 工作簿列表 · #/wb/<id> 小班列表 · #/wb/<id>/r/<idx> 小班详情 ── */
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
    show('rows');
    renderRowsList();
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
  $('#viewRows').classList.toggle('hidden', view !== 'rows');
  $('#viewDetail').classList.toggle('hidden', view !== 'detail');
  $('#btnBack').classList.toggle('hidden', view === 'list');
  $('#pageName').textContent =
    view === 'detail' ? (cur ? `${rowVal('小班号') || '详情'} · ${cur.name}` : '') :
    view === 'rows' ? (cur ? cur.name : '') : '工作簿';
  $('#btnBack').onclick = () => nav(view === 'detail' ? `#/wb/${cur.id}` : '#/');
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
        <span class="btn primary">打开</span>
      </div>`);
    n.addEventListener('click', () => nav(`#/wb/${w.id}`));
    box.appendChild(n);
  }
}

/* ── ② 小班列表：本地搜索 + 卡片 ── */
function renderRowsList() {
  const inp = $('#rowSearchInput');
  if (!inp._bound) {
    let h; inp.addEventListener('input', () => { clearTimeout(h); h = setTimeout(renderRowCards, 200); });
    inp._bound = true;
  }
  inp.value = '';
  renderRowCards();
}

function renderRowCards() {
  const q = ($('#rowSearchInput').value || '').trim().toLowerCase();
  const box = $('#rowList');
  box.innerHTML = '';
  let shown = 0;
  for (let i = 0; i < allRows.length && shown < 200; i++) {
    const r = allRows[i];
    if (q && !r.some((v) => String(v || '').toLowerCase().includes(q))) continue;
    shown++;
    const xhI = cur.headers.indexOf('小班号');
    const card = el(`
      <div class="wb-item" data-idx="${i}">
        <span class="wb-name">${escapeHtml(String(xhI >= 0 ? (r[xhI] ?? '') : (r[0] ?? ''))) || '(未编号)'}</span>
        <span class="muted">${escapeHtml(r.slice(0, 4).map(String).join(' / '))}</span>
        <span class="spacer"></span>
        <span class="btn primary">编辑</span>
      </div>`);
    card.addEventListener('click', () => nav(`#/wb/${cur.id}/r/${i}`));
    box.appendChild(card);
  }
  $('#rowMeta').textContent = q
    ? `匹配 ${countMatch(q)} / ${allRows.length} 行（显示前 ${shown}）`
    : `共 ${allRows.length} 个小班${allRows.length > 200 ? '（显示前 200，请搜索缩小范围）' : ''}`;
}

function countMatch(q) {
  let n = 0;
  for (const r of allRows) if (r.some((v) => String(v || '').toLowerCase().includes(q))) n++;
  return n;
}

/* ── ③ 小班详情：两列表单（标题左/值右，onchange 自动保存，无保存按钮） ── */
function cfgList(key) {
  return (cur.config[key] || '').split(';').map((s) => s.trim()).filter(Boolean);
}

function openDetail(idx) {
  if (!allRows[idx]) { nav(`#/wb/${cur.id}`); return; }
  curIdx = idx;
  show('detail');
  renderForm();
  renderRowPhotos();
  rememberLast();
  const feats = cfgList('功能');
  $('#btnPhoto').classList.toggle('hidden', !feats.includes('拍照'));
  $('#btnAlbum').classList.toggle('hidden', !feats.includes('拍照'));
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
  const optKey = Object.keys(cur.config).find((k) => k.endsWith('选项'));
  const optCol = optKey ? optKey.slice(0, -2) : '';
  const options = optCol ? cfgList(optKey) : [];

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
      el0.querySelectorAll('tbody tr').forEach((tr, y) => {
        if (!editable.has(cur.headers[y])) {
          const td = tr.querySelectorAll('td')[1];
          if (td) td.classList.add('locked');
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
  $('#photoHint').textContent = '📁 相片保存目录：' + (rowSubdir() || '(参数未配置目录)');
}

/* 下拉字段（验收结果选项 → 验收结果）：点击值格弹原生 select，change 即触发自动保存链 */
document.addEventListener('click', (e) => {
  if (!formGrid || !cur || curIdx < 0) return;
  const optKey = Object.keys(cur.config).find((k) => k.endsWith('选项'));
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
    scheduleRowSave();
  });
  sel.addEventListener('blur', () => { cell.textContent = allRows[curIdx][oy] || ''; });
});

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

/* 模板渲染：{{列名}}→行值；{{sheet名称}}→当前sheet；{{时间}}→YYYYMMDD_HHMMSS（C02 保留字） */
function renderTpl(tpl, row, now) {
  return (tpl || '').replace(/\{\{(.+?)\}\}/g, (_, key) => {
    key = key.trim();
    if (key === 'sheet名称') return cur.sheet_name;
    if (key === '时间') return now;
    const i = cur.headers.indexOf(key);
    return i >= 0 ? String(row[i] == null ? '' : row[i]).trim() : '';
  });
}

/* 该小班相片墙：按参数目录过滤 */
async function renderRowPhotos() {
  const g = $('#rowPhotos');
  g.innerHTML = '<span class="muted">相片加载中…</span>';
  try {
    const data = await api(`/api/workbooks/${cur.id}/photos`);
    const prefix = rowSubdir();
    const mine = data.photos.filter((p) => !prefix || p.path.startsWith(prefix + '/'));
    g.innerHTML = '';
    if (!mine.length) { g.innerHTML = '<span class="muted">该小班暂无相片，点「📷 拍照」开始。</span>'; return; }
    for (const p of mine) {
      const item = el(`<figure class="album-item" title="${escapeHtml(p.path)}（${(p.size / 1024).toFixed(0)} KB · ${p.mtime}）">
        <img loading="lazy" src="${window.SUP_BASE || ''}/api/workbooks/${cur.id}/photos/file/${encodeURIComponent(p.path)}" alt="${escapeHtml(p.path)}">
        <figcaption>${escapeHtml(p.path.split('/').pop())}</figcaption></figure>`);
      item.querySelector('img').addEventListener('click', () =>
        window.open(`${window.SUP_BASE || ''}/api/workbooks/${cur.id}/photos/file/${encodeURIComponent(p.path)}`, '_blank'));
      g.appendChild(item);
    }
  } catch (e) { g.innerHTML = `<span class="err">${e.message}</span>`; }
}

/* ── 相册弹层（全部相片 + zip） ── */
$('#btnAlbum').addEventListener('click', openAlbum);
$('#albumClose').addEventListener('click', () => $('#albumMask').classList.add('hidden'));
$('#albumMask').addEventListener('click', (e) => { if (e.target === $('#albumMask')) $('#albumMask').classList.add('hidden'); });

async function openAlbum() {
  const mask = $('#albumMask');
  mask.classList.remove('hidden');
  $('#albumZip').href = `${window.SUP_BASE || ''}/api/workbooks/${cur.id}/photos.zip`;
  $('#albumGrid').innerHTML = '<span class="muted">加载中…</span>';
  try {
    const data = await api(`/api/workbooks/${cur.id}/photos`);
    $('#albumMeta').textContent = `${data.photos.length} 张`;
    const g = $('#albumGrid');
    g.innerHTML = '';
    if (!data.photos.length) { g.innerHTML = '<span class="muted">暂无相片。</span>'; return; }
    for (const p of data.photos) {
      const item = el(`<figure class="album-item" title="${escapeHtml(p.path)}">
        <img loading="lazy" src="${window.SUP_BASE || ''}/api/workbooks/${cur.id}/photos/file/${encodeURIComponent(p.path)}" alt="${escapeHtml(p.path)}">
        <figcaption>${escapeHtml(p.path)}</figcaption></figure>`);
      item.querySelector('img').addEventListener('click', () =>
        window.open(`${window.SUP_BASE || ''}/api/workbooks/${cur.id}/photos/file/${encodeURIComponent(p.path)}`, '_blank'));
      g.appendChild(item);
    }
  } catch (err) { $('#albumGrid').innerHTML = `<span class="err">${err.message}</span>`; }
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

// 水印（黑字白边左下角 JPEG 0.92）；日期行固定输出（C06：水印日期不参数化）
async function drawWatermark(file, remark, coords) {
  let bmp;
  try { bmp = await createImageBitmap(file, { imageOrientation: 'from-image' }); }
  catch (e) { bmp = await createImageBitmap(file); }
  const canvas = document.createElement('canvas');
  canvas.width = bmp.width; canvas.height = bmp.height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(bmp, 0, 0);
  bmp.close && bmp.close();

  const lines = [`日期：${new Date().toLocaleDateString('sv-SE')}`];
  if (coords) lines.push(`坐标：${coords}`);
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
  if (curIdx < 0 || !allRows[curIdx]) { toast('请先选择小班', true); return; }
  $('#photoInput').click();
});

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
    const blob = await drawWatermark(file, remark, coords);
    const filename = sanitizeSeg(renderTpl(cur.config['相片文件名'] || '', row, ts)) || 'photo';
    const subdir = rowSubdir();
    const fd = new FormData();
    fd.append('file', blob, 'photo.jpg');
    fd.append('filename', filename);
    fd.append('subdir', subdir);
    const r = await api(`/api/workbooks/${cur.id}/photos`, { method: 'POST', body: fd });
    toast(`已上传：${r.path}`);
    renderRowPhotos();
  } catch (err) { toast('拍照上传失败：' + err.message, true); }
});

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
