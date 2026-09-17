/* hqz-supervision 管理后台：模板管理 / 下载 Excel / 下载轨迹 */
'use strict';
const $ = (s) => document.querySelector(s);

function toast(msg, isErr) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.toggle('error', !!isErr);
  t.classList.remove('hidden');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add('hidden'), 2600);
}

function getToken() { try { return localStorage.getItem('hqz_sup_token') || ''; } catch (e) { return ''; } }
function withToken(url) {
  const t = getToken();
  return t ? url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(t) : url;
}

/* v0.22：云同步 / 用户管理 两张卡片被临时注释掉后，对应 DOM 不存在；
   用 on() 包一层，元素缺失就跳过，避免整个后台脚本在空元素上报错。 */
function on(sel, ev, fn) {
  const el = $(sel);
  if (el) el.addEventListener(ev, fn);
}

async function api(url, opt) {
  const tok = getToken();
  if (tok) { opt = opt || {}; opt.headers = Object.assign({}, opt.headers || {}, { 'X-Sup-Token': tok }); }
  const r = await fetch((window.SUP_BASE || '') + url, opt);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}

async function loadWorkbooks() {
  const data = await api('/admin/api/workbooks');
  const showOff = !!($('#chkShowOff') && $('#chkShowOff').checked);
  const list = data.workbooks.filter((w) => showOff || w.is_active);
  const byId = new Map(list.map((w) => [w.id, w]));
  const tb = $('#tblWb tbody');
  tb.innerHTML = '';

  if (!list.length) {
    tb.innerHTML = `<tr><td colspan="5" class="muted">${
      showOff ? '暂无模板' : '暂无在用模板（勾选「显示已下架」可查看已下架的）'}</td></tr>`;
    return;
  }

  for (const w of list) {
    const param = w.editable_cols.length
      ? `<b>可编辑列：</b>${w.editable_cols.join('、')}`
        + (w.unique_key ? `<br><b>唯一键：</b>${escape(w.unique_key)}` : '')
        + (w.hidden_cols.length ? `<br><b>隐藏列：</b>${w.hidden_cols.join('、')}` : '')
        + (w.result_options.length ? `<br><b>验收结果：</b>${w.result_options.join(' / ')}` : '')
        + (w.features.length ? `<br><b>功能：</b>${w.features.join('、')}` : '')
      : '<span class="muted">参数表未配置可编辑列</span>';

    const actions = w.is_active
      ? `<button class="btn" data-off="${w.id}">下架</button>`
      : `<button class="btn primary" data-on="${w.id}">恢复</button>
         <button class="btn danger" data-purge="${w.id}">彻底删除</button>`;

    const tr = document.createElement('tr');
    if (!w.is_active) tr.className = 'row-off';
    tr.innerHTML = `<td>${escape(w.name)}${w.is_active ? '' : ' <span class="muted">（已下架）</span>'}</td>`
      + `<td>${escape(w.sheet_name)}</td><td class="param-cell">${param}</td>`
      + `<td>${escape(w.uploaded_at)}</td>`
      + `<td><button class="btn" data-dl="${w.id}">下载 Excel</button>
          <button class="btn" data-upd="${w.id}">更新 Excel</button>
          ${actions}</td>`;
    tb.appendChild(tr);
  }

  // 下载 Excel（v0.25）：先弹筛选框（字段来自参数「导出筛选」，没有的字段不显示）
  tb.querySelectorAll('[data-dl]').forEach((b) => b.addEventListener('click', () => {
    const w = byId.get(Number(b.dataset.dl)) || {};
    openFilterDialog(Number(b.dataset.dl), w.name || '').catch((e) => toast(e.message, true));
  }));

  // 更新 Excel（v0.26）：按唯一键把新 Excel 合并进当前工作簿
  tb.querySelectorAll('[data-upd]').forEach((b) => b.addEventListener('click', () => {
    const w = byId.get(Number(b.dataset.upd)) || {};
    openUpdateDialog(Number(b.dataset.upd), w.name || '', !!w.can_rollback);
  }));

  // 下架（软删除，C13）：名称确认 → App 端不再显示，数据与源模板全部保留
  tb.querySelectorAll('[data-off]').forEach((b) => b.addEventListener('click', async () => {
    const w = byId.get(Number(b.dataset.off)) || {};
    const yes = await confirmByName(
      '下架模板',
      '下架后 App 端不再显示该模板，普通用户看不到也填不了。'
      + '数据行、源 Excel、变更日志、照片全部保留，随时可以「恢复」。',
      w.name || '', '确认下架');
    if (!yes) return;
    await api(`/api/workbooks/${b.dataset.off}`, { method: 'DELETE' });
    toast('已下架：App 端不再显示');
    await loadWorkbooks();
  }));

  // 恢复：撤销下架
  tb.querySelectorAll('[data-on]').forEach((b) => b.addEventListener('click', async () => {
    await api(`/api/workbooks/${b.dataset.on}/restore`, { method: 'POST' });
    toast('已恢复：App 端重新可见');
    await loadWorkbooks();
  }));

  // 彻底删除：不可恢复，同样要求输入名称
  tb.querySelectorAll('[data-purge]').forEach((b) => b.addEventListener('click', async () => {
    const w = byId.get(Number(b.dataset.purge)) || {};
    const yes = await confirmByName(
      '彻底删除模板',
      '此操作不可恢复：将永久删除该模板的数据行、源 Excel、变更日志与照片同步记录。',
      w.name || '', '永久删除');
    if (!yes) return;
    await api(`/api/workbooks/${b.dataset.purge}?purge=1`, { method: 'DELETE' });
    toast('已彻底删除');
    await loadWorkbooks();
  }));
}

