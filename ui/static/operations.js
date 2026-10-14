// Inventory and queue screens. Loaded after the base application.
let inventoryStream = null;
let inventoryEpoch = 0;
let inventoryChecking = false;
const inventoryColumns = ['VMCORE / 父目录','目录时间','主机名','生成时间','OS','内核','VMLINUX','状态 / 次数','Token / 耗时','操作'];
const inventoryWidthKey = 'vmcore-inventory-widths-v1';
function inventoryWidths() {
  try { return JSON.parse(localStorage.getItem(inventoryWidthKey)) || {}; } catch { return {}; }
}
function duplicatePaths(items) {
  const groups = new Map();
  items.forEach(item => { if (item.fingerprint) groups.set(item.fingerprint, [...(groups.get(item.fingerprint)||[]), item.vmcore_path]); });
  return new Map([...groups.values()].filter(paths => paths.length > 1).flatMap(paths => paths.map(path => [path, paths.length])));
}

function inventoryActions(item, runs, active) {
  const path = esc(item.vmcore_path);
  const latest = runs[0];
  const start = `<button class="icon-button" title="开始任务" aria-label="开始任务" data-action="start" data-path="${path}" data-vmlinux="${esc(item.vmlinux_path||'')}" ${item.ready && !active ? '' : 'disabled'}>▶</button>`;
  const stop = active ? `<button class="icon-button danger-link" title="停止任务" aria-label="停止任务" data-action="stop" data-id="${esc(active.id)}">■</button>` : '';
  const retry = latest ? `<button class="icon-button" title="重新分析" aria-label="重新分析" data-retry="${esc(latest.id)}" ${item.ready ? '' : 'disabled'}>↻</button>` : '';
  return `${start}${stop}${retry}<button class="icon-button" title="任务详情" aria-label="任务详情" data-item-detail="${path}">ⓘ</button>`;
}

function inventoryTable(items) {
  const widths = inventoryWidths();
  const duplicates = duplicatePaths(state.discovered||[]);
  const sizes = inventoryColumns.map((_,i) => Number(widths[i])||[320,145,130,160,145,180,190,155,145,140][i]);
  return `<div class="section"><div class="table-wrap"><table class="task-table inventory-table" style="width:${sizes.reduce((a,b)=>a+b,0)}px"><colgroup>${sizes.map(size => `<col style="width:${size}px">`).join('')}</colgroup><thead><tr>${inventoryColumns.map((name,i) => `<th>${name}<span class="column-resize" data-column="${i}" title="拖动调整列宽" role="separator" aria-label="调整${name}列宽"></span></th>`).join('')}</tr></thead><tbody>${items.map(item => {
    const runs = state.tasks.filter(t => t.vmcore_path === item.vmcore_path && !t.imported).sort((a,b) => b.created_at.localeCompare(a.created_at));
    const active = runs.find(t => ['queued','running','paused'].includes(t.status));
    const latest = runs[0];
    const c = state.cases.find(c => c.vmcore_path === item.vmcore_path) || {};
    const value = (a,b) => a && a !== '未知' ? a : b || '未知';
    const tokens = runs.map(t => t.token_usage).filter(v => v != null).reduce((a,b) => a + Number(b), 0);
    const seconds = runs.reduce((a,t) => a + (t.duration_seconds || 0), 0);
    return `<tr><td><strong class="mono">${esc(item.vmcore_path.split(/[\\/]/).slice(-2).join('/'))}</strong>${duplicates.has(item.vmcore_path)?` <span class="badge queued" title="文件大小与三处抽样内容一致，建议人工核对">疑似重复 · ${duplicates.get(item.vmcore_path)} 份</span>`:''}<small class="mono" title="${esc(item.vmcore_path)}">${esc(item.vmcore_path)}</small>${c.incident_code?`<small class="mono">${esc(c.incident_code)}</small>`:''}</td><td>${fmt(item.folder_created_at*1000)}</td><td>${esc(value(item.hostname_hint,c.hostname))}</td><td>${esc(value(item.dump_time_hint,c.dump_time))}</td><td>${esc(value(item.os_name_hint,c.os_name))}</td><td>${esc(value(item.kernel_hint,c.kernel))}</td><td class="path-cell" title="${esc(item.vmlinux_path||'未匹配')}">${esc(item.vmlinux_path||'未匹配')}</td><td>${latest ? badge(latest.status) : '<span class="badge unknown">未分析</span>'}<small>${runs.length} 次 · ${item.ready ? '文件就绪' : '文件待稳定'}</small></td><td>${tokens ? tokens.toLocaleString() : '未知'}<small>${seconds ? dur(seconds) : '未知'}</small></td><td class="row-actions">${inventoryActions(item,runs,active)}</td></tr>`;
  }).join('')}</tbody></table></div>${items.length ? '' : '<div class="empty">扫描中，尚未发现符合条件的 vmcore</div>'}</div>`;
}

