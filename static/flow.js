/* 验收流程界面（v0.31，doc/019）
 *
 * 界面上每个输入框、下拉、按钮都不是写死的，而是由 Excel 推导：
 *   「填报控件」列 → 渲染什么控件、什么校验（parse_ctl 已在后端做完，这里拿结构）
 *   「附件要求」列 → 出哪些拍照/上传/打卡按钮、各几张下限
 *   「验收类型 / 填报要求 / 转序规则 / 通过标准」→ 卡片文案与提交闸门
 * 改 Excel 即生效，本文件不改（C01/C11 口径）。
 *
 * 三条纪律（踩过才知道）：
 *  1. **不重写拍照通道** —— 走 app.js 的 handlePhoto（同一套水印/压缩/目录/云同步，C10）
 *  2. **离场必冲刷**（C14/C17）：切后台/返回/关页前把当前环节的输入落到本地草稿，
 *     启动与切回前台自动补交；不依赖第三方编辑器派不派发事件
 *  3. **元素引用一律 on() 或空值守卫**（C16）：本模块可能整体不存在，
 *     顶层裸 addEventListener 抛 TypeError 会让整个脚本加载失败
 */
(function () {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const on = (el, ev, fn) => { if (el) el.addEventListener(ev, fn); };
  const DRAFT_KEY = 'hqz_sup_flow_draft_v1';

  const F = {
    wid: null,          // 工作簿 id
    rowIdx: -1,         // 当前行
    data: null,         // 后端回的 {flow_sheet, steps, std, records, warn}
    cur: 0,             // 当前环节下标
    val: {},            // 当前环节的输入值 {因子: 值}
    att: [],            // 当前环节的附件 [{kind,label,min,count,names:[]}]
    busy: false,
  };

  const esc = (s) => String(s == null ? '' : s)
    .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v) => (v === '' || v == null || isNaN(Number(v)) ? null : Number(v));
  const today = () => {
    const d = new Date(), p = (n) => String(n).padStart(2, '0');
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  };

  /* ── 附件留痕：与 app.js 的拍摄记录分开存，按环节归拢 ── */
  function attKey(kind, label) { return kind + '|' + label; }

  function loadDraft() {
    try {
      const raw = localStorage.getItem(DRAFT_KEY);
      if (!raw) return null;
      const d = JSON.parse(raw);
      if (Date.now() - (d.ts || 0) > 12 * 3600 * 1000) { localStorage.removeItem(DRAFT_KEY); return null; }
      return d;
    } catch (e) { return null; }
  }
  function saveDraft() {
    if (!F.data) return;
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({
        ts: Date.now(), wid: F.wid, rowIdx: F.rowIdx,
        cur: F.cur, val: F.val, att: F.att,
      }));
    } catch (e) { /* 配额满等一律不阻断填写 */ }
  }
  function clearDraft() { try { localStorage.removeItem(DRAFT_KEY); } catch (e) {} }

  /* ── 进入某行流程 ── */
  async function open(wid, rowIdx) {
    F.wid = wid; F.rowIdx = rowIdx; F.cur = 0; F.val = {}; F.att = [];
    let j;
    try { j = await api(`/api/workbooks/${wid}/flow?row=${rowIdx}`); }
    catch (e) { toast('读取验收流程失败：' + e.message, true); return false; }
    if (!j || j.ok === false) { F.data = null; return false; }
    F.data = j;
    // 回填已填记录
    const rec = (j.records || {})[String(j.steps[0].no)] || {};
    F.val = {};
    Object.keys(rec).forEach((k) => {
      if (!k) return;
      let v = rec[k].value;
      if (k === '确认项' || k === '管护作业情况') { try { v = JSON.parse(v); } catch (e) {} }
      F.val[k] = v;
    });
    F.att = (rec[''] && rec[''].att) || [];
    // 本地草稿（同 wid + 同行）优先，覆盖服务器值 —— 进程被杀也不丢（C17）
    const d = loadDraft();
    if (d && d.wid === wid && d.rowIdx === rowIdx) {
      F.cur = Math.min(d.cur || 0, j.steps.length - 1);
      F.val = Object.assign(F.val, d.val || {});
      if (d.att && d.att.length) F.att = d.att;
    } else {
      clearDraft();
    }
    if (F.cur !== 0) loadStepRecords(F.cur);
    render();
    return true;
  }

  function loadStepRecords(stepNo) {
    const rec = (F.data.records || {})[String(stepNo)] || {};
    F.val = {};
    Object.keys(rec).forEach((k) => {
      if (!k) return;
      let v = rec[k].value;
      if (k === '确认项' || k === '管护作业情况') { try { v = JSON.parse(v); } catch (e) {} }
      F.val[k] = v;
    });
    F.att = (rec[''] && rec[''].att) || [];
  }

  function step() { return F.data && F.data.steps[F.cur]; }
  function isDone(no) {
    const rec = (F.data.records || {})[String(no)];
    return !!(rec && rec[''] && rec[''].result);
  }
  /** 转序解锁：第 1 环节恒可用；之后每个环节要求**它前面所有**环节都已提交 */
  function unlocked(i) {
    for (let k = 0; k < i; k++) if (!isDone(F.data.steps[k].no)) return false;
    return true;
  }

  /* ── 必填闸门（口径与后端一致；后端还会再兜一次，C13）── */
  function gate() {
    const s = step(), miss = [];
    (s.controls || []).forEach((c) => {
      const nm = c.name;
      if (['验收人', '验收日期', '整改期限'].indexOf(nm) >= 0) return;
      if (c.type === '勾选') {
        const items = String(s.factors || '').split('、').map((x) => x.trim()).filter(Boolean);
        const ck = F.val[nm] || {};
        const lack = items.filter((x) => !ck[x]);
        if (lack.length) miss.push(`${nm}（${lack.length} 项未勾）`);
        return;
      }
      if (c.type === '多行纯文本') {
        if (!String(F.val[nm] || '').trim() && F.val['病虫害情况'] === '严重') miss.push(`${nm}（病虫害=严重时必填）`);
        return;
      }
      if (c.type === '多选') {
        if (!Array.isArray(F.val[nm]) || !F.val[nm].length) miss.push(nm);
        return;
      }
      if ('选填' in (c.arg || '') || c.type === '单行文本') return;
      if ('系统自动算' in (c.arg || '')) return;
      if (c.type === '数值×3') {
        const subs = c.sub || [];
        if (!subs.every((k) => String(F.val[k] || '').trim())) miss.push(`${nm}（${subs.join('/')}三项）`);
        return;
      }
      if (String(F.val[nm] || '').trim() === '') miss.push(nm);
    });
    if (!F.val['验收结果']) miss.push('验收结果');
    (s.attachments || []).forEach((a) => {
      const need = a.min || 0;
      if (!need) return;
      const got = F.att.find((x) => attKey(x.kind, x.label) === attKey(a.kind, a.label));
      if (!got || (got.count || 0) < need) miss.push(`附件「${a.label}」需 ${need} 份`);
    });
    return miss;
  }

  /* ── 成活率 / 保存率自动算（不可手填）── */
  function autoRate(s) {
    const isAlive = s.controls.some((c) => c.name === '成活率');
    const sN = num(F.val['抽查株数']);
    const dN = num(isAlive ? F.val['死亡株数'] : F.val['存活株数']);
    if (sN == null || sN <= 0 || dN == null) return null;
    return (100 * (sN - dN) / sN);
  }

  /* ── 渲染 ── */
  function render() {
    const host = $('#flowPanel');
    if (!host) return;
    if (!F.data) { host.innerHTML = ''; host.classList.add('hidden'); return; }
    host.classList.remove('hidden');

    let h = '<div class="flow-head">'
      + `<div class="flow-title">验收 · ${esc(F.data.flow_sheet.replace(/^流程-/, ''))}</div>`
      + `<div class="flow-meta">流程命中：${esc(F.data.flow_sheet)} · 优先级 ${F.data.prio}`
      + (F.data.cond_field && F.data.cond_field !== '1' ? ` · 条件 ${esc(F.data.cond_field)}=${esc(F.data.cond_val)}` : '')
      + '</div></div>';

    // 配置体检提示（把 Excel 的坑显式说出来，别等现场踩）
    (F.data.warn || []).forEach((w) => {
      h += `<div class="flow-warn"><b>配置提示</b>${esc(w.text || w)}</div>`;
    });

    // 步骤条
    h += '<div class="flow-steps">';
    F.data.steps.forEach((s, i) => {
      const done = isDone(s.no), cur = i === F.cur, lock = !unlocked(i);
      h += `<button class="flow-step${done ? ' done' : ''}${cur ? ' cur' : ''}${lock ? ' lock' : ''}" data-i="${i}">`
        + `<span class="dot">${done ? '✓' : i + 1}</span>`
        + `<span class="nm">${esc(String(s.step).replace(/（.*/, ''))}</span></button>`;
    });
    h += '</div>';

    // 环节卡片
    const s = step();
    const lock = !unlocked(F.cur);
    h += '<div class="flow-card' + (lock ? ' locked' : '') + '">';
    h += `<div class="flow-card-head"><span class="flow-card-title">${esc(s.step)}</span>`
      + `<span class="flow-badge">${esc(s.type)}</span>`
      + `<span class="flow-no">第 ${s.no} / ${F.data.steps.length} 环节</span></div>`;
    if (s.memo) h += `<div class="flow-note">${esc(s.memo)}</div>`;
    h += `<div class="flow-meta-sm">填报要求：${esc(s.require)} · 校验方式：${esc(s.check)} · 通过标准：${esc(s.pass)}</div>`;
    if (lock) {
      const prev = F.data.steps[F.cur - 1];
      h += `<div class="flow-lock">前一环节尚未提交，按「转序规则」本环节不可填报：${esc(prev.seq || '上一环节须先合格')}</div>`;
    } else {
      h += '<div class="flow-ctrls">' + ctrlsHtml(s) + '</div>';
      h += attHtml(s);
    }
    h += '</div>';

    // 提交栏
    const miss = gate();
    h += '<div class="flow-submit"><span class="prog">'
      + (miss.length ? '还差 ' + miss.length + ' 项：' + esc(miss.join('、')) : '本环节可提交')
      + '</span>'
      + (lock || miss.length ? '<span class="why">未完成，提交置灰</span>' : '')
      + `<button id="flowSubmit" class="btn primary"${lock || miss.length || F.busy ? ' disabled' : ''}>提交本环节</button></div>`;

    host.innerHTML = h;

    Array.prototype.forEach.call(host.querySelectorAll('.flow-step'), (b) => {
      b.onclick = () => {
        const i = +b.dataset.i;
        if (!unlocked(i)) { toast('请先完成前面的环节', true); return; }
        if (i === F.cur) return;
        saveDraft();
        F.cur = i; loadStepRecords(step().no); render();
      };
    });
    bind(s);
    on($('#flowSubmit'), 'click', () => submit(s));
  }

  function ctrlsHtml(s) {
    let h = '';
    (s.controls || []).forEach((c) => {
      const nm = esc(c.name);
      if (c.name === '验收人') {
        h += `<div class="fs-item"><div class="fs-label">${nm}
          <span class="fs-hint">自动取登录账号，不可手选</span></div>
          <input disabled value="${esc(window.__supUser || '')}"></div>`;
        return;
      }
      if (c.name === '验收日期') {
        h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span></div>
          <input type="date" data-f="${nm}" value="${esc(F.val[c.name] || today())}"></div>`;
        return;
      }
      if (c.name === '验收结果') {
        const opts = splitList(window.__supAcceptOpts || '合格;不合格;整改后合格');
        h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span></div>
          <select data-f="${nm}">${opts.map((o) => `<option${F.val[c.name] === o ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select>
          <div class="fs-hint">选项来自「参数」页 验收结果选项</div></div>`;
        return;
      }
      if (c.name === '整改期限') {
        if (F.val['验收结果'] !== '整改后合格') return '';
        h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span></div>
          <input type="date" data-f="${nm}" value="${esc(F.val[c.name] || '')}">
          <div class="fs-hint">验收结果 = 整改后合格 时必填</div></div>`;
        return;
      }
      if (c.type === '勾选') {
        const items = String(s.factors || '').split('、').map((x) => x.trim()).filter(Boolean);
        const ck = F.val[c.name] || {};
        const n = items.filter((x) => ck[x]).length;
        h += `<div class="fs-item"><div class="fs-label">逐项确认 <span class="req">*</span></div>
          <div class="fs-hint">本底数据来自 data 表，任何人不可修改，仅作对照基准</div>`;
        items.forEach((it) => {
          h += `<label class="fs-chk"><input type="checkbox" data-c="${nm}" data-v="${esc(it)}"${ck[it] ? ' checked' : ''}>`
            + `<span>${esc(it)}</span><span class="fs-std">${esc(stdText(it))}</span></label>`;
        });
        h += `<div class="fs-verdict ${n === items.length ? 'ok' : 'hint'}">已确认 ${n} / ${items.length} 项</div></div>`;
        return;
      }
      if (c.type === '数值×3') {
        const subs = c.sub || [];
        const stdRaw = String(F.data.std[c.name] || '');
        const dims = stdRaw.split(/[×xX*]/);
        h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span>
          <span class="fs-hint">${esc(dims[1] ? (c.arg || '') : (c.arg || ''))}</span></div>
          <div class="fs-hint">标准值 <b>${esc(stdRaw || '—')}</b>${stdRaw ? '（取自 data 列，拆为三项对照）' : '（data 无对应列，按方案人工判定）'}</div>
          <div class="fs-grid3">`;
        subs.forEach((k, i) => {
          h += `<input type="number" inputmode="decimal" data-f="${esc(k)}" placeholder="${esc(dims[i] || '')}" value="${esc(F.val[k] || '')}">`;
        });
        h += '</div><div class="fs-verdict" data-verdict="dim"></div></div>';
        return;
      }
      if (c.type === '下拉') {
        let opts = splitList(c.arg);
        if (/枚举|data/.test(c.arg)) opts = dataEnum(c.name, c.arg);
        h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span></div>
          <select data-f="${nm}">${opts.map((o) => `<option${F.val[c.name] === o ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select>
          ${F.data.std[c.name] != null && F.data.std[c.name] !== ''
            ? `<div class="fs-hint">标准值 <b>${esc(F.data.std[c.name])}</b></div>` : ''}</div>`;
        return;
      }
      if (c.type === '多选') {
        const opts = splitList(c.arg);
        const sel = F.val[c.name] || [];
        h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span>
          <span class="fs-hint">至少勾选 1 项</span></div><div class="fs-multi">`;
        opts.forEach((o) => {
          h += `<label class="fs-chk"><input type="checkbox" data-m="${nm}" data-v="${esc(o)}"${sel.indexOf(o) >= 0 ? ' checked' : ''}>${esc(o)}</label>`;
        });
        h += `</div><div class="fs-verdict ${sel.length ? 'ok' : 'hint'}">已选 ${sel.length} 项</div></div>`;
        return;
      }
      if (c.type === '多行纯文本') {
        const need = F.val['病虫害情况'] === '严重';
        h += `<div class="fs-item"><div class="fs-label">${nm} ${need ? '<span class="req">*</span>' : '<span class="fs-hint">选填</span>'}</div>
          <textarea data-f="${nm}" maxlength="500" rows="3" placeholder="纯文本，不带格式；≤500 字">${esc(F.val[c.name] || '')}</textarea>
          <div class="fs-hint">纯文本 ≤500 字；病虫害情况 = 严重 时必填</div></div>`;
        return;
      }
      if (c.type === '单行文本') {
        h += `<div class="fs-item"><div class="fs-label">${nm} <span class="fs-hint">选填 · ${esc(c.arg || '')}</span></div>
          <input type="text" data-f="${nm}" maxlength="20" value="${esc(F.val[c.name] || '')}"></div>`;
        return;
      }
      if (c.type === '日期') {
        h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span></div>
          <input type="date" data-f="${nm}" value="${esc(F.val[c.name] || '')}"></div>`;
        return;
      }
      // 数值（含「系统自动算」的只读展示）
      const auto = '系统自动算' in (c.arg || '');
      if (auto) {
        const rate = autoRate(s);
        h += `<div class="fs-item auto"><div class="fs-label">${nm}
          <span class="fs-hint">系统自动算，不可手填</span></div>
          <div class="fs-auto-v">${rate == null ? '—' : rate.toFixed(1) + '%'}</div>
          <div class="fs-hint">${'成活率' === c.name
            ? '(抽查株数 − 死亡株数) ÷ 抽查株数 × 100%'
            : '(抽查株数 − 存活株数) ÷ 抽查株数 × 100%'}</div></div>`;
        return;
      }
      h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span></div>
        <input type="number" inputmode="decimal" data-f="${nm}" value="${esc(F.val[c.name] || '')}">
        ${c.arg ? `<div class="fs-hint">${esc(c.arg)}</div>` : ''}</div>`;
    });
    /* 跨环节通用区：**验收结果 / 整改期限** 不在任何环节的「填报控件」列里
       （因子控件对照表说它们是「所有环节」通用），但又必填，所以在每个环节底部固定渲染。
       少了这一块，现场人员永远看不到验收结果下拉，提交永远被拦。 */
    h += '<div class="fs-common">'
      + `<div class="fs-item"><div class="fs-label">验收结果 <span class="req">*</span></div>
         <select data-f="验收结果">`
      + (window.__supAcceptOpts || ['合格', '不合格', '整改后合格']).map(
        (o) => `<option${F.val['验收结果'] === o ? ' selected' : ''}>${esc(o)}</option>`).join('')
      + `</select><div class="fs-hint">选项来自「参数」页 验收结果选项</div></div>`;
    if (F.val['验收结果'] === '整改后合格') {
      h += `<div class="fs-item"><div class="fs-label">整改期限 <span class="req">*</span></div>
        <input type="date" data-f="整改期限" value="${esc(F.val['整改期限'] || '')}">
        <div class="fs-hint">验收结果 = 整改后合格 时必填</div></div>`;
    }
    h += '</div>';
    return h;
  }

  function stdText(name) {
    const v = F.data.std[name];
    return v == null || v === '' ? '—' : v;
  }

  /** 下拉数据源：控件列写了「data「X」枚举」就从 data 该列去重（取该工作簿全部出现过的值） */
  function dataEnum(name, arg) {
    const m = /data\s*[「"]?([^」"」]+)[」"]?/.exec(String(arg || ''));
    const col = (m && m[1] ? m[1] : name).trim();
    const seen = [], set = {};
    (window.__supRows || []).forEach((r) => {
      const raw = r ? r[col] : null;
      const v = String(raw == null ? '' : raw).trim();
      if (v && !set[v]) { set[v] = 1; seen.push(v); }
    });
    // 兜底：拿不到该列（列名带尾随空格等）时，至少给出已填过的值
    if (!seen.length) {
      Object.keys(window.__supRows && window.__supRows[0] || {}).forEach((k) => {
        if (k.trim() !== col) return;
        window.__supRows.forEach((r) => {
          const v = String(r[k] == null ? '' : r[k]).trim();
          if (v && !set[v]) { set[v] = 1; seen.push(v); }
        });
      });
    }
    return seen;
  }

  function splitList(s) {
    return String(s || '').split(';').map((x) => x.trim()).filter(Boolean);
  }

  function attHtml(s) {
    const list = s.attachments || [];
    if (!list.length) return '';
    let h = '<div class="fs-att"><div class="fs-label">附件要求'
      + `<span class="fs-hint">Excel 原文「${esc(s.att_raw || '')}」</span></div><div class="fs-att-btns">`;
    list.forEach((a, i) => {
      const got = F.att.find((x) => attKey(x.kind, x.label) === attKey(a.kind, a.label));
      const cnt = got ? (got.count || 0) : 0;
      const okc = cnt >= (a.min || 0);
      const ico = a.kind === 'photo' ? '📷' : (a.kind === 'upload' ? '📎' : '📍');
      const verb = a.kind === 'photo' ? '拍照' : (a.kind === 'upload' ? '上传' : '打卡');
      h += `<button class="fs-att-btn${okc ? ' ok' : ''}" data-att="${i}">${ico} ${verb} · ${esc(a.label)}`
        + `${a.min ? ` <span class="fs-hint">至少 ${a.min} 份</span>` : ''}`
        + `${cnt ? ` <span class="cnt">${cnt}</span>` : ''}</button>`;
    });
    h += '</div>';
    // 已传清单（只显示文件名与位置，不预览大图，避免卡顿 —— 与既有行为一致）
    F.att.forEach((x) => {
      (x.names || []).forEach((n) => {
        h += `<div class="fs-att-item">📄 ${esc(n)}${x.operator ? ` · ${esc(x.operator)}` : ''}</div>`;
      });
    });
    h += `<div class="fs-hint" style="margin-top:6px">拍照 = App 内实时拍摄 · 禁相册选图 · 自动叠水印与定位；上传 = 可选照片或 PDF，操作人/时间/定位仍留痕</div></div>`;
    return h;
  }

  function bind(s) {
    const host = $('#flowPanel');
    if (!host) return;

    Array.prototype.forEach.call(host.querySelectorAll('[data-f]'), (el) => {
      const k = el.dataset.f;
      const ev = (el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') ? 'change' : 'input';
      on(el, ev, () => {
        F.val[k] = el.value;
        /* 验收结果 → 整改期限的出现与否跟着变；病虫害情况 → 问题描述必填性跟着变。
           这两个必须整块重渲染（局部刷新做不到）。 */
        if (k === '验收结果' || k === '病虫害情况') { render(); saveDraft(); return; }
        /* 抽查/死亡/存活株数变化：只刷成活率那个只读数字 + 判定文案，不整块重画
           （整块重画会让正在点的输入框失焦，现场填数字体验很差）。 */
        if (['抽查株数', '死亡株数', '存活株数'].indexOf(k) >= 0) {
          const rate = autoRate(s), box = host.querySelector('.fs-auto-v');
          if (box) box.textContent = rate == null ? '—' : rate.toFixed(1) + '%';
          verdict(); saveDraft(); refreshSubmit();
          return;
        }
        verdict(); saveDraft();
        // 附件下限变了 → 附件区要重画
        if ((s.attachments || []).some((a) => a.min)) { render(); return; }
        refreshSubmit();
      });
    });

    Array.prototype.forEach.call(host.querySelectorAll('[data-c]'), (el) => {
      on(el, 'change', () => {
        const f = el.dataset.c, v = el.dataset.v;
        F.val[f] = F.val[f] || {};
        if (el.checked) F.val[f][v] = true; else delete F.val[f][v];
        render(); saveDraft();
      });
    });

    Array.prototype.forEach.call(host.querySelectorAll('[data-m]'), (el) => {
      on(el, 'change', () => {
        const f = el.dataset.m, v = el.dataset.v;
        F.val[f] = F.val[f] || [];
        const i = F.val[f].indexOf(v);
        if (el.checked && i < 0) F.val[f].push(v);
        else if (!el.checked && i >= 0) F.val[f].splice(i, 1);
        render(); saveDraft();
      });
    });

    Array.prototype.forEach.call(host.querySelectorAll('[data-att]'), (el) => {
      on(el, 'click', () => shoot(+(el.dataset.att)));
    });

    verdict();
  }

  /** 数值对照标准的判定文案 */
  function verdict() {
    const host = $('#flowPanel');
    if (!host) return;
    const s = step();
    const box = host.querySelector('[data-verdict="dim"]');
    if (!box || !s) return;
    const c = (s.controls || []).find((x) => x.type === '数值×3');
    if (!c) return;
    const subs = c.sub || [];
    const dims = String(F.data.std[c.name] || '').split(/[×xX*]/).map(Number);
    const act = subs.map((k) => num(F.val[k]));
    if (act.some((x) => x == null)) { box.className = 'fs-verdict hint'; box.textContent = '三项填齐后自动对照标准值'; return; }
    const pass = dims.length >= subs.length && act.every((a, i) => a >= dims[i]);
    box.className = 'fs-verdict ' + (pass ? 'ok' : 'bad');
    box.textContent = `${act.join('×')}　对照标准 ${dims.join('×')}`
      + (pass ? ' · 通过' : ' · 低于标准');
  }

  function refreshSubmit() {
    const host = $('#flowPanel');
    if (!host) return;
    const s = step(), miss = gate();
    const bar = host.querySelector('.flow-submit');
    if (!bar) return;
    const lock = !unlocked(F.cur);
    bar.querySelector('.prog').textContent = miss.length
      ? '还差 ' + miss.length + ' 项：' + miss.join('、')
      : '本环节可提交';
    const why = bar.querySelector('.why');
    const btn = bar.querySelector('#flowSubmit');
    if (btn) btn.disabled = lock || miss.length > 0 || F.busy;
    if (why) why.style.display = (lock || miss.length) ? '' : 'none';
  }

  /* ── 拍照 / 上传 / 打卡：复用 app.js 的通道 ── */
  async function shoot(i) {
    const s = step(), a = (s.attachments || [])[i];
    if (!a) return;
    const key = attKey(a.kind, a.label);
    const rec = F.att.find((x) => attKey(x.kind, x.label) === key)
      || { kind: a.kind, label: a.label, min: a.min, count: 0, names: [], operator: '' };

    if (a.kind === 'upload') {
      // 上传：可选照片或 PDF（原生长流程会压缩；这里只做留痕 + 云同步）
      const inp = document.createElement('input');
      inp.type = 'file';
      inp.accept = a.label.indexOf('证件') >= 0 ? 'image/*,application/pdf' : 'image/*,application/pdf';
      inp.onchange = async () => {
        const f = inp.files && inp.files[0];
        if (!f) return;
        rec.count = (rec.count || 0) + 1;
        rec.names = (rec.names || []).concat(f.name);
        rec.operator = window.__supUser || '';
        F.att = F.att.filter((x) => attKey(x.kind, x.label) !== key).concat([rec]);
        saveDraft(); render();
        toast('已附 ' + f.name);
      };
      inp.click();
      return;
    }

    if (a.kind === 'punch') {
      // 定位打卡：取一次定位存进附件留痕（与轨迹不同，这是单点标记）
      const geo = navigator.geolocation
        ? await new Promise((res) => navigator.geolocation.getCurrentPosition(
          (p) => res(p.coords.latitude.toFixed(6) + ',' + p.coords.longitude.toFixed(6)),
          () => res(''), { timeout: 8000 }))
        : '';
      if (!geo) { toast('定位失败，请检查定位权限后重试', true); return; }
      rec.count = (rec.count || 0) + 1;
      rec.names = (rec.names || []).concat('打卡 ' + geo + ' · ' + new Date().toLocaleString('zh-CN'));
      rec.operator = window.__supUser || '';
      F.att = F.att.filter((x) => attKey(x.kind, x.label) !== key).concat([rec]);
      saveDraft(); render();
      toast('已打卡：' + geo);
      return;
    }

    // 拍照：走 app.js 的同一条通道（水印/压缩/目录/云同步全部一致）
    toast('正在处理水印…');
    window.pendingPhotoDone = (res) => {
      if (!res || !res.ok) { toast('拍照失败', true); return; }
      rec.count = (rec.count || 0) + 1;
      rec.names = (rec.names || []).concat(res.path);
      rec.operator = window.__supUser || '';
      F.att = F.att.filter((x) => attKey(x.kind, x.label) !== key).concat([rec]);
      saveDraft(); render();
      toast((res.saved || '已拍照') + (res.sync || ''));
    };
    const pi = $('#photoInput');
    if (!pi) { toast('找不到拍照控件', true); return; }
    pi.click();
  }

  /* ── 提交 ── */
  async function submit(s) {
    if (F.busy) return;
    const miss = gate();
    if (miss.length) { toast('还有 ' + miss.length + ' 项未完成', true); return; }
    if (!unlocked(F.cur)) { toast('请先完成前面的环节', true); return; }
    F.busy = true; refreshSubmit();
    try {
      const payload = {
        row: F.rowIdx, step_no: s.no,
        result: F.val['验收结果'] || '',
        att: F.att,
        values: Object.assign({}, F.val, {
          验收人: window.__supUser || '',
          验收日期: F.val['验收日期'] || today(),
        }),
      };
      /* 注意：api() 只是 fetch 包装，**不会自动把对象序列化成 JSON**。
         裸传对象会得到 415 UNSUPPORTED MEDIA TYPE（2026-10-14 线上实测踩到，
         表现为"点提交没反应也不报错"）。必须自己 JSON.stringify + 声明 Content-Type。 */
      await api(`/api/workbooks/${F.wid}/flow`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      // 回读一次拿服务端最终结果（实测对照设计会自动改写验收结果）
      F.data = await api(`/api/workbooks/${F.wid}/flow?row=${F.rowIdx}`);
      clearDraft();
      toast(`已提交「${s.step}」`);
      if (F.cur < F.data.steps.length - 1) { F.cur += 1; loadStepRecords(step().no); }
      render();
    } catch (e) {
      toast('提交失败：' + e.message, true);
    } finally { F.busy = false; refreshSubmit(); }
  }

  /* ── 离场冲刷（C14/C17）：切后台/关页前存草稿，切回前台不自动提交但草稿已在 ── */
  function installGuards() {
    on(document, 'visibilitychange', () => {
      if (document.visibilityState === 'hidden') { saveDraft(); }
      else if (F.data) { render(); }          // 切回前台重画：草稿已在，不丢
    });
    on(window, 'pagehide', saveDraft);
    on(window, 'beforeunload', saveDraft);
  }

  window.supFlow = {
    open, close, render, installGuards, saveDraft, clearDraft,
    // 调试出口：线上排查"某环节明明提交了却还显示未完成"这类问题时，
    // 直接在控制台看 supFlow.debug() 就能知道 records 的真实 key 与 result，
    // 不用猜（不加这个就只能靠截图猜，已经猜错过一次）。
    debug: () => ({
      wid: F.wid, rowIdx: F.rowIdx, cur: F.cur,
      recordKeys: Object.keys((F.data && F.data.records) || {}),
      records: F.data && F.data.records,
      isDone: F.data ? F.data.steps.map((s) => [s.no, isDone(s.no)]) : null,
    }),
  };
  installGuards();
})();