if ($('#chkShowOff')) $('#chkShowOff').addEventListener('change', () => loadWorkbooks().catch((e) => toast(e.message, true)));

async function loadTracks() {
  const tb = $('#tblTrack tbody');
  if (!tb) return;              // v0.27：轨迹卡片被注释掉时静默跳过，别报错
  const data = await api('/admin/api/tracks');
  tb.innerHTML = '';
  if (!data.tracks.length) {
    tb.innerHTML = '<tr><td colspan="3" class="muted">暂无轨迹文件</td></tr>';
    return;
  }
  for (const t of data.tracks) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${t.file}</td><td>${(t.size / 1024).toFixed(1)} KB</td>
      <td><a class="btn" href="${withToken((window.SUP_BASE || '') + '/admin/api/tracks/' + encodeURIComponent(t.file))}">下载</a></td>`;
    tb.appendChild(tr);
  }
}

async function loadLogs() {
  const data = await api('/admin/api/accept-logs');
  const tb = $('#tblLogs tbody');
  tb.innerHTML = '';
  if (!data.logs.length) {
    tb.innerHTML = '<tr><td colspan="7" class="muted">暂无验收变更记录</td></tr>';
    return;
  }
  for (const l of data.logs) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${l.created_at}</td><td>${l.wb_name || l.workbook_id}</td><td>${l.xiaoban || '-'}</td>
      <td>${l.field}</td><td>${escape(l.old_value || '')}</td><td>${escape(l.new_value || '')}</td><td>${l.operator}</td>`;
    tb.appendChild(tr);
  }
}

function escape(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');   // 引号也转义，才能安全用于属性
}

/* ── 危险操作确认（v0.24，C13）：要求照着输入名称才放行 ──────────────
   比一键 confirm 稳：每次都要逐字输入，不会因为"用顺手了"而失效。
   用自建 DOM 弹层而非 window.prompt —— Android WebView 默认不实现 onJsPrompt，
   在手机上 window.prompt 会直接返回 null（=永远确认不了）。 */
