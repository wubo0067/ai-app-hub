// One crash is one vmcore. A host may have many crashes; each crash may have many reports.
names.cases = '报告对比与复核';
names.reviews = names.cases;
const originalWorkflowTaskDetail = taskDetail;
taskDetail = function(task) {
  return originalWorkflowTaskDetail(task).replace('报告主机', '主机名').replace('<div class="detail-grid">',
    `<div class="detail-grid">${detailItem('全局崩溃编码', task.incident_code || '待生成')}`);
};

function reportGroups(rows) {
  const hosts = new Map();
  [...rows].sort((a, b) => b.modified_at - a.modified_at).forEach(row => {
    const host = row.hostname && row.hostname !== '未知' ? row.hostname : '未知主机';
    if (!hosts.has(host)) hosts.set(host, {latest: row.modified_at, incidents: new Map()});
    const group = hosts.get(host);
    group.latest = Math.max(group.latest, row.modified_at);
    const code = row.incident_code || '未编码';
    if (!group.incidents.has(code)) group.incidents.set(code, []);
    group.incidents.get(code).push(row);
  });
  return [...hosts].sort((a, b) => b[1].latest - a[1].latest);
}

reportsTable = function(rows) {
  if (!rows.length) return '<div class="empty">尚无匹配的报告</div><div id="source-reader"></div>';
  const html = reportGroups(rows).map(([host, group]) => {
    const incidents = [...group.incidents].sort((a, b) => b[1][0].modified_at - a[1][0].modified_at);
    return `<details class="report-host" open><summary><strong>${esc(host)}</strong><span>${incidents.length} 次崩溃 · ${incidents.reduce((sum, [, list]) => sum + list.length, 0)} 份报告</span></summary>${incidents.map(([code, list]) => {
      const first = list[0];
      return `<details class="report-incident" open><summary><strong class="mono">${esc(code)}</strong><span>${esc(first.vmcore_path || '未关联 vmcore')} · ${list.length} 份 · 最近 ${fmt(first.modified_at / 1000000)}</span></summary><div class="table-wrap"><table class="task-table"><thead><tr><th>报告</th><th>OS / 内核</th><th>故障时间</th><th>Token</th><th>修改时间</th><th>操作</th></tr></thead><tbody>${list.map(row => `<tr><td><strong>${esc(row.filename)}</strong><small class="mono" title="${esc(row.path)}">${esc(row.path)}</small></td><td>${esc(row.os_name)}<small>${esc(row.kernel)}</small></td><td>${esc(row.dump_time)}</td><td>${row.token_usage == null ? '未知' : Number(row.token_usage).toLocaleString()}</td><td>${fmt(row.modified_at / 1000000)}</td><td class="row-actions"><button class="link" data-source-report="${esc(row.id)}">阅读</button>${row.case_id ? `<button class="link" data-case="${esc(row.case_id)}">对比</button>` : ''}<button class="icon-button danger-link" data-delete-report="${esc(row.id)}" title="删除报告" aria-label="删除报告">✕</button></td></tr>`).join('')}</tbody></table></div></details>`;
    }).join('')}</details>`;
  }).join('');
  return html + '<div id="source-reader"></div>';
};

