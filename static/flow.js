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
    data: null,         // 后端回的 {flow_sheet, steps, std, records, multi, warn}
    cur: 0,             // 当前环节下标
    recNo: 0,           // 多次环节：0 = 新增一条；>0 = 编辑第 N 条
    entry: '',          // 当前入口（验收/巡视/材料）；老格式为空
    val: {},            // 当前环节的输入值 {因子: 值}
    att: [],            // 当前环节的附件 [{kind,label,min,count,names:[]}]
    busy: false,
    /* v0.38：v2 模板（schema=flow_tpl/1）—— 整条流程一张表单，状态按环节分桶 */
    v2val: {},          // {环节序号: {因子: 值}}
    v2att: {},          // {环节序号: [{kind,label,min,count,names:[]}]}
  };

  /* 只有这些结论算"这一关过了"（与后端 flow_config.PASS_RESULTS 同一份口径）。
     不通过 = 已留痕但不放行，必须整改后重交通过才能进下一环节（纪要红线）。 */
  const PASS_RESULTS = ['通过', '合格', '整改后合格', '全部完成', '已完成', '符合'];

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
        ts: Date.now(), wid: F.wid, rowIdx: F.rowIdx, entry: F.entry,
        cur: F.cur, recNo: F.recNo, val: F.val, att: F.att,
        flow: (F.data && F.data.schema === 'flow_tpl/1') ? F.data.flow_sheet : '',
        v2val: F.v2val, v2att: F.v2att,
      }));
    } catch (e) { /* 配额满等一律不阻断填写 */ }
  }
  function clearDraft() { try { localStorage.removeItem(DRAFT_KEY); } catch (e) {} }

  /* ── 进入某行流程 ──
     entry 留空 = 让后端挑优先级最高的那条（老行为）；
     指定「验收 / 巡视 / 材料」= 只在这个入口的流程里匹配。
     同一个小班可以既有验收流程又有巡视流程，两条都得能进 —— 靠顶部 tab 切。
     v0.38：flowSel = v2 模板下拉框里选的那条流程（`流程-回塘底肥`），
     留空 = 后端按「流程路由」挑当前该做的那条。 */
  async function open(wid, rowIdx, entry, flowSel) {
    F.wid = wid; F.rowIdx = rowIdx; F.cur = 0; F.recNo = 0; F.val = {}; F.att = [];
    F.v2val = {}; F.v2att = {};
    let j;
    const qs = `/api/workbooks/${wid}/flow?row=${rowIdx}`
      + (entry ? `&entry=${encodeURIComponent(entry)}` : '')
      + (flowSel ? `&flow=${encodeURIComponent(flowSel)}` : '');
    try { j = await api(qs); }
    catch (e) { toast('读取验收流程失败：' + e.message, true); return false; }
    if (!j || j.ok === false) {
      F.data = null;
      // 该行任何流程都没命中 → 退回网格填表（老行为）
      if (!j || !(j.entries || []).length) return false;
      // 只是这个入口没命中：留着上一份数据反而更糊涂，直接报清楚
      // v0.37：rowIdx<0 = 项目级（材料检验不挂小班档案），主语是该"项目"不是该"小班"
      const who = rowIdx < 0 ? '该项目' : '该小班';
      toast(`${who}没有「${entry}」流程`, true);
      return false;
    }
    F.data = j;
    F.entry = j.entry || '';
    const d = loadDraft();

    // v2 模板：整条流程一张表，值按环节分桶（j.values = {环节序号: {因子: 值}}）
    if (j.schema === 'flow_tpl/1') {
      const same = d && d.wid === wid && d.rowIdx === rowIdx
        && (d.flow || '') === j.flow_sheet;
      if (!same) clearDraft();
      if (!j.flow_repeat) {          // 可重复流程（日常巡护）= 新一次记录，不带上一次的值
        const vals = j.values || {};
        Object.keys(vals).forEach((k) => {
          F.v2val[k] = {};
          const one = vals[k] || {};
          Object.keys(one).forEach((f) => { F.v2val[k][f] = decodeVal(one[f]); });
        });
      }
      if (same) {
        F.v2val = Object.assign(F.v2val, d.v2val || {});
        F.v2att = d.v2att || {};
      }
      render();
      return true;
    }

    // 回填已填记录（第 1 环节）
    loadStepRecords(j.steps[0].no);
    // 本地草稿（同 wid + 同行 + 同入口）优先，覆盖服务器值 —— 进程被杀也不丢（C17）
    if (d && d.wid === wid && d.rowIdx === rowIdx && (d.entry || '') === F.entry) {
      F.cur = Math.min(d.cur || 0, j.steps.length - 1);
      F.recNo = d.recNo || 0;
      F.val = Object.assign(F.val, d.val || {});
      if (d.att && d.att.length) F.att = d.att;
    } else {
      clearDraft();
    }
    if (F.cur !== 0) loadStepRecords(step().no, d ? (d.recNo || 0) : 0);
    render();
    return true;
  }

  /** 值可能是 JSON（多选/勾选存成数组或对象），按首字符判断，别硬 parse（C19 同源） */
  function decodeVal(v) {
    if (typeof v === 'string' && v && (v.charAt(0) === '[' || v.charAt(0) === '{')) {
      try { return JSON.parse(v); } catch (e) { return v; }
    }
    return v;
  }

  function loadStepRecords(stepNo, recNo) {
    F.recNo = recNo || 0;
    F.val = {}; F.att = [];
    const s = (F.data.steps || []).find((x) => x.no === stepNo) || {};
    const multi = (F.data.multi || {})[String(stepNo)] || {};
    if (F.recNo > 0 && multi[String(F.recNo)]) {
      const one = multi[String(F.recNo)];
      Object.keys(one.values || {}).forEach((k) => { F.val[k] = decodeVal(one.values[k]); });
      F.att = one.att || [];
      return;
    }
    F.recNo = 0;
    // 多次环节的"新增一条"必须是空白的，不能把上一条的值带进来
    if (s.multi) return;
    const rec = (F.data.records || {})[String(stepNo)] || {};
    Object.keys(rec).forEach((k) => {
      if (!k) return;
      F.val[k] = decodeVal(rec[k].value);
    });
    F.att = (rec[''] && rec[''].att) || [];
  }

  function step() { return F.data && F.data.steps[F.cur]; }
  /** 提交过就算"已填"（仅记录类环节没有结论，但有环节级行） */
  function isDone(no) {
    const rec = (F.data.records || {})[String(no)];
    return !!(rec && rec['']);
  }
  function resultOf(no) {
    const rec = (F.data.records || {})[String(no)] || {};
    return (rec[''] && rec[''].result) || '';
  }
  function isPass(no) {
    return PASS_RESULTS.indexOf(resultOf(no)) >= 0;
  }
  /** 转序解锁：声明了「前置环节」就只等那几个（材料检验不必等造林工序）；
   *  没声明 = 前面所有环节都要**结论通过**。
   *  v0.36：前置条件可以带期望结论（`整地=合格`），期望落在 s.prev_result 里；
   *  期望是正向词时按"这一类"放宽判（现场可能填「合格」而不是「通过」）。 */
  function prevOk(no, want) {
    const got = resultOf(no);
    if (!got) return false;
    if (PASS_RESULTS.indexOf(want || '通过') >= 0) return PASS_RESULTS.indexOf(got) >= 0;
    return got === want;
  }
  function unlocked(i) {
    const s = F.data.steps[i];
    const prev = (s && s.prev) || [];
    const want = (s && s.prev_result) || {};
    if (prev.length) {
      const names = F.data.steps.map((x) => x.step);
      for (let k = 0; k < prev.length; k++) {
        const idx = names.indexOf(prev[k]);
        if (idx >= 0 && !prevOk(F.data.steps[idx].no, want[prev[k]])) return false;
      }
      return true;
    }
    for (let k = 0; k < i; k++) if (!isPass(F.data.steps[k].no)) return false;
    return true;
  }

  /* ── 校验规则（③「校验规则」列；服务端有一份权威实现，这里只是少跑一趟）──
     口径与 flow_config.check_rule 完全一致：空值不管（归必填），多规则用「；」分隔。 */
  function ruleBad(rule, val, label) {
    const t = String(rule || '').trim();
    if (!t || val == null || typeof val === 'object') return '';
    const v = String(val).trim();
    if (v === '') return '';
    const nm = label || '该值';
    const toks = t.split(/[；;]/).map((x) => x.trim()).filter(Boolean);
    for (let i = 0; i < toks.length; i++) {
      const tok = toks[i];
      let m;
      if (tok === '整数') {
        if (!/^-?\d+$/.test(v)) return nm + '必须是整数';
        continue;
      }
      if (tok === '日期≥今天') {
        const d = v.slice(0, 10);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return nm + '不是合法日期（要 2026-10-21 这种）';
        if (d < today()) return nm + '不能早于今天';
        continue;
      }
      m = /^≤\s*(\d+)\s*字$/.exec(tok);
      if (m) {
        if (v.length > +m[1]) return nm + '不能超过 ' + m[1] + ' 字（现 ' + v.length + ' 字）';
        continue;
      }
      m = /^正则[:：](.+)$/.exec(tok);
      if (m) {
        try { if (!new RegExp(m[1]).test(v)) return nm + '格式不对'; } catch (e) { /* 配置错不拦现场 */ }
        continue;
      }
      m = /^(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)$/.exec(tok);
      if (m) {
        const x = parseFloat(v);
        if (isNaN(x)) return nm + '必须是数字';
        if (x < +m[1] || x > +m[2]) return nm + '要在 ' + m[1] + '~' + m[2] + ' 之间（现 ' + v + '）';
        continue;
      }
      m = /^(≥|>=|≤|<=|>|<)\s*(-?\d+(?:\.\d+)?)$/.exec(tok);
      if (m) {
        const x = parseFloat(v), n = +m[2], op = m[1];
        if (isNaN(x)) return nm + '必须是数字';
        const badv = ((op === '≥' || op === '>=') && x < n)
          || ((op === '≤' || op === '<=') && x > n)
          || (op === '>' && x <= n) || (op === '<' && x >= n);
        if (badv) return nm + '不满足「' + tok + '」（现 ' + v + '）';
        continue;
      }
    }
    return '';
  }

  function ruleMiss(s) {
    const out = [];
    if (!s) return out;
    (s.controls || []).forEach((c) => {
      const r = c.rule || '';
      if (!r) return;
      if (c.type === '数值×3') {
        (c.sub || []).forEach((k) => { const e = ruleBad(r, F.val[k], k); if (e) out.push(e); });
        return;
      }
      const e = ruleBad(r, F.val[c.name], c.name);
      if (e) out.push(e);
    });
    return out;
  }

  /* ── 必填闸门（口径与后端一致；后端还会再兜一次，C13）── */
  function gate() {
    const s = step(), miss = [];
    if (!s) return miss;
    (s.controls || []).forEach((c) => {
      const nm = c.name;
      // 「是否通过」由底部结论区统一判定（ctrlsHtml 里也跳过了它），
      // 这里必须一起跳过 —— 否则同一个缺失会被数两遍，界面出"还差 3 项：是否通过、是否通过…"
      // （2026-10-21 截图自测发现）。
      if (['验收人', '验收日期', '整改期限', '验收结果', '是否通过'].indexOf(nm) >= 0) return;
      if (c.required === false) return;            // 业务员文档里明确写了「选填」
      if (c.type === '勾选') {
        const items = String(s.factors || '').split('、').map((x) => x.trim()).filter(Boolean);
        const ck = F.val[nm] || {};
        const lack = items.filter((x) => !ck[x]);
        if (lack.length) miss.push(`${nm}（${lack.length} 项未勾）`);
        return;
      }
      if (c.type === '多行纯文本') {
        if (!String(F.val[nm] || '').trim() && F.val['病虫害情况'] === '严重') miss.push(`${nm}（病虫害=严重时必填）`);
        else if (!String(F.val[nm] || '').trim() && c.required === true) miss.push(nm);
        return;
      }
      if (c.type === '多选') {
        if (!Array.isArray(F.val[nm]) || !F.val[nm].length) miss.push(nm);
        return;
      }
      /* 注意：arg 是**字符串**（如 '长/宽/深,cm,≥标准自动判'）。JS 的 `in` 只能用于对象，
         `'选填' in 字符串` 会直接 TypeError → gate() 崩 → render() 崩 → 整个验收界面空白
         （2026-10-14 线上实测，靠 v0.32.1 的 jslog 第一时间抓到）。查字符串用 indexOf。 */
      if (String(c.arg || '').indexOf('选填') >= 0) return;
      // 老格式：单行文本一律可空（arg 里没有 required 标记）；新格式才区分必填/选填
      if (c.type === '单行文本' && c.required !== true) return;
      if (String(c.arg || '').indexOf('系统自动算') >= 0) return;
      if (c.type === '数值×3') {
        const subs = c.sub || [];
        if (!subs.every((k) => String(F.val[k] || '').trim())) miss.push(`${nm}（${subs.join('/')}三项）`);
        return;
      }
      if (String(F.val[nm] || '').trim() === '') miss.push(nm);
    });
    // 结论：是否通过 / 三档结果（「仅记录」环节不要求结论，s.need_result === false）
    if (s.verdict === '是否通过' && !F.val['是否通过']) miss.push('是否通过');
    if (s.note_required && F.val['是否通过'] === '不通过'
      && !String(F.val['不通过原因'] || '').trim()) miss.push('不通过原因（选不通过必须写清问题）');
    if (s.need_result !== false && !F.val['验收结果']) miss.push('验收结果');
    (s.attachments || []).forEach((a) => {
      const need = a.min || 0;
      if (!need) return;
      const got = F.att.find((x) => attKey(x.kind, x.label) === attKey(a.kind, a.label));
      if (!got || (got.count || 0) < need) miss.push(`附件「${a.label}」需 ${need} 份`);
    });
    // v0.36：③「校验规则」的即时提示（填了但不合规 → 同样挡住提交，不必等服务端 400）
    return miss.concat(ruleMiss(s));
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
    if (F.data.schema === 'flow_tpl/1') { renderV2(host); return; }

    let h = '<div class="flow-head">'
      + `<div class="flow-title">${esc(F.data.entry || '验收')} · `
      + `${esc(F.data.flow_sheet.replace(/^流程-/, '').replace(/·[^·]*$/, ''))}</div>`
      + `<div class="flow-meta">流程命中：${esc(F.data.flow_sheet)} · 优先级 ${F.data.prio}`
      // v0.37：项目级（材料检验按批次）明说"不挂小班"，避免现场以为漏选了小班
      + (F.rowIdx < 0 ? ' · 项目级（按批次登记，不挂小班）' : '')
      // 「独立入口=独立入口」是内部注册标记（flow_config 写死它，保证任何行都
      // 匹配不到材料流程），给现场人看反而困惑 —— 项目级不显示这一行
      + (F.data.cond_field && F.data.cond_field !== '1' && F.data.cond_field !== '独立入口'
         ? ` · 条件 ${esc(F.data.cond_field)}=${esc(F.data.cond_val)}` : '')
      + '</div></div>';

    // 入口 tab（验收 / 巡视）：同一个小班往往两条流程都能走，
    // **由人决定这次是来做验收还是巡视**，不是让匹配器替他选（否则巡视永远进不去）。
    // 材料入口不挂小班档案，不在这个 tab 里出现（项目级入口，另做）。
    const ents = F.data.entries || [];
    if (ents.length > 1) {
      h += '<div class="flow-entries">';
      ents.forEach((e) => {
        const cur = e.entry === F.data.entry ? ' cur' : '';
        h += `<button class="fe${cur}" data-entry="${esc(e.entry)}">${esc(e.entry)}`
          + `<span class="n">${e.steps || 0} 环节</span></button>`;
      });
      h += '</div>';
    }

    // 配置体检提示（把 Excel 的坑显式说出来，别等现场踩）
    (F.data.warn || []).forEach((w) => {
      h += `<div class="flow-warn"><b>配置提示</b>${esc(w.text || w)}</div>`;
    });

    // 步骤条（v0.32.4：三态配色 + 自动换行；**所有环节都可点开查看**——
    // 未解锁的环节进入只读预览，让人先知道"这一步要填什么"，不必等解锁后才发现缺料）
    h += '<div class="flow-steps">';
    F.data.steps.forEach((s, i) => {
      const done = isDone(s.no), cur = i === F.cur, lock = !unlocked(i);
      const st = done ? 'done' : (lock ? 'lock' : 'todo');
      h += `<button class="flow-step ${st}${cur ? ' cur' : ''}" data-i="${i}">`
        + `<span class="dot">${done ? '✓' : i + 1}</span>`
        + `<span class="nm">${esc(String(s.step).replace(/（.*/, ''))}</span></button>`;
    });
    h += '</div>';
    h += '<div class="flow-legend">'
      + '<span class="lg done">已填写</span>'
      + '<span class="lg todo">待填写</span>'
      + '<span class="lg lock">不可填写（点击可预览）</span></div>';

    // 环节卡片
    const s = step();
    const lock = !unlocked(F.cur);
    h += '<div class="flow-card' + (lock ? ' locked' : '') + '">';
    h += `<div class="flow-card-head"><span class="flow-card-title">${esc(s.step)}</span>`
      + `<span class="flow-badge">${esc(s.type)}</span>`
      + `<span class="flow-no">第 ${s.no} / ${F.data.steps.length} 环节</span></div>`;
    if (s.memo) h += `<div class="flow-note">${esc(s.memo)}</div>`;
    h += `<div class="flow-meta-sm">填报要求：${esc(s.require)} · 校验方式：${esc(s.check)} · 通过标准：${esc(s.pass)}</div>`;

    // 本环节要显示的基本信息（业务专家文档「显示 / 不显示的基本信息」两列）——
    // 现场先看到本底数据再填，不用来回翻 Excel；只读，任何人不可改。
    // 语义：白名单留空 + expert 文档 = data 全部列；黑名单优先从中剔除。
    // 老 12 列流程没有 expert 标记、白名单也为空 → 不显示这一块（线上行为不变）。
    const std = F.data.std || {};
    let basic = (s.basic_info || []).slice();
    // 白名单留空 = data 全部列。**用 std_cols（真实表头），不要用 Object.keys(std)** ——
    // std 里还混着「控件名 → 设计值」的映射（整地数量=627），会把假列算进来。
    if (!basic.length && s.expert) basic = (F.data.std_cols || Object.keys(std));
    // 去重 + 去空（data 表头可能带尾随空格，std_lookup 同时保留原名与 trim 名）
    const seenB = {};
    basic = basic.filter((k) => {
      const t = String(k == null ? '' : k).trim();
      if (!t || seenB[t]) return false;
      seenB[t] = 1;
      return true;
    });
    const hide = s.basic_hide || [];
    if (hide.length) {
      const bad = {};
      hide.forEach((k) => { bad[String(k).trim()] = 1; });
      basic = basic.filter((k) => !bad[String(k).trim()]);
    }
    // v0.37：项目级（材料批次一条记录关联多个小班）只显示拿得到值的基本信息 ——
    // 白名单里随小班变化的列（造林树种/总株数…）在项目级没有单一值，
    // 渲染成「—」会被现场当成"数据缺失"，直接不显示；小班差异看「关联小班」多选
    if (F.rowIdx < 0) {
      basic = basic.filter((k) => {
        const v = (std[k] != null && std[k] !== '') ? std[k] : std[String(k).trim()];
        return v != null && v !== '';
      });
    }
    if (basic.length) {
      const cellOf = (k) => {
        let v = std[k];
        if (v == null || v === '') v = std[String(k).trim()];
        if (v == null || v === '') v = '—';
        return `<div class="fb-cell"><span class="k">${esc(String(k).trim())}</span>`
          + `<span class="v">${esc(v)}</span></div>`;
      };
      h += '<div class="flow-basic"><div class="fb-h">本环节基本信息'
        + `<span>来自本底数据，不可修改 · 共 ${basic.length} 项`
        + (hide.length ? ` · 已隐藏 ${hide.length} 项` : '') + '</span></div>';
      if (basic.length > 6) {
        // 列多时默认折叠（原生 details，Chrome 73 支持；不用自己写展开逻辑）
        h += '<details class="fb-more"><summary>展开全部 ' + basic.length + ' 项</summary>'
          + '<div class="fb-grid">' + basic.map(cellOf).join('') + '</div></details>';
      } else {
        h += '<div class="fb-grid">' + basic.map(cellOf).join('') + '</div>';
      }
      h += '</div>';
    }

    // 多次环节：历史记录 + 新增一条（巡视按次、整地分批、苗木按批次）
    if (s.multi) {
      const m = ((F.data.multi || {})[String(s.no)]) || {};
      const keys = Object.keys(m).map(Number).sort((a, b) => a - b);
      h += '<div class="flow-multi"><div class="fm-head">本环节可多次填报'
        + `<span>已填 ${keys.length} 条</span>`
        + (F.recNo ? `<b class="fm-editing">正在编辑第 ${F.recNo} 条</b>` : '')
        + '</div>';
      if (keys.length) {
        h += '<div class="fm-list">';
        keys.forEach((k) => {
          const r = m[String(k)] || {};
          h += `<button class="fm-item${F.recNo === k ? ' cur' : ''}" data-rec="${k}">`
            + `<span class="n">#${k}</span>`
            + `<span class="d">${esc(recStamp(r))}</span>`
            + `<span class="r">${esc(r.result || '—')}</span></button>`;
        });
        h += '</div>';
      }
      h += `<button class="fm-new" id="flowNewRec">＋ 新增一条${keys.length ? '（空白）' : ''}</button></div>`;
    }
    if (lock) {
      const prev = F.data.steps[F.cur - 1];
      h += `<div class="flow-lock">🔒 按「转序规则」本环节暂不可填报（前面环节提交后解锁）：${esc(prev ? (prev.seq || '上一环节须先合格') : '')}。下方控件为只读预览。</div>`;
    }
    h += '<div class="flow-ctrls">' + ctrlsHtml(s) + '</div>';
    h += attHtml(s);
    h += '</div>';

    // 提交栏（锁定环节不显示"还差 N 项"——那是对空表单算的，全是误报）
    const miss = gate();
    h += '<div class="flow-submit"><span class="prog">'
      + (lock ? '🔒 本环节未解锁'
        : miss.length ? '还差 ' + miss.length + ' 项：' + esc(miss.join('、')) : '本环节可提交')
      + '</span>'
      + (lock ? '<span class="why">未解锁，提交置灰</span>'
        : miss.length ? '<span class="why">未完成，提交置灰</span>' : '')
      + `<button id="flowSubmit" class="btn primary"${lock || miss.length || F.busy ? ' disabled' : ''}>${s.multi ? (F.recNo ? '保存第 ' + F.recNo + ' 条' : '保存这一条') : '提交本环节'}</button></div>`;

    host.innerHTML = h;

    // 锁定环节 = 只读预览：把控件全部禁用（disabled 元素不派发 input/change，
    // bind() 的监听自然失效，不会污染 F.val），拍照/上传按钮同样禁用
    if (lock) {
      Array.prototype.forEach.call(
        host.querySelectorAll('.flow-card.locked input, .flow-card.locked select,'
          + ' .flow-card.locked textarea, .flow-card.locked button'),
        (el) => { el.disabled = true; });
    }

    Array.prototype.forEach.call(host.querySelectorAll('.flow-entries .fe'), (b) => {
      b.onclick = () => {
        const en = b.dataset.entry;
        if (!en || en === F.data.entry) return;
        saveDraft();
        open(F.wid, F.rowIdx, en);
      };
    });
    Array.prototype.forEach.call(host.querySelectorAll('.flow-step'), (b) => {
      b.onclick = () => {
        const i = +b.dataset.i;
        if (i === F.cur) return;
        saveDraft();
        F.cur = i; loadStepRecords(step().no, 0); render();   // 未解锁环节 = 只读预览（render 处理）
      };
    });
    bind(s);
    on($('#flowSubmit'), 'click', () => submit(s));
  }

  /* ══ v2 模板：选流程 → 整条流程一张表单（v0.38，tpl-人工造林-验收流程模板）══
     与老格式的"环节向导"不同，v2 一条流程只有一个提交按钮：
     模板里的「备注」是整条流程共用的一行，按环节提交会出现半成品数据（见 doc/029）。
     所有控件、选项、设计值仍由 Excel 推导，这里不写死任何环节名。 */
  const flowName = (s) => String(s || '').replace(/^流程-/, '');
  const cjkCount = (s) => (String(s || '').match(/[\u4e00-\u9fff]/g) || []).length;

  function v2Step(no) {
    return (F.data && (F.data.steps || []).find((x) => x.no === +no)) || null;
  }
  function v2Vals(no) {
    if (!F.v2val[no]) F.v2val[no] = {};
    return F.v2val[no];
  }

  /** 整条流程的值摊平成一层（公式跨环节取值用） */
  function v2Flat() {
    const out = {};
    (F.data.steps || []).forEach((s) => Object.assign(out, F.v2val[s.no] || {}));
    return out;
  }

  /** 「自动(公式)」求值：变量按"精确 → 后缀唯一 → 包含"解析（公式用短名，环节用长名）。
   *  只允许数字与 + - * / 括号，不 eval 任意文本。 */
  function evalFormula(expr, vals) {
    let s = String(expr || '');
    if (!s) return null;
    s = s.replace(/×/g, '*').replace(/÷/g, '/')
      .replace(/（/g, '(').replace(/）/g, ')').replace(/%\s*$/, '');
    const toks = s.match(/[\u4e00-\u9fff]+/g) || [];
    const keys = Object.keys(vals);
    const bad = [];
    toks.forEach((tok) => {
      if (bad.indexOf(tok) >= 0) return;
      const k = keys.find((x) => x === tok) || keys.find((x) => x.endsWith(tok))
        || keys.find((x) => x.indexOf(tok) >= 0);
      const n = k ? num(vals[k]) : null;
      if (n == null) { bad.push(tok); return; }
      s = s.split(tok).join(String(n));
    });
    if (bad.length) return null;
    if (!/^[0-9+\-*/().\s]+$/.test(s)) return null;
    try {
      const v = Function('return (' + s + ')')();    // 已限定为纯数字表达式
      return isFinite(v) ? Math.round(v * 100) / 100 : null;
    } catch (e) { return null; }
  }

  /** 备注何时必须填：结论=不通过，或任一项选中了带「→备注」的选项。 */
  function v2NoteNeed() {
    const steps = F.data.steps || [];
    for (let i = 0; i < steps.length; i++) {
      const vals = F.v2val[steps[i].no] || {};
      const ctrls = steps[i].controls || [];
      for (let j = 0; j < ctrls.length; j++) {
        const on = ctrls[j].note_on || [];
        if (on.indexOf(String(vals[ctrls[j].name] || '')) >= 0) return true;
      }
    }
    return false;
  }

  function v2Gate() {
    const miss = [];
    const noteNeed = v2NoteNeed();
    const noteNo = F.data.note_step_no || 0;
    (F.data.steps || []).forEach((s) => {
      const vals = F.v2val[s.no] || {};
      (s.controls || []).forEach((c) => {
        if (c.hidden) {
          if (noteNeed && s.no === noteNo && cjkCount(vals[c.name]) < 2) {
            miss.push('备注（≥2 个汉字）');
          }
          return;
        }
        if (c.verdict) return;      // 结论由底部「通过 / 不通过」按钮给出，不在表单里校验
        if (c.type === '自动') return;
        const v = vals[c.name];
        const label = c.name === s.step ? c.name : s.step + '·' + c.name;
        if (c.type === '多选') {
          if (!Array.isArray(v) || !v.length) miss.push(label);
          return;
        }
        if (String(v == null ? '' : v).trim() === '') { miss.push(label); return; }
        const e = ruleBad(c.rule, v, c.name);
        if (e) miss.push(e);
      });
    });
    return miss;
  }

  /** 进表单前把 Excel 的缺省值 / 自动值铺进状态，保证"看到的 = 要提交的" */
  function v2Normalize() {
    (F.data.steps || []).forEach((s) => {
      const vals = v2Vals(s.no);
      (s.controls || []).forEach((c) => {
        if (c.default && String(vals[c.name] || '') === '') vals[c.name] = c.default;
      });
    });
    // 公式变量可能来自别的环节（成活株数在第 13 行、公式在第 15 行）→ 铺平整条流程再求值
    const flat = v2Flat();
    (F.data.steps || []).forEach((s) => {
      const vals = v2Vals(s.no);
      (s.controls || []).forEach((c) => {
        if (c.type !== '自动') return;
        const v = evalFormula(c.formula, flat);
        if (v != null) { vals[c.name] = v; flat[c.name] = v; }
      });
    });
  }

  function renderV2(host) {
    v2Normalize();
    const d = F.data, steps = d.steps || [];
    const locked = !!d.flow_locked;
    const noteNeed = v2NoteNeed();
    const curName = flowName(d.flow_sheet);
    // 主线按序号在前；独立流程（巡护）排最后，且不参与默认选择
    const list = (d.flow_list || []).slice().sort((a, b) =>
      ((a.independent ? 1 : 0) - (b.independent ? 1 : 0)) || ((a.no || 0) - (b.no || 0)));

    let h = '<div class="flow-head">'
      + `<div class="flow-title">${esc(curName)}</div>`
      + `<div class="flow-meta">项目类型：${esc(d.cond_val || '')}`
      + (d.flow_independent ? ' · 独立流程（可多次，不参与主线先后）'
        : d.flow_repeat ? ' · 可重复填报（每次提交新增一次记录）' : '')
      + (d.flow_trigger ? ` · ${esc(d.flow_trigger)}` : '')
      + '</div></div>';

    // 流程下拉：路由里的全部流程按序号列出（未解锁的也能点开预览，只是不能提交）
    h += '<div class="flow-pick"><label for="flowSel">流程</label>'
      + '<select id="flowSel">';
    list.forEach((f) => {
      const tag = f.done ? (f.repeat ? '　可继续填报' : '　✓ 已完成')
        : (f.available ? '' : '　🔒 需先完成前置');
      const noTag = f.independent ? '独立' : `${f.no}. `;
      h += `<option value="${esc(f.sheet)}"${f.sheet === d.flow_sheet ? ' selected' : ''}>`
        + `${noTag}${esc(flowName(f.sheet))}${tag}</option>`;
    });
    h += '</select>';
    if (d.flow_repeat) {
      h += `<span class="fs-hint">已填报 ${d.flow_rec_no || 0} 次，本次为第 ${(d.flow_rec_no || 0) + 1} 次</span>`;
    } else {
      h += (d.flow_rec_no ? '<span class="fs-hint">已有一次填报，提交将覆盖为本表单内容</span>' : '');
    }
    h += '</div>';

    (d.warn || []).forEach((w) => {
      h += `<div class="flow-warn"><b>配置提示</b>${esc(w.text || w)}</div>`;
    });

    if (locked) {
      h += `<div class="flow-lock">🔒 按「流程路由」，需先完成前置流程：`
        + esc((d.flow_prereq || []).map(flowName).join('、'))
        + '（结论通过后自动解锁）。下方为只读预览。</div>';
    }

    steps.forEach((s) => {
      const vals = v2Vals(s.no);
      const c = (s.controls || [])[0] || {};
      // 结论行不单独渲染：底部「通过 / 不通过」就是提交按钮
      if (c.verdict) return;
      // 隐藏备注：不通过 / 选了备注项时才出现；其余情况不占版面
      if (c.hidden && !noteNeed) return;
      h += '<div class="flow-card' + (locked ? ' locked' : '') + '">';
      h += '<div class="flow-card-head">'
        + `<span class="flow-card-title">${esc(s.step)}</span>`
        + `<span class="flow-no">${s.no} / ${steps.length}</span></div>`;
      if (s.memo) h += `<div class="flow-note">${esc(s.memo)}</div>`;
      h += '<div class="flow-ctrls' + (c.design ? ' v2-big' : '') + '">'
        + v2CtrlHtml(s, c, vals) + '</div>';
      if ((s.attachments || []).length) h += attHtmlV2(s);
      h += '</div>';
    });

    const miss = locked ? [] : v2Gate();
    h += '<div class="flow-submit"><span class="prog">'
      + (locked ? '🔒 流程未解锁'
        : miss.length ? '还差 ' + miss.length + ' 项：' + esc(miss.join('、'))
          : '填完了，选择结论提交')
      + '</span>'
      + (locked ? '<span class="why">未解锁，不能提交</span>'
        : miss.length ? '<span class="why">未完成，点「通过」会列出缺项</span>' : '')
      + `<button id="flowPass" class="btn pass"${locked || F.busy ? ' disabled' : ''}>`
      + `✓ 通过</button>`
      + `<button id="flowReject" class="btn reject"${locked || F.busy ? ' disabled' : ''}>`
      + `✗ 不通过</button></div>`;

    host.innerHTML = h;

    if (locked) {
      Array.prototype.forEach.call(
        host.querySelectorAll('.flow-card.locked input, .flow-card.locked select,'
          + ' .flow-card.locked textarea, .flow-card.locked button'),
        (el) => { el.disabled = true; });
    }
    bindV2(host);
  }

  function v2CtrlHtml(s, c, vals) {
    const nm = esc(c.name);
    const stdV = (F.data.std || {})[c.name];
    const hasStd = stdV !== undefined && stdV !== null && stdV !== '';
    let h = '';
    // 设计值提示放在输入组**之前**并放大 —— 现场先看设计值，再填实测值
    if (c.design) {
      // 「常量：1.5kg/株」把值单独放大显示，标签只留「常量」，避免同一串字出现两遍
      const lbl = c.design_const ? '常量' : c.design;
      h += '<div class="fs-design"><span class="k">设计值 · '
        + `${esc(lbl)}</span><b>${hasStd ? esc(stdV) : '—'}</b>`
        + (c.unit ? `<span class="u">${esc(c.unit)}</span>` : '')
        + '</div>';
    }
    if (c.type === '自动') {
      const v = vals[c.name];
      h += `<div class="fs-item auto"><div class="fs-auto-v" data-auto="${s.no}" data-f="${nm}">`
        + `${v == null || v === '' ? '—' : esc(v) + (c.unit || (String(c.formula || '').indexOf('%') >= 0 ? '%' : ''))}</div>`
        + `<div class="fs-hint">系统自动算：${esc(c.formula || c.arg || '')}</div></div>`;
      return h;
    }
    if (c.type === '下拉') {
      const opts = splitList(c.arg);
      const cur = String(vals[c.name] == null ? '' : vals[c.name]);
      h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span></div>
        <select data-s="${s.no}" data-f="${nm}">`;
      if (!cur) h += '<option value="" selected>请选择</option>';
      opts.forEach((o) => {
        h += `<option${cur === o ? ' selected' : ''}>${esc(o)}</option>`;
      });
      h += '</select></div>';
      return h;
    }
    if (c.type === '多选') {
      const opts = splitList(c.arg);
      const sel = Array.isArray(vals[c.name]) ? vals[c.name] : [];
      h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span>
        <span class="fs-hint">可多选</span></div><div class="fs-multi">`;
      opts.forEach((o) => {
        h += `<label class="fs-chk"><input type="checkbox" data-s="${s.no}" data-m="${nm}"`
          + ` data-v="${esc(o)}"${sel.indexOf(o) >= 0 ? ' checked' : ''}>${esc(o)}</label>`;
      });
      h += `</div><div class="fs-verdict hint" data-mcount="${s.no}">已选 ${sel.length} 项</div></div>`;
      return h;
    }
    if (c.type === '多行纯文本') {
      const need = c.required && !c.hidden ? '<span class="req">*</span>' : '';
      h += `<div class="fs-item"><div class="fs-label">${nm} ${c.hidden ? `<span class="req">*</span>
        <span class="fs-hint">结论=不通过 或 选了带备注的项时必填，≥2 个汉字</span>` : need}</div>
        <textarea data-s="${s.no}" data-f="${nm}" maxlength="500" rows="3"
          placeholder="按现场实际情况填写">${esc(vals[c.name] || '')}</textarea></div>`;
      return h;
    }
    if (c.type === '数值') {
      h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span>
        ${c.unit ? `<span class="fs-hint">单位：${esc(c.unit)}</span>` : ''}</div>
        <input type="number" inputmode="decimal" data-s="${s.no}" data-f="${nm}"
          placeholder="实测" value="${esc(vals[c.name] || '')}"></div>`;
      return h;
    }
    // 单行文本（含"文本"）
    h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span></div>
      <input type="text" data-s="${s.no}" data-f="${nm}" maxlength="60"
        placeholder="按实际测量填写" value="${esc(vals[c.name] || '')}"></div>`;
    return h;
  }

  function attHtmlV2(s) {
    const list = s.attachments || [];
    const got0 = F.v2att[s.no] || [];
    let h = '<div class="fs-att"><div class="fs-label">附件要求'
      + `<span class="fs-hint">Excel 原文「${esc(s.att_raw || '')}」</span></div><div class="fs-att-btns">`;
    list.forEach((a, i) => {
      const got = got0.find((x) => attKey(x.kind, x.label) === attKey(a.kind, a.label));
      const cnt = got ? (got.count || 0) : 0;
      const ico = a.kind === 'photo' ? '📷' : (a.kind === 'upload' ? '📎' : '📍');
      const verb = a.kind === 'photo' ? '拍照' : (a.kind === 'upload' ? '上传' : '打卡');
      h += `<button class="fs-att-btn${cnt >= (a.min || 0) ? ' ok' : ''}" data-s="${s.no}" data-att2="${i}">`
        + `${ico} ${verb} · ${esc(a.label)}`
        + (a.min ? ` <span class="fs-hint">至少 ${a.min} 份</span>` : '')
        + (cnt ? ` <span class="cnt">${cnt}</span>` : '') + '</button>';
    });
    h += '</div></div>';
    return h;
  }

  function bindV2(host) {
    on($('#flowSel'), 'change', () => {
      const v = $('#flowSel').value;
      if (!v || v === F.data.flow_sheet) return;
      saveDraft();
      open(F.wid, F.rowIdx, '', v);
    });

    Array.prototype.forEach.call(host.querySelectorAll('[data-s][data-f]'), (el) => {
      const no = +el.dataset.s, k = el.dataset.f;
      const s = v2Step(no);
      const c = s && (s.controls || []).find((x) => x.name === k);
      if (!c) return;
      const ev = (el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') ? 'change' : 'input';
      on(el, ev, () => {
        v2Vals(no)[k] = el.value;
        if ((c.note_on || []).length) { saveDraft(); render(); return; }   // 备注区跟着出现/消失
        v2AutoRefresh(no);
        saveDraft();
        refreshV2Submit();
      });
    });

    Array.prototype.forEach.call(host.querySelectorAll('[data-s][data-m]'), (el) => {
      on(el, 'change', () => {
        const no = +el.dataset.s, f = el.dataset.m, v = el.dataset.v;
        const vals = v2Vals(no);
        const arr = Array.isArray(vals[f]) ? vals[f] : (vals[f] = []);
        const i = arr.indexOf(v);
        if (el.checked && i < 0) arr.push(v);
        else if (!el.checked && i >= 0) arr.splice(i, 1);
        const cnt = host.querySelector(`[data-mcount="${no}"]`);
        if (cnt) cnt.textContent = '已选 ' + arr.length + ' 项';
        saveDraft();
        refreshV2Submit();
      });
    });

    Array.prototype.forEach.call(host.querySelectorAll('[data-s][data-att2]'), (el) => {
      on(el, 'click', () => {
        const no = +el.dataset.s, s = v2Step(no);
        const a = s && (s.attachments || [])[+el.dataset.att2];
        if (!a) return;
        if (!F.v2att[no]) F.v2att[no] = [];
        shootAtt(a, F.v2att[no], () => { saveDraft(); render(); });
      });
    });

    on($('#flowPass'), 'click', () => submitV2('通过'));
    on($('#flowReject'), 'click', () => submitV2('不通过'));
  }

  /** 自动字段重算（只改那一格，不整块重画，避免输入框失焦）。
   *  公式变量可能来自别的环节，所以任一处输入变化都要重算**全部**自动字段。 */
  function v2AutoRefresh() {
    const host = $('#flowPanel');
    if (!host) return;
    const flat = v2Flat();
    (F.data.steps || []).forEach((s) => {
      const vals = v2Vals(s.no);
      (s.controls || []).forEach((c) => {
        if (c.type !== '自动') return;
        const v = evalFormula(c.formula, flat);
        if (v != null) { vals[c.name] = v; flat[c.name] = v; }
        const box = host.querySelector(`[data-auto="${s.no}"][data-f="${c.name}"]`);
        if (box) {
          box.textContent = (v == null || v === '')
            ? '—' : v + (c.unit || (String(c.formula || '').indexOf('%') >= 0 ? '%' : ''));
        }
      });
    });
  }

  function refreshV2Submit() {
    const host = $('#flowPanel');
    if (!host) return;
    const bar = host.querySelector('.flow-submit');
    const pass = host.querySelector('#flowPass');
    const reject = host.querySelector('#flowReject');
    if (!bar || !pass || !reject) return;
    const locked = !!F.data.flow_locked;
    const miss = locked ? [] : v2Gate();
    bar.querySelector('.prog').textContent = locked
      ? '🔒 流程未解锁'
      : miss.length ? '还差 ' + miss.length + ' 项：' + miss.join('、') : '填完了，选择结论提交';
    pass.disabled = locked || F.busy;
    reject.disabled = locked || F.busy;
  }

  /** 底部「通过 / 不通过」= 提交：结论写进流程的「是否通过」控件，再走整条提交。
   *  不通过且备注 <2 汉字时先展开备注框并聚焦，不提交。 */
  async function submitV2(verdict) {
    if (F.busy) return;
    if (F.data.flow_locked) { toast('该流程尚未解锁，请先完成前置流程', true); return; }
    const vs = (F.data.steps || []).find((s) => (s.controls || []).some((c) => c.verdict));
    if (vs) {
      const vc = (vs.controls || []).find((c) => c.verdict);
      if (vc) v2Vals(vs.no)[vc.name] = verdict;
    }
    const miss = v2Gate();
    if (miss.length) {
      saveDraft();
      render();          // 选「不通过」后把隐藏的备注框画出来
      if (verdict === '不通过') {
        toast('不通过需填写备注（≥2 个汉字）');
        setTimeout(() => {
          const el2 = document.querySelector('#flowPanel .flow-card textarea');
          if (el2) el2.focus();
        }, 0);
      } else {
        toast('还有 ' + miss.length + ' 项未完成：' + miss.slice(0, 3).join('、'), true);
      }
      return;
    }
    const sheet = F.data.flow_sheet;
    F.busy = true;
    refreshV2Submit();
    try {
      const geo = await submitGeo();
      const steps = (F.data.steps || []).map((s) => ({
        no: s.no, values: F.v2val[s.no] || {}, att: F.v2att[s.no] || [],
      }));
      await api(`/api/workbooks/${F.wid}/flow/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ row: F.rowIdx, flow: sheet, steps: steps, geo: geo }),
      });
      clearDraft();
      toast('已提交「' + flowName(sheet) + '」·' + verdict);
      await open(F.wid, F.rowIdx, '', sheet);
    } catch (e) {
      toast('提交失败：' + e.message, true);
    } finally { F.busy = false; refreshV2Submit(); }
  }

  function ctrlsHtml(s) {
    let h = '';
    (s.controls || []).forEach((c) => {
      const nm = esc(c.name);
      // 验收结果统一由底部「结论区」渲染（见本函数末尾），避免出现两个下拉
      if (c.name === '验收结果') return;
      // 是否通过：两个大按钮，不通过必须写原因（业务员文档「结论问法」原文当问句）
      if (c.name === '是否通过') {
        const q = s.question || '是否通过';
        const cur = F.val['是否通过'] || '';
        h += `<div class="fs-item"><div class="fs-label">${esc(q)} <span class="req">*</span></div>
          <div class="flow-pass">
            <button class="ps ok${cur === '通过' ? ' cur' : ''}" data-pass="通过">✓ 通过</button>
            <button class="ps bad${cur === '不通过' ? ' cur' : ''}" data-pass="不通过">✗ 不通过</button>
          </div>`;
        if (cur === '不通过' && s.note_required) {
          h += `<div class="fs-label" style="margin-top:10px">不通过原因 <span class="req">*</span>
            <span class="fs-hint">必填：写清具体问题与位置，不能只写"不合格"</span></div>
            <textarea data-f="不通过原因" maxlength="500" rows="3"
              placeholder="如：回填深度不足约 20cm，含大量石块，位于小班东北角约 2 亩">${esc(F.val['不通过原因'] || '')}</textarea>`;
        }
        h += '</div>';
        return;
      }
      if (c.type === '自动') {
        // 「自动」= 系统带出、不可手填（如设计数量、验收人）
        let v = c.name === '验收人' ? (window.__supUser || '') : (F.val[c.name] || (F.data.std || {})[c.name] || '');
        h += `<div class="fs-item auto"><div class="fs-label">${nm}
          <span class="fs-hint">系统带出，不可手改</span></div>
          <div class="fs-auto-v">${esc(v || '—')}</div></div>`;
        return;
      }
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
          /* v0.32.3：placeholder 不再用裸标准值数字（灰色 60/60/50 被当成"已带过来填好"，
             提交却说没填）。占位文字用「实测·长/宽/深」（从子字段名去掉控件名得到），
             标准值只在下方提示行展示 */
          const dimName = k.replace(c.name, '') || '值';
          h += `<input type="number" inputmode="decimal" data-f="${esc(k)}" placeholder="实测·${esc(dimName)}" value="${esc(F.val[k] || '')}">`;
        });
        h += '</div><div class="fs-verdict" data-verdict="dim"></div></div>';
        return;
      }
      if (c.type === '下拉') {
        /* 选项一律走 optsFor —— 它统一处理「字面值 / data 枚举 / 不显示选项剪枝」。
           这里原来自己内联了一遍 splitList + dataEnum（只有多选走了 optsFor），
           导致 v0.36 的下拉剪枝只对多选生效、对下拉不生效（截图自测才发现）。 */
        const opts = optsFor(c);
        /* v0.32.3：同验收结果 —— 加「请选择」占位，未选时不得假装显示第一项 */
        h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span></div>
          <select data-f="${nm}"><option value=""${F.val[c.name] ? '' : ' selected'}>请选择</option>`
          + opts.map((o) => `<option${F.val[c.name] === o ? ' selected' : ''}>${esc(o)}</option>`).join('')
          + `</select>
          ${F.data.std[c.name] != null && F.data.std[c.name] !== ''
            ? `<div class="fs-hint">标准值 <b>${esc(F.data.std[c.name])}</b></div>` : ''}</div>`;
        return;
      }
      if (c.type === '多选') {
        const opts = optsFor(c);
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
        const need = F.val['病虫害情况'] === '严重' || c.required === true;
        h += `<div class="fs-item"><div class="fs-label">${nm} ${need ? '<span class="req">*</span>' : '<span class="fs-hint">选填</span>'}</div>
          <textarea data-f="${nm}" maxlength="500" rows="3" placeholder="纯文本，不带格式；≤500 字">${esc(F.val[c.name] || '')}</textarea>
          <div class="fs-hint">${esc(c.hint || '纯文本 ≤500 字')}</div></div>`;
        return;
      }
      if (c.type === '单行文本') {
        const req = c.required === true;
        h += `<div class="fs-item"><div class="fs-label">${nm} ${req ? '<span class="req">*</span>' : '<span class="fs-hint">选填</span>'}
          ${c.hint ? `<span class="fs-hint">${esc(c.hint)}</span>` : ''}</div>
          <input type="text" data-f="${nm}" maxlength="60" value="${esc(F.val[c.name] || '')}"></div>`;
        return;
      }
      if (c.type === '日期') {
        h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span>
          ${c.hint ? `<span class="fs-hint">${esc(c.hint)}</span>` : ''}</div>
          <input type="date" data-f="${nm}" value="${esc(F.val[c.name] || today())}"></div>`;
        return;
      }
      // 数值（含「系统自动算」的只读展示）—— arg 是字符串，查子串用 indexOf（C22，勿用 in）
      const auto = String(c.arg || '').indexOf('系统自动算') >= 0;
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
      /* 有「标准值来源」的数值：设计值 vs 现场实测，边填边判（业务员文档 ③核验项） */
      const stdV = (c.std_col && F.data.std) ? F.data.std[c.name] : '';
      const hasStd = stdV !== undefined && stdV !== null && stdV !== '';
      h += `<div class="fs-item"><div class="fs-label">${nm} <span class="req">*</span>
        <span class="fs-hint">${esc(c.arg || '')}</span></div>`;
      if (hasStd) {
        h += `<div class="fs-hint">设计值 <b>${esc(stdV)}</b>${c.std_col !== c.name ? `（取自 data「${esc(c.std_col)}」）` : ''}</div>`;
      }
      h += `<input type="number" inputmode="decimal" data-f="${nm}" value="${esc(F.val[c.name] || '')}">`;
      if (hasStd && c.cmp && c.cmp !== '不判定') {
        h += `<div class="fs-verdict" data-cmp="${esc(nm)}"></div>`;
      }
      h += '</div>';
    });
    /* 跨环节通用区：**验收结果 / 整改期限** 不在任何环节的「填报控件」列里
       （因子控件对照表说它们是「所有环节」通用），但又必填，所以在每个环节底部固定渲染。
       少了这一块，现场人员永远看不到验收结果下拉，提交永远被拦。
       v0.35：「仅记录 / 是否通过」的环节没有验收结果这个概念（s.need_result === false），
       不能再硬塞一个必填下拉，否则巡视永远提交不了。 */
    if (s.need_result !== false) {
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
    }
    return h;
  }

  /** 多选/下拉的选项：字面值，或 `data「列名」枚举`（取该工作簿该列去重值）。
   *  v0.36：③「不显示选项」给出的值从最终列表里剔除 —— 业务专家可以在不写脚本、
   *  不动数据的前提下，把"下游用不上/现场不该选"的选项直接剪掉。 */
  function optsFor(c) {
    const arg = String(c.arg || '');
    let list;
    if (arg.indexOf('枚举') >= 0 || arg.indexOf('data') >= 0) {
      list = dataEnum(c.name, arg).map(String);
    } else {
      list = splitList(arg);
    }
    const drop = c.drop || [];
    if (!drop.length) return list;
    const bad = {};
    drop.forEach((x) => { bad[String(x).trim()] = 1; });
    return list.filter((x) => !bad[String(x).trim()]);
  }

  function recStamp(r) {
    const v = r.values || {};
    const d = v['记录日期'] || v['巡护日期'] || v['验收日期'] || '';
    const who = r.operator || '';
    const s = [d, who].filter(Boolean).join(' · ');
    return s || (r.updated_at || '');
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

    // 是否通过：两个按钮，点了就重画（不通过要立刻出现原因框）
    Array.prototype.forEach.call(host.querySelectorAll('[data-pass]'), (el) => {
      on(el, 'click', () => {
        F.val['是否通过'] = el.dataset.pass;
        if (el.dataset.pass === '通过') F.val['不通过原因'] = '';
        saveDraft(); render();
      });
    });

    // 多次环节：新增一条（空白）/ 点历史记录编辑
    on($('#flowNewRec'), 'click', () => {
      F.recNo = 0; F.val = {}; F.att = []; saveDraft(); render();
    });
    Array.prototype.forEach.call(host.querySelectorAll('[data-rec]'), (el) => {
      on(el, 'click', () => {
        loadStepRecords(step().no, +(el.dataset.rec));
        saveDraft(); render();
      });
    });

    verdict();
  }

  /** 数值对照标准的判定文案 */
  function verdict() {
    const host = $('#flowPanel');
    if (!host) return;
    const s = step();
    if (!s) return;
    const box = host.querySelector('[data-verdict="dim"]');
    if (box) {
      const c = (s.controls || []).find((x) => x.type === '数值×3');
      if (c) {
        const subs = c.sub || [];
        const dims = String(F.data.std[c.name] || '').split(/[×xX*]/).map(Number);
        const act = subs.map((k) => num(F.val[k]));
        if (act.some((x) => x == null)) {
          box.className = 'fs-verdict hint';
          box.textContent = '三项填齐后自动对照标准值';
        } else {
          const pass = dims.length >= subs.length && act.every((a, i) => a >= dims[i]);
          box.className = 'fs-verdict ' + (pass ? 'ok' : 'bad');
          box.textContent = `${act.join('×')}　对照标准 ${dims.join('×')}`
            + (pass ? ' · 通过' : ' · 低于标准');
        }
      }
    }
    // v0.35：③核验项 声明了「合格判定」的数值框，边填边判（不用等提交才知道差多少）
    Array.prototype.forEach.call(host.querySelectorAll('[data-cmp]'), (el) => {
      const nm = el.dataset.cmp;
      const c = (s.controls || []).find((x) => x.name === nm);
      if (!c) return;
      const std = num(F.data.std[c.name]);
      const act = num(F.val[c.name]);
      if (std == null || act == null) {
        el.className = 'fs-verdict hint';
        el.textContent = '填了自动对照设计值 ' + (F.data.std[c.name] == null ? '—' : F.data.std[c.name]);
        return;
      }
      let pass = false, label = '';
      if (c.cmp === '实测≥标准') { pass = act >= std; label = act >= std ? '达标' : '低于设计值'; }
      else if (c.cmp === '实测≤标准') { pass = act <= std; label = act <= std ? '未超设计' : '超出设计值'; }
      else if (c.cmp === '实测=标准') { pass = Math.abs(act - std) < 0.005; label = pass ? '一致' : '与设计不一致'; }
      else if (c.cmp === '偏差≤5%') {
        /* 混交比例在 data 里存的是小数（0.2938），现场按百分比填（29.38）——
           两边量纲不一致会算出几百%的偏差，这里按"设计值≤1 且实测>1"归一为百分比 */
        let base = std;
        if (Math.abs(base) <= 1 && Math.abs(act) > 1) base = base * 100;
        pass = base !== 0 && Math.abs(act - base) / Math.abs(base) <= 0.05;
        label = pass ? '偏差在 5% 内' : '偏差超过 5%';
      }
      el.className = 'fs-verdict ' + (pass ? 'ok' : 'bad');
      el.textContent = `实测 ${act}　设计 ${std} · ${label}`;
    });
  }

  function refreshSubmit() {
    const host = $('#flowPanel');
    if (!host) return;
    const s = step(), miss = gate();
    const bar = host.querySelector('.flow-submit');
    if (!bar) return;
    const lock = !unlocked(F.cur);
    bar.querySelector('.prog').textContent = lock
      ? '🔒 本环节未解锁'
      : miss.length ? '还差 ' + miss.length + ' 项：' + miss.join('、') : '本环节可提交';
    const why = bar.querySelector('.why');
    const btn = bar.querySelector('#flowSubmit');
    if (btn) btn.disabled = lock || miss.length > 0 || F.busy;
    if (why) why.style.display = (lock || miss.length) ? '' : 'none';
  }

  /* ── 拍照 / 上传 / 打卡：复用 app.js 的通道 ── */
  async function shoot(i) {
    const s = step(), a = (s.attachments || [])[i];
    if (!a) return;
    return shootAtt(a, F.att, () => { saveDraft(); render(); });
  }

  /** 附件采集（老格式/ v2 共用）：list 是这一层的附件数组，done 由调用方决定怎么刷新 */
  async function shootAtt(a, list, done) {
    const key = attKey(a.kind, a.label);
    const rec = list.find((x) => attKey(x.kind, x.label) === key)
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
        const i = list.findIndex((x) => attKey(x.kind, x.label) === key);
        if (i >= 0) list.splice(i, 1);
        list.push(rec);
        done();
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
      const i = list.findIndex((x) => attKey(x.kind, x.label) === key);
      if (i >= 0) list.splice(i, 1);
      list.push(rec);
      done();
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
      const i = list.findIndex((x) => attKey(x.kind, x.label) === key);
      if (i >= 0) list.splice(i, 1);
      list.push(rec);
      done();
      toast((res.saved || '已拍照') + (res.sync || ''));
    };
    const pi = $('#photoInput');
    if (!pi) { toast('找不到拍照控件', true); return; }
    pi.click();
  }

  /* ── 提交 ── */
  /** 提交瞬间取一次定位（审计留痕，v0.32.5）。best-effort：3 秒拿不到/无权限 → null，
   *  绝不阻塞提交（打卡是 8 秒因为用户在等结果；这里只是留痕，不能拖慢提交）。 */
  function submitGeo() {
    return new Promise((res) => {
      if (!navigator.geolocation) return res(null);
      const t = setTimeout(() => res(null), 3000);
      navigator.geolocation.getCurrentPosition(
        (p) => { clearTimeout(t);
          res({ lat: p.coords.latitude.toFixed(6), lon: p.coords.longitude.toFixed(6),
                acc: Math.round(p.coords.accuracy || 0), t: new Date().toLocaleString('zh-CN') }); },
        () => { clearTimeout(t); res(null); },
        { enableHighAccuracy: true, timeout: 3000 });
    });
  }

  async function submit(s) {
    if (F.busy) return;
    const miss = gate();
    if (miss.length) { toast('还有 ' + miss.length + ' 项未完成', true); return; }
    if (!unlocked(F.cur)) { toast('请先完成前面的环节', true); return; }
    F.busy = true; refreshSubmit();
    try {
      const geo = await submitGeo();
      const payload = {
        row: F.rowIdx, step_no: s.no,
        entry: F.data.entry || '',     // 同小班有验收/巡视两条流程时，必须指明走哪条
        rec_no: F.recNo || 0,          // 多次环节：0 = 新增一条，>0 = 改这一条
        result: F.val['验收结果'] || '',
        att: F.att,
        geo: geo,
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
      // 回读一次拿服务端最终结果（实测对照设计会自动改写验收结果）。
      // **必须带 entry**：同一个小班可能同时命中验收/巡视两条流程，不带 entry
      // 会回读到另一条的数据；项目级 row=-1 不带 entry 更是直接匹配不到（v0.37 修）。
      F.data = await api(`/api/workbooks/${F.wid}/flow?row=${F.rowIdx}`
        + `&entry=${encodeURIComponent(F.data.entry || '')}`);
      clearDraft();
      toast(`已提交「${s.step}」`);
      if (s.multi) {
        // 多次环节：提交完停在原地，清成一张空白表，方便接着录下一条
        loadStepRecords(s.no, 0);
        toast(`已保存「${s.step}」第 ${(F.data.multi || {})[String(s.no)] ? Object.keys(F.data.multi[String(s.no)]).length : 1} 条`);
      } else if (F.cur < F.data.steps.length - 1) {
        F.cur += 1; loadStepRecords(step().no, 0);
      }
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