function confirmByName(title, hint, name, okText) {
  return new Promise((resolve) => {
    const mask = $('#dangerModal');
    $('#dangerTitle').textContent = title;
    $('#dangerHint').textContent = hint;
    $('#dangerName').textContent = name;
    $('#dangerOk').textContent = okText || '确认';
    const inp = $('#dangerInput');
    inp.value = '';
    $('#dangerErr').textContent = '';
    mask.classList.remove('hidden');
    inp.focus();

    const onOk = () => {
      if (inp.value.trim() !== name) {
        $('#dangerErr').textContent = '名称不一致，请逐字输入（区分大小写）';
        inp.focus();
        return;
      }
      done(true);
    };
    const onCancel = () => done(false);
    const onKey = (e) => {
      if (e.key === 'Enter') { e.preventDefault(); onOk(); }
      if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
    };
    function done(v) {
      mask.classList.add('hidden');
      $('#dangerOk').removeEventListener('click', onOk);
      $('#dangerCancel').removeEventListener('click', onCancel);
      inp.removeEventListener('keydown', onKey);
      resolve(v);
    }
    $('#dangerOk').addEventListener('click', onOk);
    $('#dangerCancel').addEventListener('click', onCancel);
    inp.addEventListener('keydown', onKey);
  });
}

/* ── 导出筛选弹框（v0.25）────────────────────────────────────────────
   字段与控件类型来自参数 sheet 的「导出筛选」（形如 标段|select;验收日期|date）。
   后端只返回该工作簿**确实存在**的字段 → 没有的字段不会出现在这里。
   只筛行：把选中的条件作为查询参数交给后台下载接口；全留空＝下载全部。 */
async function openFilterDialog(wid, name) {
  const mask = $('#filterModal');
  const box = $('#filterFields');
  $('#filterTitle').textContent = name ? `下载 Excel · ${name}` : '下载 Excel';
  $('#filterErr').textContent = '';
  box.innerHTML = '<span class="muted">加载筛选项…</span>';
  mask.classList.remove('hidden');

  let fields = [];
  try {
    fields = (await api(`/admin/api/workbooks/${wid}/filter-options`)).fields || [];
  } catch (e) {
    box.innerHTML = '';
    $('#filterErr').textContent = '筛选项加载失败：' + e.message;
    return;
  }

  if (!fields.length) {
    box.innerHTML = '<span class="muted">该模板未声明「导出筛选」，可直接下载全部。</span>';
  } else {
    box.innerHTML = fields.map((f) => {
      const fid = `flt_${wid}_${f.field}`;
      const label = escape(f.field);
      if (f.type === 'date') {
        return `<label for="${fid}">${label}（日期，等于）`
             + `<input type="date" id="${fid}" data-field="${label}"></label>`;
      }
      const opts = ['<option value="">全部</option>']
        .concat((f.options || []).map((o) => `<option value="${escape(o)}">${escape(o)}</option>`))
        .join('');
      return `<label for="${fid}">${label}（下拉单选，${(f.options || []).length} 个可选）`
           + `<select id="${fid}" data-field="${label}">${opts}</select></label>`;
    }).join('');
  }

  const go = (withFilter) => {
    const params = {};
    if (withFilter) {
      box.querySelectorAll('[data-field]').forEach((el) => {
        const v = (el.value || '').trim();
        if (v) params[el.dataset.field] = v;      // 空值不带参数 = 该条件不筛
      });
    }
    mask.classList.add('hidden');
    // 与原有下载同链路（导航式下载，浏览器按 Content-Disposition 落盘，页面不跳走）
    location.href = withToken((window.SUP_BASE || '') + `/admin/api/workbooks/${wid}/download`
                              + (Object.keys(params).length ? '?' + new URLSearchParams(params) : ''));
    toast(withFilter && Object.keys(params).length ? '开始导出（已按条件筛选）' : '开始导出（全部）');
  };
  $('#filterOk').onclick = () => go(true);
  $('#filterAll').onclick = () => go(false);
  $('#filterCancel').onclick = () => mask.classList.add('hidden');
}

/* ── 更新 Excel（v0.26）──────────────────────────────────────────────
   场景：工作簿填到一半，此时要改模板（加「导出筛选」、改「目录」、加列、修正原始数据）。
   原做法只能重新上传 → 会**新建**一个工作簿，已填数据成孤儿、变更日志与下架状态断链。
   这里按唯一键合并进**当前工作簿**（id 不变）。
   两步走：先「预览变更」看统计，再输入工作簿名称确认才写库（C13 的确认规矩）。
   写库前后端自动备份，弹框里可一键回滚。 */