function reportMatches(caseRecord) {
  return (state.reports || []).filter(row => row.case_id === caseRecord.id).sort((a, b) => b.modified_at - a.modified_at);
}
function groupedCaseCards(cases) {
  if (!cases.length) return '<div class="empty">没有匹配的报告；可调整筛选条件。</div>';
  const hosts = new Map();
  cases.forEach(record => {
    const host = record.hostname && record.hostname !== '未知' ? record.hostname : '未知主机';
    if (!hosts.has(host)) hosts.set(host, []);
    hosts.get(host).push(record);
  });
  return [...hosts].map(([host, items]) => `<details class="report-host" open><summary><strong>${esc(host)}</strong><span>${items.length} 次崩溃</span></summary><div class="case-list">${items.map(record => {
    const reports = reportMatches(record);
    return `<div class="case-card"><div><h3><span class="mono">${esc(record.incident_code || record.id)}</span> <span class="badge ${record.review_state === '已复核' ? 'success' : 'queued'}">${esc(record.review_state)}</span>${record.has_conflict ? ' <span class="badge failed">结果不一致</span>' : ''}</h3><p class="mono" title="${esc(record.vmcore_path || '')}">${esc(record.vmcore_path || '未关联 vmcore')}</p><div class="case-meta"><span>故障时间 ${esc(record.dump_time || '未知')}</span><span>内核 ${esc(record.kernel || '未知')}</span><span>报告 ${reports.length} 份</span><span>结果版本 ${record.variant_count} 种</span></div></div><div class="actions"><button class="link" data-case="${esc(record.id)}">对比并复核 →</button></div></div>`;
  }).join('')}</div></details>`).join('');
}
caseCards = groupedCaseCards;
casesPage = function() {
  const folders = [...new Set(state.cases.map(record => record.collection))].sort();
  return head('REPORTS / COMPARE', '报告对比与复核', '按主机和每次 vmcore 崩溃聚合报告；比较全部结论并填写人工最终判断。', '<button class="secondary" id="import-open">导入 Markdown</button>') +
    `<div id="import-area" class="section" hidden><div class="section-heading"><h2>导入历史报告</h2></div><div class="section-body"><div class="field"><label>选择 Markdown 文件（最大 10 MB）</label><input id="import-file" type="file" accept=".md,.markdown,text/markdown"></div><button class="primary" id="import-submit">导入报告</button></div></div>` +
    `<div class="toolbar"><input id="case-search" placeholder="搜索主机、崩溃编码、vmcore、内核或结论"><select id="collection-filter"><option value="">全部目录批次</option>${folders.map(path => `<option value="${esc(path)}" ${state.collectionFilter === path ? 'selected' : ''}>${esc(path.split(/[\\/]/).pop())}</option>`).join('')}</select><select id="case-filter"><option value="">全部报告</option><option value="conflict">结果不一致</option><option value="pending">待复核 / 新报告</option><option value="reviewed">已复核</option></select></div><div id="case-list">${groupedCaseCards(state.cases.filter(record => !state.collectionFilter || record.collection === state.collectionFilter))}</div>`;
};
filteredCases = function() {
  const q = ($('#case-search')?.value || '').toLowerCase();
  const filter = $('#case-filter')?.value || '';
  const collection = $('#collection-filter')?.value || '';
  return state.cases.filter(record => [record.incident_code, record.hostname, record.vmcore_path, record.kernel, record.ai?.fault_type, record.ai?.root_cause, record.collection].join(' ').toLowerCase().includes(q) &&
    (!collection || record.collection === collection) &&
    (!filter || (filter === 'conflict' ? record.has_conflict : filter === 'reviewed' ? record.review_state === '已复核' : record.review_state !== '已复核')));
};

runComparison = function(record) {
  const reports = reportMatches(record);
  if (!reports.length) return '<div class="empty">暂无可比较的报告</div>';
  return `<div class="table-wrap"><table class="diff-table"><thead><tr><th>对比项目</th>${reports.map((row, i) => `<th><button class="link" data-source-report="${esc(row.id)}">报告 ${i + 1} · ${esc(row.filename)}</button><small>${fmt(row.modified_at / 1000000)}</small></th>`).join('')}</tr></thead><tbody>${Object.entries(fields).map(([key, label]) => {
    const values = reports.map(row => row.ai?.[key] || '未知');
    const different = new Set(values).size > 1;
    return `<tr class="${different ? 'different' : ''}"><td><strong>${label}</strong>${different ? '<small>结果不同</small>' : ''}</td>${values.map(value => `<td>${esc(value)}</td>`).join('')}</tr>`;
  }).join('')}</tbody></table></div>`;
};

