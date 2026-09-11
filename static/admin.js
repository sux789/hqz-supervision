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

async function api(url, opt) {
  const r = await fetch((window.BASE || '') + url, opt);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}

async function loadWorkbooks() {
  const data = await api('/admin/api/workbooks');
  const tb = $('#tblWb tbody');
  tb.innerHTML = '';
  for (const w of data.workbooks) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${w.name}</td><td>${w.sheet_name}</td><td>${w.uploaded_at}</td>
      <td><a class="btn" href="${window.BASE || ''}/admin/api/workbooks/${w.id}/download">下载 Excel</a>
          <a class="btn" href="${window.BASE || ''}/api/workbooks/${w.id}/photos.zip">下载相片</a>
          <button class="btn danger" data-del="${w.id}">删除</button></td>`;
    tb.appendChild(tr);
  }
  tb.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
    if (!confirm('确认删除该模板？')) return;
    await api(`/api/workbooks/${b.dataset.del}`, { method: 'DELETE' });
    toast('已删除');
    await loadWorkbooks();
  }));
}

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
      <td><a class="btn" href="${window.BASE || ''}/admin/api/tracks/${encodeURIComponent(t.file)}">下载</a></td>`;
    tb.appendChild(tr);
  }
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

$('#btnZip').addEventListener('click', () => { location.href = (window.BASE || '') + '/admin/api/tracks.zip'; });

$('#btnLogout').addEventListener('click', async () => {
  await api('/api/logout', { method: 'POST' }).catch(() => {});
  location.href = '/';
});

loadWorkbooks().catch((e) => toast(e.message, true));
loadTracks().catch(() => {});
