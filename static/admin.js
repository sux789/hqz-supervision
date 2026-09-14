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
      + `<td><a class="btn" href="${withToken((window.SUP_BASE || '') + `/admin/api/workbooks/${w.id}/download`)}">下载 Excel</a>
          ${actions}</td>`;
    tb.appendChild(tr);
  }

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
  const data = await api('/admin/api/tracks');
  const tb = $('#tblTrack tbody');
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

$('#btnZip').addEventListener('click', () => { location.href = withToken((window.SUP_BASE || '') + '/admin/api/tracks.zip'); });

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
    ? '✓ 配置齐全' : '⚠ 缺配置：' + data.missing.join('、');
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
  $('#syncPath').textContent = `${s.sync_baidu_app_dir}/${s.sync_baidu_prefix}/{参数目录}/{文件名}.jpg`;
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
