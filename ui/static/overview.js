/* Overview layout and explicitly labeled manual dashboard figures. */
function overview() {
  const s = state.summary;
  const overrides = s.manual_overrides || {};
  const stat = (key, label, computed, note, format) => {
    const manual = overrides[key] !== undefined && overrides[key] !== null;
    const value = manual ? overrides[key] : computed;
    const description = manual
      ? `<span class="manual-tag">手动填报</span><span class="computed-note">系统值 ${esc(format(computed))}</span>`
      : `系统计算 · ${note}`;
    return metric(label, esc(format(value)), description, true);
  };
  const accuracy = Object.entries(s.accuracy).map(([key, a]) =>
    `<div class="accuracy-row"><span>${fields[key]}</span><div class="bar"><i style="width:${a.rate || 0}%"></i></div><strong>${a.rate == null ? '—' : a.rate + '%'}<small>已复核 ${a.reviewed} 项</small></strong></div>`
  ).join('');
  const folders = state.collections.map(g =>
    `<tr><td><strong>${esc(g.path.split(/[\\/]/).pop())}</strong><small class="mono">${esc(g.path)}</small></td><td>${g.discovered}</td><td>${g.ready}</td><td>${g.cases}</td><td>${g.runs}</td><td>${g.conflicts ? `<span class="badge failed">${g.conflicts} 例结果不一致</span>` : '—'}</td><td>${g.pending_reviews ? `<span class="badge queued">${g.pending_reviews} 例待复核</span>` : '—'}</td><td><button class="link" data-collection="${esc(g.path)}">查看 →</button>${g.path === '未归入目录' ? '' : `<button class="link" data-batch="${esc(g.path)}" style="margin-left:12px">分析未处理</button>`}</td></tr>`
  ).join('');
  const percent = v => v == null ? '—' : `${Number(v).toLocaleString('zh-CN', {maximumFractionDigits: 1})}%`;
  const minutesInput = key => overrides[key] == null ? '' : esc(Number((overrides[key] / 60).toFixed(2)));
  const action = '<div class="toolbar" style="margin:0"><button class="secondary" id="batch-all">批量分析未处理</button><button class="primary" data-page="tasks">创建分析任务 <span>↗</span></button></div>';
  return head('OPERATIONS / SNAPSHOT', '运行概览', '按目录批次汇总，每次 vmcore 崩溃的多份报告分别核对。', action) +
    `<div class="metric-grid overview-primary">${metric('崩溃记录', s.cases, `其中 ${s.conflict_cases} 次结果不一致`, true)}${metric('目录批次', state.collections.filter(g => g.path !== '未归入目录').length, '扫描根目录下的一级子目录', true)}${metric('报告数量', s.report_count || 0, '已索引的 Markdown 报告', true)}${metric('累计任务', s.tasks, '包含历史导入记录', true)}</div>` +
    `<div class="overview-stat-heading"><div><h2>分析效率</h2><small>统计项可手动填报；手动值与系统值分别标识</small></div><button class="secondary" id="dashboard-edit" type="button">编辑统计</button></div>` +
    `<div class="metric-grid overview-operational">${stat('success_rate', '任务成功率', s.success_rate, `已完成 ${s.completed} 项`, percent)}${stat('avg_analysis_seconds', '平均 AI 耗时', s.avg_analysis_seconds, '成功且有耗时的任务', dur)}${stat('avg_human_seconds', '平均人工耗时', s.avg_human_seconds, '有录入耗时的复核', dur)}${stat('avg_saved_seconds', '平均节省时间', s.avg_saved_seconds, `可配对 ${s.savings_samples} 例`, dur)}</div>` +
    `<div class="section"><div class="section-heading"><h2>目录批次汇总</h2><small>脚本模式只需 vmcore 稳定；Agent 模式还需 dmesg 与符号匹配</small></div><div class="table-wrap"><table><thead><tr><th>目录</th><th>发现 vmcore</th><th>文件稳定</th><th>崩溃</th><th>运行</th><th>分歧</th><th>待复核</th><th></th></tr></thead><tbody>${folders}</tbody></table>${folders ? '' : '<div class="empty"><strong>尚未发现目录批次</strong>在设置中配置 /data/vmcore 扫描根目录。</div>'}</div></div>` +
    `<div class="two-col"><div class="section"><div class="section-heading"><h2>逐项准确率</h2><small>仅“正确”计入准确；待确认不入分母</small></div><div class="section-body">${accuracy}</div></div><div class="section"><div class="section-heading"><h2>常见故障类型</h2><small>按每次 vmcore 崩溃去重；已复核的采用人工故障类型</small></div><div class="section-body fault-list">${s.fault_types.length ? s.fault_types.map(([n, count]) => `<div class="fault-item"><span title="${esc(n)}">${esc(n)}</span><b>${count}</b></div>`).join('') : '<div class="empty">暂无可识别的故障类型</div>'}</div></div></div>` +
    `<div class="section"><div class="section-heading"><h2>最近任务</h2><button class="link" data-page="tasks">查看全部 →</button></div></div>${table(state.tasks.slice(0, 5))}` +
    `<dialog id="dashboard-dialog" class="task-dialog dashboard-dialog"><div class="dialog-head"><div><div class="eyebrow">DASHBOARD / MANUAL VALUES</div><h2>编辑概览统计</h2></div><button class="icon-button" id="dashboard-close" type="button" aria-label="关闭">×</button></div><form id="dashboard-form" class="dialog-body"><p class="hint">留空即采用系统计算值。手动数值会在卡片上标明，并保存到本地数据目录。</p><div class="form-grid"><div class="field"><label for="override-success">任务成功率（%）</label><input id="override-success" name="success_rate" type="number" min="0" max="100" step="0.1" value="${overrides.success_rate == null ? '' : esc(overrides.success_rate)}"><small>系统值：${percent(s.success_rate)}</small></div><div class="field"><label for="override-ai">平均 AI 耗时（分钟）</label><input id="override-ai" name="avg_analysis_seconds" type="number" min="0" step="0.01" value="${minutesInput('avg_analysis_seconds')}"><small>系统值：${dur(s.avg_analysis_seconds)}</small></div><div class="field"><label for="override-human">平均人工耗时（分钟）</label><input id="override-human" name="avg_human_seconds" type="number" min="0" step="0.01" value="${minutesInput('avg_human_seconds')}"><small>系统值：${dur(s.avg_human_seconds)}</small></div><div class="field"><label for="override-saved">平均节省时间（分钟）</label><input id="override-saved" name="avg_saved_seconds" type="number" step="0.01" value="${minutesInput('avg_saved_seconds')}"><small>系统值：${dur(s.avg_saved_seconds)}；负值表示 AI 更慢</small></div></div><div class="form-actions"><button class="secondary" id="dashboard-clear" type="button">全部恢复自动</button><button class="primary" type="submit">保存统计</button></div></form></dialog>`;
}

document.addEventListener('click', e => {
  const button = e.target.closest('button');
  if (!button) return;
  if (button.id === 'dashboard-edit') document.querySelector('#dashboard-dialog')?.showModal();
  if (button.id === 'dashboard-close') document.querySelector('#dashboard-dialog')?.close();
  if (button.id === 'dashboard-clear') document.querySelectorAll('#dashboard-form input').forEach(input => { input.value = ''; });
});

document.addEventListener('submit', async e => {
  if (e.target.id !== 'dashboard-form') return;
  e.preventDefault();
  e.stopImmediatePropagation();
  const form = new FormData(e.target);
  const payload = {};
  for (const key of ['success_rate', 'avg_analysis_seconds', 'avg_human_seconds', 'avg_saved_seconds']) {
    const raw = String(form.get(key) || '').trim();
    payload[key] = raw === '' ? null : Number(raw) * (key === 'success_rate' ? 1 : 60);
  }
  try {
    await api('/dashboard-overrides', {method: 'PUT', body: JSON.stringify(payload)});
    document.querySelector('#dashboard-dialog')?.close();
    toast('概览统计已保存');
    await load();
  } catch (error) {
    toast(error.message);
  }
}, true);