function updStatsHtml(s) {
  const rows = [
    ['匹配并按规则合并', `${s.matched} 行`, '人工填过的列保留原值；其余列以新 Excel 为准'],
    ['新 Excel 新增', `${s.added} 行`, '追加到数据末尾'],
    ['仅当前数据有（新表没有）', `${s.kept_old_only} 行`, '保留，不删'],
    ['被保住的冲突格子', `${s.conflicts} 个`, '新 Excel 同格有不同值，但旧值是人工填的 → 按规则保留旧值'],
    ['数据行数', `${s.old_row_count} → ${s.result_row_count}`, `新 Excel 有 ${s.new_row_count} 行`],
    ['列数', `${s.old_col_count} → ${s.new_col_count}`, '按列名对齐，列可增删、可换序'],
  ];
  if ((s.new_only_cols || []).length) {
    rows.push(['新增的列', s.new_only_cols.join('、'), '老行这些列取新 Excel 的值']);
  }
  if ((s.old_only_cols || []).length) {
    rows.push(['被删掉的列', s.old_only_cols.join('、'), `这些列的数据会丢（其中有值格 ${s.dropped_nonempty} 个）`]);
  }
  rows.push(['受保护的列（人工填写优先）',
             (s.preserve_cols || []).join('、') || '—', '两边「可编辑列」的并集']);

  const warn = [];
  if (s.matched === 0) warn.push('⚠️ 没有任何唯一键匹配上 —— 确认上传的是同一批数据吗？');
  if (s.dup_new_key_count) {
    warn.push(`⚠️ 新 Excel 的唯一键有 ${s.dup_new_key_count} 个重复（${(s.dup_new_keys || []).join('、')}…）：`
              + '这些行无法判断更新哪一行，会当作新行追加');
  }
  if (s.blanked_nonempty) {
    warn.push(`⚠️ 有 ${s.blanked_nonempty} 个格子在当前工作簿里有值、但新 Excel 里是空的 —— `
              + '这些格子会被清空（非可编辑列以新 Excel 为准；如果只是漏填，请先补好再上传）');
  }
  if (s.dropped_nonempty) warn.push(`⚠️ 新 Excel 删了列，共 ${s.dropped_nonempty} 个有值单元格会丢失`);

  return '<table class="tbl stat">'
    + rows.map(([k, v, h]) =>
        `<tr><th>${escape(k)}</th><td><b>${escape(String(v))}</b>`
        + `<div class="muted">${escape(h)}</div></td></tr>`).join('')
    + '</table>'
    + warn.map((w) => `<div class="err">${escape(w)}</div>`).join('');
}