function tasksPage() {
  setTimeout(checkNewInventory, 0);
  const existing = state.discovered||[];
  return head('VMCORE / INVENTORY','任务','复用已有扫描结果；自动检查新增文件，点击重新扫描才完整扫描。','<div class="toolbar"><button class="primary" id="create-task-open">创建任务 ＋</button><button class="secondary" id="scan-refresh">重新扫描 ↻</button></div>') +
    `<div id="scan-progress" class="scan-progress" role="status" aria-live="polite"><span class="scan-spinner"></span><div><strong>读取扫描记录…</strong><small>已有结果会保留在下方</small></div></div>`+
    `<div class="toolbar"><input id="inventory-search" placeholder="搜索目录、主机、OS、内核"><select id="inventory-status"><option value="">全部状态</option><option value="unanalyzed">未分析</option>${Object.entries(statuses).map(([k,v]) => `<option value="${k}">${v}</option>`).join('')}</select><span class="hint" id="inventory-count">共 ${existing.length} 个 vmcore</span></div><div id="task-table">${inventoryTable(existing)}</div>`+
    `<dialog id="create-task-dialog" class="task-dialog"><form id="manual-task-form"><h2>创建分析任务</h2><p class="hint">输入 vmcore 文件或其父目录的绝对路径。</p><div class="field"><label>vmcore 路径</label><input name="vmcore_path" placeholder="/data/vmcore/127.0.0.1/file" required></div>${state.settings.execution_mode==='agent'?'<div class="field"><label>dmesg 路径</label><input name="vmcore_dmesg_path" required></div><div class="field"><label>vmlinux 路径</label><input name="vmlinux_path" required></div>':''}<div class="form-actions"><button type="button" class="secondary" id="create-task-close">取消</button><button type="submit" class="primary">加入队列</button></div></form></dialog>`+
    `<dialog id="inventory-detail" class="task-dialog"><div class="dialog-body" id="inventory-detail-body"></div></dialog>`;
}

function filteredInventory() {
  const q = ($('#inventory-search')?.value || '').toLowerCase();
  const filter = $('#inventory-status')?.value || '';
  return (state.discovered||[]).filter(item => {
    const latest = state.tasks.filter(t => t.vmcore_path === item.vmcore_path).sort((a,b) => b.created_at.localeCompare(a.created_at))[0];
    const matched = !filter || (filter === 'unanalyzed' ? !latest : latest?.status === filter);
    return matched && [item.vmcore_path,item.hostname_hint,item.os_name_hint,item.kernel_hint,item.vmlinux_path].join(' ').toLowerCase().includes(q);
  });
}

function paintInventory() {
  const filtered = filteredInventory();
  const table = $('#task-table');
  if (table) table.innerHTML = inventoryTable(filtered);
  const count = $('#inventory-count');
  if (count) count.textContent = `共 ${(state.discovered||[]).length} 个 vmcore · 按父目录时间倒序`;
}