caseDetail = function(record) {
  const reports = reportMatches(record);
  const old = state.reviews.find(review => review.case_id === record.id) || {};
  const chosen = reports.find(row => (row.task_id || 'report:' + row.id) === old.task_id) || reports[0];
  const ai = chosen?.ai || {};
  return head('REPORT / ' + esc(record.incident_code || record.id), '报告对比与复核', `${esc(record.hostname || '未知主机')} · ${esc(record.incident_code || record.id)}`, '<button class="secondary" data-page="cases">← 返回报告列表</button>') +
    `<div class="section"><div class="section-heading"><h2>崩溃信息</h2><span class="badge ${record.review_state === '已复核' ? 'success' : 'queued'}">${esc(record.review_state)}</span></div><div class="section-body"><div class="detail-grid">${detailItem('全局崩溃编码', record.incident_code || record.id)}${detailItem('主机名', record.hostname)}${detailItem('vmcore 绝对路径', record.vmcore_path)}${detailItem('故障时间', record.dump_time)}${detailItem('内核版本', record.kernel)}${detailItem('报告数量', String(reports.length))}</div></div></div>` +
    `${record.has_conflict ? `<div class="callout">本次崩溃的报告结论存在差异，请结合原文和人工分析确认。</div>` : ''}` +
    `<div class="section"><div class="section-heading"><h2>全部报告结果对比</h2><small>每列一份报告；点击表头阅读原文</small></div>${runComparison(record)}</div><div id="source-reader"></div>` +
    `<form id="review-form" data-id="${esc(record.id)}"><div class="section"><div class="section-heading"><h2>人工复核与最终结论</h2><small>评分同步至概览准确率；故障类型优先采用人工确认值</small></div><div class="section-body"><div class="field" style="margin-bottom:17px"><label>评分基准报告</label><select id="review-run" name="selected_task_id">${reports.map(row => {
      const id = row.task_id || 'report:' + row.id;
      return `<option value="${esc(id)}" ${chosen === row ? 'selected' : ''}>${esc(row.filename)} · ${fmt(row.modified_at / 1000000)}</option>`;
    }).join('')}</select></div><div class="compare-grid"><div class="compare-head">项目 / AI 结论</div><div class="compare-head">人工结论</div><div class="compare-head">判断</div>${Object.entries(fields).map(([key, label]) => `<div class="compare-row"><div class="compare-cell"><label>${label}</label><span data-ai-field="${key}">${esc(ai[key] || '未知')}</span></div><div class="compare-cell"><textarea name="human_${key}" placeholder="填写人工结论">${esc((old.human || {})[key] || '')}</textarea></div><div class="compare-cell"><select name="verdict_${key}">${['待确认', '正确', '部分正确', '错误'].map(value => `<option ${((old.verdicts || {})[key] || '待确认') === value ? 'selected' : ''}>${value}</option>`).join('')}</select></div></div>`).join('')}</div></div></div><div class="section"><div class="section-heading"><h2>最终确认</h2></div><div class="section-body"><div class="form-grid"><div class="field wide"><label>人工最终确认的崩溃原因</label><textarea name="final_cause" required placeholder="写明最终根因和判断依据">${esc(old.final_cause || '')}</textarea></div><div class="field"><label>人工分析耗时（分钟）</label><input name="human_minutes" type="number" min="0" step="0.1" value="${old.human_seconds == null ? '' : old.human_seconds / 60}"></div><div class="field wide"><label>复核备注</label><textarea name="notes">${esc(old.notes || '')}</textarea></div></div><div class="form-actions"><button class="primary" type="submit" ${reports.length ? '' : 'disabled'}>保存人工复核 ↗</button></div></div></div></form>`;
};

const originalWorkflowRender = render;
render = async function() {
  if (state.page === 'reviews') state.page = 'cases';
  if (state.page === 'cases' && !state.task) {
    try { state.reports = await api('/reports'); } catch (error) { toast(error.message); state.reports = []; }
  }
  await originalWorkflowRender();
};

document.addEventListener('click', async event => {
  const button = event.target.closest('button[data-delete-report]');
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const row = (state.reports || []).find(report => report.id === button.dataset.deleteReport);
  if (!row || !confirm(`确定删除报告“${row.filename}”？关联运行记录会保留，已有人工复核需重新确认。`)) return;
  try {
    await api('/reports/' + encodeURIComponent(row.id), {method: 'DELETE'});
    toast('报告已删除');
    await load();
  } catch (error) { toast(error.message); }
}, true);

document.addEventListener('submit', async event => {
  if (event.target.id !== 'review-form') return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const form = new FormData(event.target);
  const human = {}, verdicts = {};
  Object.keys(fields).forEach(key => {
    human[key] = String(form.get('human_' + key) || '').trim();
    verdicts[key] = String(form.get('verdict_' + key) || '待确认');
  });
  const minutes = form.get('human_minutes');
  try {
    await api('/cases/' + event.target.dataset.id + '/review', {method: 'PUT', body: JSON.stringify({
      selected_task_id: form.get('selected_task_id'), human, verdicts,
      final_cause: String(form.get('final_cause') || '').trim(),
      human_seconds: minutes === '' ? null : Math.round(Number(minutes) * 60),
      notes: String(form.get('notes') || '').trim()
    })});
    toast('人工复核已保存');
    await load();
  } catch (error) { toast(error.message); }
}, true);