function openUpdateDialog(wid, name, canRollback) {
  const mask = $('#updateModal');
  const prev = $('#updPreview');
  const input = $('#updInput');
  $('#updFile').value = '';
  prev.innerHTML = '';
  input.value = '';
  $('#updErr').textContent = '';
  $('#updTitle').textContent = name ? `更新 Excel · ${name}` : '更新 Excel';
  $('#updRun').disabled = true;
  $('#updRollback').classList.toggle('hidden', !canRollback);
  mask.classList.remove('hidden');

  async function send(dry) {
    const f = $('#updFile').files && $('#updFile').files[0];
    if (!f) { $('#updErr').textContent = '请先选择要上传的 Excel 文件'; return null; }
    const fd = new FormData();
    fd.append('file', f);
    if (!dry) fd.append('confirm', input.value.trim());
    return api(`/api/workbooks/${wid}/update${dry ? '?dry=1' : ''}`, { method: 'POST', body: fd });
  }

  $('#updPreviewBtn').onclick = async () => {
    $('#updErr').textContent = '';
    $('#updRun').disabled = true;
    prev.innerHTML = '<span class="muted">解析中…</span>';
    try {
      const r = await send(true);
      if (!r) { prev.innerHTML = ''; return; }
      prev.innerHTML = updStatsHtml(r.preview);
      $('#updRun').disabled = false;
    } catch (e) {
      prev.innerHTML = '';
      $('#updErr').textContent = e.message;
    }
  };

  $('#updRun').onclick = async () => {
    $('#updErr').textContent = '';
    if (!input.value.trim()) { $('#updErr').textContent = '请输入当前工作簿名称以确认'; return; }
    try {
      const r = await send(false);
      if (!r) return;
      const s = r.preview;
      mask.classList.add('hidden');
      toast(`已更新：共 ${s.result_row_count} 行（匹配 ${s.matched} / 新增 ${s.added} / 保留 ${s.kept_old_only}）`);
      await loadWorkbooks();
    } catch (e) {
      $('#updErr').textContent = e.message;
    }
  };

  $('#updRollback').onclick = async () => {
    $('#updErr').textContent = '';
    if (!input.value.trim()) { $('#updErr').textContent = '回滚同样要输入当前工作簿名称以确认'; return; }
    try {
      const r = await api(`/api/workbooks/${wid}/rollback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: input.value.trim() }),
      });
      mask.classList.add('hidden');
      toast(`已回滚到更新前（${r.restored.rows} 行）`);
      await loadWorkbooks();
    } catch (e) {
      $('#updErr').textContent = e.message;
    }
  };

  $('#updCancel').onclick = () => mask.classList.add('hidden');
}

$('#adminFile').addEventListener('change', async (e) => {
  const files = [...e.target.files];
  e.target.value = '';
  for (const f of files) {
    const fd = new FormData();
    fd.append('file', f);
    try {
      const r = await api('/api/workbooks', { method: 'POST', body: fd });
      toast(`已上传「${f.name}」：${r.rows} 行`);
      $('#adminMsg').textContent = '';
    } catch (err) {
      $('#adminMsg').textContent = `「${f.name}」上传失败：${err.message}`;
    }
  }
  await loadWorkbooks();
});

/* v0.27：轨迹卡片被注释掉后 #btnZip 不存在 —— 必须用 on() 包一层，
   否则顶层 addEventListener 会在 null 上报错、整个后台脚本加载失败（整页失效）。 */
on('#btnZip', 'click', () => { location.href = withToken((window.SUP_BASE || '') + '/admin/api/tracks.zip'); });

/* ── 相片云同步（doc/007） ── */
function fmtTokenExp(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const days = Math.round((ts * 1000 - Date.now()) / 86400000);
  return `${d.toLocaleDateString('sv-SE')}（剩 ${days} 天）`;
}

async function loadSyncSettings() {
  if (!$('#syncQiniuAk')) return;          // 卡片被注释掉 → 直接返回
  const data = await api('/admin/api/sync/settings');
  const s = data.settings;
  $('#syncEnabled').checked = s.sync_enabled === '1';
  $('#syncQiniuAk').value = s.sync_qiniu_ak || '';
  $('#syncQiniuBucket').value = s.sync_qiniu_bucket || '';
  $('#syncBaiduAppKey').value = s.sync_baidu_app_key || '';
  $('#syncBaiduAppDir').value = s.sync_baidu_app_dir || '';
  $('#syncBaiduPrefix').value = s.sync_baidu_prefix || '';
  $('#syncKeepDays').value = s.sync_keep_days != null ? s.sync_keep_days : '30';
  $('#syncReady').textContent = data.ready
    ? '✓ 配置齐全'
    : '⚠ 还缺：' + ((data.missing_labels || data.missing || []).join('、'));
  $('#pMaxSide').value = s.photo_max_side ?? '1440';
  $('#pQuality').value = s.photo_quality ?? '0.8';
  $('#vPhoneRec').value = s.video_phone_rec ?? '1';
  $('#vRecMode').value = s.video_rec_mode ?? 'system';
  $('#vTranscode').value = s.video_transcode ?? '1';
  $('#vCamQ').value = s.video_cam_quality ?? '1';
  $('#vFps').value = s.video_fps ?? '30';
  $('#vMaxSec').value = s.video_max_seconds ?? '60';
  $('#vMaxHeight').value = s.video_max_height ?? '720';
  $('#vCrf').value = s.video_crf ?? '28';
  $('#vMaxrate').value = s.video_maxrate_k ?? '2500';
  $('#vAudio').value = s.video_audio_k ?? '96';
  $('#vMaxMb').value = s.video_max_mb ?? '300';
  $('#vFfmpeg').value = s.video_ffmpeg || '';
  // v0.29.2：拼接后折叠重复斜杠 —— 「网盘应用目录」若填成 /apps/x/，
  // 原来的 `${app_dir}/${prefix}/…` 会显示成 /apps/x//supervision/…（用户实测反馈）
  const joinPath = (...ps) => ps.join('/').replace(/\/{2,}/g, '/');
  $('#syncPath').textContent = joinPath(
    s.sync_baidu_app_dir || '', s.sync_baidu_prefix || '', '{参数目录}', '{文件名}.jpg');
}

async function loadSyncStatus() {
  if (!$('#syncCounts')) return;           // 卡片被注释掉 → 直接返回
  const data = await api('/admin/api/sync/status');
  const c = data.counts || {};
  const order = ['received', 'qiniu_ok', 'baidu_ok'];
  $('#syncCounts').innerHTML = order.map((k) =>
    `<span class="cnt cnt-${k}">${({received: '接收', qiniu_ok: '已暂存', baidu_ok: '已同步百度'})[k]} <b>${c[k] || 0}</b></span>`
  ).join('') + (data.token_expires_at ? `<span class="cnt muted">token 有效期至 ${fmtTokenExp(data.token_expires_at)}</span>` : '')
    + (data.purged ? `<span class="cnt muted">本次清理七牛副本 ${data.purged} 个</span>` : '');
  const tb = $('#tblSync tbody');
  tb.innerHTML = '';
  if (!data.recent.length) {
    tb.innerHTML = '<tr><td colspan="10" class="muted">暂无同步记录（开启同步后拍照/视频即产生）</td></tr>';
    return;
  }
  const kb = (n) => (!n ? '-' : n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.round(n / 1024) + ' KB');
  for (const p of data.recent) {
    const st = ({received: '接收', qiniu_ok: '已暂存', baidu_ok: '✓已同步'})[p.state] || p.state;
    const t = p.state === 'baidu_ok' ? p.synced_at : p.created_at;
    const isV = p.kind === 'video';
    const size = isV && p.orig_size
      ? `${kb(p.size)}<br><span class="muted">原始 ${kb(p.orig_size)}</span>` : kb(p.size);
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${p.id}</td>
      <td><span class="k-tag${isV ? ' v' : ''}">${isV ? '视频' : '照片'}</span></td>
      <td>${escape(p.wb_name || p.workbook_id)}</td>
      <td>${escape(p.xiaoban || '-')}</td><td>${escape(p.filename)}</td>
      <td class="nowrap">${size}</td>
      <td class="st-${p.state}">${st}</td><td>${p.retry_count}</td><td>${escape(t || '')}</td>
      <td class="err-cell">${escape((p.last_error || '').slice(0, 120))}</td>`;
    tb.appendChild(tr);
  }
}

/* 统一的设置保存（v0.16：图片压缩 / 视频压缩 / 云同步 三块各自保存） */
async function saveSettings(settings, msgEl) {
  if (msgEl) msgEl.textContent = '';
  try {
    await api('/admin/api/sync/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings }),
    });
    toast('已保存');
    await loadSyncSettings();
  } catch (err) {
    if (msgEl) msgEl.textContent = err.message; else toast(err.message, true);
  }
}

$('#btnPhotoSave').addEventListener('click', () => saveSettings({
  photo_max_side: $('#pMaxSide').value,
  photo_quality: $('#pQuality').value,
}, $('#photoMsg')));

$('#btnVideoSave').addEventListener('click', () => saveSettings({
  video_rec_mode: $('#vRecMode').value,
  video_cam_quality: $('#vCamQ').value,
  video_transcode: $('#vTranscode').value,
  video_phone_rec: $('#vPhoneRec').value,
  video_max_height: $('#vMaxHeight').value,
  video_maxrate_k: $('#vMaxrate').value,
  video_fps: $('#vFps').value,
  video_max_seconds: $('#vMaxSec').value,
  video_max_mb: $('#vMaxMb').value,
  video_crf: $('#vCrf').value,
  video_audio_k: $('#vAudio').value,
  video_ffmpeg: $('#vFfmpeg').value,
}, $('#videoMsg')));

on('#btnSyncSave', 'click', async () => {
  const settings = {
    sync_enabled: $('#syncEnabled').checked ? '1' : '0',
    sync_qiniu_ak: $('#syncQiniuAk').value,
    sync_qiniu_sk: $('#syncQiniuSk').value,
    sync_qiniu_bucket: $('#syncQiniuBucket').value,
    sync_baidu_app_key: $('#syncBaiduAppKey').value,
    sync_baidu_secret_key: $('#syncBaiduSecretKey').value,
    sync_baidu_app_dir: $('#syncBaiduAppDir').value,
    sync_baidu_prefix: $('#syncBaiduPrefix').value,
    sync_keep_days: $('#syncKeepDays').value,
  };
  const tok = $('#syncBaiduToken').value.trim();
  if (tok) settings.sync_baidu_token = tok;
  $('#syncMsg').textContent = '';
  try {
    await api('/admin/api/sync/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings }),
    });
    $('#syncBaiduToken').value = '';
    $('#syncQiniuSk').value = '';
    $('#syncBaiduSecretKey').value = '';
    toast('同步设置已保存');
    await loadSyncSettings();
    await loadSyncStatus().catch(() => {});
  } catch (err) {
    $('#syncMsg').textContent = err.message;
  }
});