function scanProgress(folders, current, done, error, found=0) {
  const el = $('#scan-progress');
  if (!el) return;
  el.classList.toggle('complete', done && !error);
  el.classList.toggle('scan-error', !!error);
  el.innerHTML = `<span class="scan-spinner" aria-hidden="true"></span><div><strong>${error ? '扫描失败' : done ? '扫描完成' : '正在扫描目录…'} · ${folders.length} 个目录 · ${found} 个 vmcore</strong><small>${esc(error || current || '等待目录')}</small><div class="scan-trail">${folders.slice(-4).reverse().map(path => `<span title="${esc(path)}">${esc(path)}</span>`).join('')}</div></div>`;
}

function incrementalProgress(title, detail, active=false, error=false) {
  const el = $('#scan-progress');
  if (!el) return;
  el.classList.toggle('complete', !active && !error);
  el.classList.toggle('scan-error', error);
  el.innerHTML = `<span class="scan-spinner" aria-hidden="true"></span><div><strong>${esc(title)}</strong><small>${esc(detail)}</small></div>`;
}

async function checkNewInventory() {
  if (state.page !== 'tasks' || state.task || !$('#scan-progress') || inventoryStream || inventoryChecking) return;
  inventoryChecking = true;
  const epoch = ++inventoryEpoch;
  try {
    const cached = await api('/discover/cache');
    if (epoch !== inventoryEpoch || state.page !== 'tasks' || state.task) return;
    if (JSON.stringify(state.discovered||[]) !== JSON.stringify(cached.items)) {
      state.discovered = cached.items;
      paintInventory();
    }
    if (!cached.initialized) {
      incrementalProgress('尚无扫描记录','点击“重新扫描”建立首次扫描结果');
      return;
    }
    incrementalProgress('检查新增或变化的 VMCORE…','已有且未变化的文件直接复用扫描记录',true);
    const update = await api('/discover/incremental',{method:'POST'});
    if (epoch !== inventoryEpoch || state.page !== 'tasks' || state.task) return;
    if (JSON.stringify(state.discovered||[]) !== JSON.stringify(update.items)) {
      state.discovered = update.items;
      paintInventory();
    }
    incrementalProgress('新增文件检查完成',`本次重新读取 ${update.changed} 个新增或变化的 VMCORE`);
  } catch (error) {
    if (epoch === inventoryEpoch) incrementalProgress('新增文件检查失败',error.message,false,true);
  } finally {
    inventoryChecking = false;
  }
}

function startInventoryScan() {
  if (state.page !== 'tasks' || state.task || !$('#scan-progress')) return;
  ++inventoryEpoch;
  if (inventoryStream) inventoryStream.close();
  state.scanning = true;
  const folders = [];
  const found = new Map();
  scanProgress(folders,'连接扫描服务',false, null, 0);
  const stream = new EventSource('/api/discover/stream');
  inventoryStream = stream;
  stream.onmessage = event => {
    if (inventoryStream !== stream) return;
    const data = JSON.parse(event.data);
    if (data.type === 'directory') {
      folders.push(data.path);
      scanProgress(folders,data.path,false,null,found.size);
    } else if (data.type === 'vmcore') {
      found.set(data.item.vmcore_path, data.item);
      scanProgress(folders,data.item.vmcore_path,false,null,found.size);
    } else {
      stream.close();
      inventoryStream = null;
      state.scanning = false;
      if (data.type === 'complete') {
        const previous = JSON.stringify(state.discovered||[]);
        state.discovered = [...found.values()].sort((a,b) => b.folder_created_at-a.folder_created_at || b.vmcore_path.localeCompare(a.vmcore_path));
        if (JSON.stringify(state.discovered) !== previous) paintInventory();
      }
      scanProgress(folders, data.type === 'complete' ? '全部目录已检查' : '', true, data.type === 'error' ? data.message : null, found.size);
    }
  };
  stream.onerror = () => {
    if (inventoryStream !== stream || !state.scanning) return;
    stream.close();
    inventoryStream = null;
    state.scanning = false;
    scanProgress(folders,'',true,'扫描连接中断，请重新扫描',found.size);
  };
}