on('#btnSyncRetry', 'click', async () => {
  try {
    const r = await api('/admin/api/sync/retry', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    toast(`重推完成：成功 ${r.ok}，失败 ${r.fail}`);
    await loadSyncStatus();
  } catch (err) { toast(err.message, true); }
});

/* ── 用户管理（v0.21）：管理员重置他人密码 ── */
async function loadUsers() {
  if (!$('#tblUsers')) return;             // 卡片被注释掉 → 直接返回
  const data = await api('/admin/api/users');
  const tb = $('#tblUsers tbody');
  tb.innerHTML = '';
  for (const u of data.users) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${escape(u.username)}</td>
      <td>${u.role === 'admin' ? '管理员' : '普通用户'}</td>
      <td><input type="text" placeholder="输入新密码" data-u="${escape(u.username)}" style="width:100%;padding:6px 8px;border:1px solid #d4d9de;border-radius:6px;"></td>
      <td><button class="btn" data-reset="${escape(u.username)}">重置</button></td>`;
    tb.appendChild(tr);
  }
  tb.querySelectorAll('button[data-reset]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const name = btn.getAttribute('data-reset');
      const input = tb.querySelector(`input[data-u="${CSS.escape(name)}"]`);
      const pwd = (input.value || '').trim();
      $('#usersMsg').textContent = '';
      if (pwd.length < 4) { $('#usersMsg').textContent = '新密码至少 4 位'; return; }
      if (!confirm(`确定把「${name}」的密码重置为「${pwd}」？该用户需用新密码重新登录。`)) return;
      try {
        await api(`/admin/api/users/${encodeURIComponent(name)}/password`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ new_password: pwd }),
        });
        input.value = '';
        toast(`${name} 的密码已重置`);
      } catch (err) { $('#usersMsg').textContent = err.message; }
    });
  });
}

on('#btnSyncRefresh', 'click', () => loadSyncStatus().catch((e) => toast(e.message, true)));

$('#btnLogout').addEventListener('click', async () => {
  await api('/api/logout', { method: 'POST' }).catch(() => {});
  // 同 app.js：留在本应用内，不要跳网关首页 '/'（会掉进别的应用）
  location.href = (window.SUP_BASE || '') + '/';
});

loadWorkbooks().catch((e) => toast(e.message, true));
loadTracks().catch(() => {});
loadLogs().catch(() => {});
if ($('#syncQiniuAk')) loadSyncSettings().catch(() => {});     // 云同步卡片被注释则跳过
if ($('#tblUsers')) loadUsers().catch(() => {});               // 用户管理卡片被注释则跳过
if ($('#syncCounts')) loadSyncStatus().catch(() => {});