function queuePage() {
  const active = state.tasks.filter(t => ['queued','running','paused'].includes(t.status)).sort((a,b) =>
    (a.status==='running'?-1:b.status==='running'?1:0) ||
    (b.priority||0)-(a.priority||0) || a.created_at.localeCompare(b.created_at));
  const history = state.tasks.filter(t => !['queued','running','paused'].includes(t.status)).sort((a,b) => b.created_at.localeCompare(a.created_at));
  return head('QUEUE / SERIAL','任务管理','每次只执行一项；这里跟踪排队、进度、Token、耗时与错误。')+
    `<div class="queue-summary"><span><b>${active.filter(t=>t.status==='running').length}</b> 运行中</span><span><b>${active.filter(t=>t.status==='queued').length}</b> 排队中</span><span><b>${active.filter(t=>t.status==='paused').length}</b> 已暂停</span></div>`+
    `<div class="queue-label"><h2>当前任务</h2><small>按优先级和加入时间排序</small></div>${table(active)}`+
    `<div class="queue-label"><h2>历史运行</h2><small>${history.length} 条记录</small></div>${table(history)}`;
}

// Enhance every data table, including tables rendered after navigation or API calls.
const tableWidthPrefix = 'vmcore-table-widths-v1:';
function tableWidthKey(table, headers) {
  if (table.classList.contains('inventory-table')) return inventoryWidthKey;
  if (table.classList.contains('diff-table')) return `${tableWidthPrefix}comparison-${headers.length}`;
  return tableWidthPrefix + headers.map(th => th.textContent.trim()).join('|');
}
function storedTableWidths(key) {
  try { return JSON.parse(localStorage.getItem(key)) || {}; } catch { return {}; }
}
function enhanceTables() {
  document.querySelectorAll('#content table').forEach(table => {
    if (table.dataset.resizable === '1') return;
    const headers = [...table.querySelectorAll('thead tr:first-child > th')];
    if (!headers.length) return;
    const key = tableWidthKey(table, headers);
    const saved = storedTableWidths(key);
    const existing = [...table.querySelectorAll(':scope > colgroup > col')];
    const measured = headers.map((th, i) => Math.max(75, Math.ceil(existing[i]?.getBoundingClientRect().width || th.getBoundingClientRect().width || 120)));
    const widths = headers.map((_, i) => Math.max(75, Number(saved[i]) || measured[i]));
    let colgroup = table.querySelector(':scope > colgroup');
    if (!colgroup) {
      colgroup = document.createElement('colgroup');
      table.prepend(colgroup);
    }
    if (colgroup.children.length !== headers.length) {
      colgroup.replaceChildren(...widths.map(() => document.createElement('col')));
    }
    [...colgroup.children].forEach((col, i) => { col.style.width = `${widths[i]}px`; });
    table.style.width = `${widths.reduce((sum, width) => sum + width, 0)}px`;
    table.classList.add('resizable-table');
    table.dataset.tableWidthKey = key;
    table.dataset.resizable = '1';
    headers.forEach((th, i) => {
      let handle = th.querySelector('.column-resize');
      if (!handle) {
        handle = document.createElement('span');
        handle.className = 'column-resize';
        handle.dataset.column = String(i);
        handle.setAttribute('role', 'separator');
        handle.setAttribute('aria-label', `调整${th.textContent.trim() || '当前'}列宽`);
        handle.title = '拖动调整列宽';
        th.append(handle);
      }
      handle.tabIndex = 0;
      handle.setAttribute('aria-orientation', 'vertical');
    });
  });
}
let tableEnhanceFrame = 0;
const tableObserver = new MutationObserver(() => {
  if (tableEnhanceFrame) return;
  tableEnhanceFrame = requestAnimationFrame(() => {
    tableEnhanceFrame = 0;
    enhanceTables();
  });
});
tableObserver.observe(document.querySelector('#content'), {childList: true, subtree: true});
enhanceTables();

function setColumnWidth(table, column, width) {
  document.querySelectorAll('#content table.resizable-table').forEach(current => {
    if (current.dataset.tableWidthKey !== table.dataset.tableWidthKey) return;
    const cols = [...current.querySelectorAll(':scope > colgroup > col')];
    if (!cols[column]) return;
    cols[column].style.width = `${Math.max(75, width)}px`;
    current.style.width = `${cols.reduce((sum, col) => sum + Number.parseFloat(col.style.width), 0)}px`;
  });
}
function saveTableWidths(table) {
  const widths = [...table.querySelectorAll(':scope > colgroup > col')].map(col => Math.round(Number.parseFloat(col.style.width)));
  localStorage.setItem(table.dataset.tableWidthKey, JSON.stringify(widths));
}
document.addEventListener('pointerdown', event => {
  const handle = event.target.closest('.column-resize');
  if (!handle) return;
  const table = handle.closest('table.resizable-table');
  if (!table) return;
  event.preventDefault();
  const column = Number(handle.dataset.column);
  const startWidth = table.querySelectorAll(':scope > colgroup > col')[column].getBoundingClientRect().width;
  const startX = event.clientX;
  const move = next => setColumnWidth(table, column, Math.round(startWidth + next.clientX - startX));
  const stop = next => {
    if (next.type === 'pointerup') move(next);
    saveTableWidths(table);
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', stop);
    window.removeEventListener('pointercancel', stop);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', stop);
  window.addEventListener('pointercancel', stop);
});
document.addEventListener('keydown', event => {
  const handle = event.target.closest('.column-resize');
  if (!handle || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  const table = handle.closest('table.resizable-table');
  if (!table) return;
  event.preventDefault();
  const column = Number(handle.dataset.column);
  const col = table.querySelectorAll(':scope > colgroup > col')[column];
  setColumnWidth(table, column, Math.round(Number.parseFloat(col.style.width) + (event.key === 'ArrowRight' ? 16 : -16)));
  saveTableWidths(table);
});

document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.id === 'scan-refresh') {
    event.stopImmediatePropagation();
    startInventoryScan();
  } else if (button.dataset.itemDetail) {
    event.stopImmediatePropagation();
    const item = (state.discovered||[]).find(d => d.vmcore_path === button.dataset.itemDetail);
    if (!item) return;
    const runs = state.tasks.filter(t => t.vmcore_path === item.vmcore_path).sort((a,b) => b.created_at.localeCompare(a.created_at));
    const fields = [['vmcore',item.vmcore_path],['主机名',item.hostname_hint],['OS',item.os_name_hint],['内核',item.kernel_hint],['故障时间',item.dump_time_hint],['VMLINUX',item.vmlinux_path||'未匹配'],['osinfo.txt',item.osinfo_status],['文件状态',item.ready?'就绪':'待稳定']];
    $('#inventory-detail-body').innerHTML = `<div class="reader-head"><strong>VMCORE 详情</strong><button class="secondary" id="inventory-detail-close">关闭</button></div><div class="detail-grid">${fields.map(([name,value])=>detailItem(name,value||'未知')).join('')}</div><h3>分析记录 · ${runs.length}</h3>${runs.map(t=>`<button class="detail-run" data-task="${esc(t.id)}">${fmt(t.created_at)} · ${statuses[t.status]||t.status} · ${dur(t.duration_seconds)}</button>`).join('')||'<p class="hint">尚未分析</p>'}`;
    $('#inventory-detail').showModal();
  } else if (button.id === 'inventory-detail-close') {
    event.stopImmediatePropagation();
    $('#inventory-detail').close();
  }
}, true);

document.addEventListener('input', event => {
  if (event.target.id === 'inventory-search' || event.target.id === 'inventory-status') {
    event.stopImmediatePropagation();
    paintInventory();
  }
}, true);
