import React, { useState, useEffect, useRef, useMemo } from 'react';
import { X, Plus, Search, Loader2, Sparkles, Trash2, BookOpen, AlertCircle, RefreshCw, FolderOpen, Settings, Edit3, Check } from 'lucide-react';

function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ============== API HELPERS ==============

async function apiFetch(url, options = {}) {
  const method = options.method || 'GET';
  console.log(`[API] ${method} ${url}`, options.body ? JSON.parse(options.body) : '');
  const r = await fetch(url, options);
  console.log(`[API] ${method} ${url} → ${r.status} ${r.statusText}`);
  if (!r.ok) {
    const errBody = await r.text().catch(() => '(无法读取响应体)');
    console.error(`[API] ${method} ${url} 错误响应:`, errBody);
    let detail = `HTTP ${r.status}`;
    try { const j = JSON.parse(errBody); detail = j.detail || j.message || detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return r;
}

const API = {
  async getConfig() {
    return (await apiFetch('/api/config')).json();
  },
  async saveImageDir(dir) {
    return (await apiFetch('/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_dir: dir })
    })).json();
  },
  async scan() {
    return (await apiFetch('/api/scan')).json();
  },
  async indexImage(filePath, title, summary, content, tags, notes, mastery, practiceCount, lastPracticedAt) {
    return (await apiFetch('/api/images/index', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: filePath, title, summary, content, tags, notes, mastery, practice_count: practiceCount, last_practiced_at: lastPracticedAt })
    })).json();
  },
  async updateImage(filePath, title, summary, content, tags, notes, mastery, practiceCount, lastPracticedAt, solution) {
    return (await apiFetch('/api/images/update', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: filePath, title, summary, content, tags, notes, mastery, practice_count: practiceCount, last_practiced_at: lastPracticedAt, solution })
    })).json();
  },
  async deleteImage(filePath) {
    return (await apiFetch(`/api/images/delete?file_path=${encodeURIComponent(filePath)}`, { method: 'DELETE' })).json();
  },
  async getAllImages(params = {}) {
    const qs = new URLSearchParams();
    if (params.query) qs.set('query', params.query);
    if (params.subject) qs.set('subject', params.subject);
    if (params.dateEnabled) qs.set('date_enabled', '1');
    if (params.startDate) qs.set('start_date', params.startDate);
    if (params.endDate) qs.set('end_date', params.endDate);
    const qsStr = qs.toString();
    const url = '/api/images/all' + (qsStr ? '?' + qsStr : '');
    return (await apiFetch(url)).json();
  },
  imageUrl(filePath) {
    return `/api/image-file?path=${encodeURIComponent(filePath)}`;
  }
};

// ============== CSS ==============

const CSS = `
.mnb {
  --paper: #FBF8F0;
  --grid: #DCE7F2;
  --margin: #C74B4B;
  --ink: #253654;
  --ink-soft: #57648A;
  --pencil: #9098A6;
  --accent: #E3B341;
  --accent-2: #4C9A8E;
  --card: #FFFFFF;
  --shadow: rgba(37, 54, 84, 0.10);
  font-family: "PingFang SC", "Microsoft YaHei", -apple-system, sans-serif;
  color: var(--ink);
  background:
    linear-gradient(90deg, transparent 0 55px, var(--grid) 55px 56px, transparent 56px),
    repeating-linear-gradient(var(--paper) 0 27px, var(--grid) 27px 28px);
  background-color: var(--paper);
  min-height: 100%;
  padding: 28px 20px 60px;
  position: relative;
  box-sizing: border-box;
}
.mnb *, .mnb *::before, .mnb *::after { box-sizing: border-box; }
.mnb .holes {
  position: absolute; left: 22px; top: 90px;
  display: flex; flex-direction: column; gap: 46px;
}
.mnb .hole {
  width: 12px; height: 12px; border-radius: 50%;
  background: var(--paper);
  box-shadow: inset 0 1px 3px rgba(37,54,84,0.35), 0 0 0 1px var(--grid);
}
.mnb .shell { max-width: 960px; margin: 0 auto; padding-left: 40px; }
.mnb .margin-rule {
  position: absolute; left: 56px; top: 0; bottom: 0;
  width: 2px; background: var(--margin); opacity: 0.55;
}
.mnb .header {
  display: flex; align-items: baseline; justify-content: space-between;
  flex-wrap: wrap; gap: 12px; margin-bottom: 22px;
}
.mnb h1 {
  font-family: "Songti SC", "STSong", "Noto Serif SC", serif;
  font-size: 30px; font-weight: 700; margin: 0; letter-spacing: 1px;
  position: relative; display: inline-block;
}
.mnb h1 .hl { background: linear-gradient(transparent 60%, var(--accent) 60%); padding: 0 2px; }
.mnb .subtitle { color: var(--ink-soft); font-size: 13px; margin-top: 4px; }
.mnb .tabs { display: flex; gap: 6px; }
.mnb .tab-btn {
  font-family: "Songti SC", "STSong", serif;
  font-size: 14px; padding: 9px 18px; border-radius: 8px 8px 0 0;
  border: 1.5px solid var(--ink); border-bottom: none;
  background: var(--paper); color: var(--ink-soft); cursor: pointer;
  position: relative; top: 1.5px; transition: all .15s ease;
}
.mnb .tab-btn.active {
  background: var(--card); color: var(--ink); font-weight: 700;
  box-shadow: 0 -2px 8px var(--shadow);
}
.mnb .tab-btn:not(.active):hover { color: var(--ink); }
.mnb .panel {
  background: var(--card); border: 1.5px solid var(--ink);
  border-radius: 0 10px 10px 10px; padding: 24px;
  box-shadow: 0 4px 18px var(--shadow);
}
.mnb .config-box {
  border: 1.5px dashed var(--grid); border-radius: 10px; padding: 16px;
  margin-bottom: 18px; background: rgba(255, 255, 255, 0.72);
}
.mnb .config-title {
  font-family: "Songti SC", "STSong", serif;
  font-size: 16px; font-weight: 700; margin: 0 0 6px;
}
.mnb .config-hint {
  color: var(--ink-soft); font-size: 12.5px; line-height: 1.6; margin: 0 0 12px;
}
.mnb .field-label {
  font-size: 12px; color: var(--ink-soft); font-weight: 700;
  letter-spacing: .5px; margin-bottom: 6px; display: block;
}
.mnb input[type="text"], .mnb input[type="password"], .mnb textarea {
  width: 100%; border: none; border-bottom: 1.5px solid var(--grid);
  background: transparent; padding: 7px 2px;
  font-size: 14px; color: var(--ink); font-family: inherit; outline: none;
}
.mnb textarea { resize: vertical; }
.mnb input[type="text"]:focus, .mnb input[type="password"]:focus, .mnb textarea:focus {
  border-bottom-color: var(--margin);
}
.mnb .field { margin-bottom: 16px; }
.mnb .save-btn {
  margin-top: 18px; padding: 10px 22px; border-radius: 7px;
  border: 1.5px solid var(--accent-2); background: var(--accent-2);
  color: #fff; font-weight: 700; font-size: 14px; cursor: pointer;
}
.mnb .save-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.mnb .save-btn.secondary {
  background: var(--paper); color: var(--ink); border-color: var(--ink);
}
.mnb .save-msg { font-size: 12.5px; color: var(--accent-2); margin-top: 8px; font-weight: 600; }
.mnb .save-msg.error { color: var(--margin); }
.mnb .error-msg {
  display: flex; align-items: center; gap: 6px;
  font-size: 12.5px; color: var(--margin); margin-top: 8px; font-weight: 600;
}
.mnb .tag-pill {
  display: inline-flex; align-items: center; gap: 5px;
  background: #FFF6E0; border: 1px solid var(--accent); color: #6B5314;
  border-radius: 999px; padding: 4px 10px 4px 12px;
  font-size: 12.5px; font-weight: 600;
  font-family: ui-monospace, "SF Mono", Consolas, monospace;
  cursor: default;
}
.mnb .tag-pill.editable { cursor: pointer; }
.mnb .tag-pill.editable:hover { background: #FFEDB0; }
.mnb .tag-pill button {
  background: none; border: none; cursor: pointer;
  color: #8A7020; display: flex; padding: 0;
}
.mnb .tag-pill input {
  border: none; background: transparent; font: inherit; color: inherit;
  width: auto; min-width: 40px; outline: none; padding: 0;
  border-bottom: 1px dashed var(--accent);
}
.mnb .tag-list { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.mnb .tag-list-vertical { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
.mnb .tag-row { display: flex; align-items: center; }
.mnb .tag-add-row { display: flex; gap: 8px; align-items: center; }
.mnb .tag-add-row input {
  border: 1.5px dashed var(--grid); border-radius: 999px;
  padding: 5px 12px; font-size: 12.5px; flex: 1;
}
.mnb .tag-add-row button {
  border: 1.5px solid var(--ink); background: var(--paper);
  border-radius: 999px; width: 28px; height: 28px;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; flex-shrink: 0;
}

/* Scan tab */
.mnb .scan-header {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 18px;
}
.mnb .scan-dir {
  font-size: 13px; color: var(--ink-soft);
  font-family: ui-monospace, "SF Mono", Consolas, monospace;
  background: var(--paper); padding: 4px 10px; border-radius: 4px;
  border: 1px solid var(--grid);
}
.mnb .refresh-btn {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 16px; border-radius: 7px;
  border: 1.5px solid var(--ink); background: var(--paper);
  color: var(--ink); font-weight: 700; font-size: 13px; cursor: pointer;
  transition: all .15s ease;
}
.mnb .refresh-btn:hover { background: var(--ink); color: var(--paper); }
.mnb .refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.mnb .scan-stats {
  font-size: 13px; color: var(--ink-soft); margin-left: auto;
}
.mnb .section-title {
  font-family: "Songti SC", "STSong", serif;
  font-size: 16px; font-weight: 700; margin: 0 0 12px;
  display: flex; align-items: center; gap: 8px;
}
.mnb .section-title .badge {
  font-size: 12px; background: var(--margin); color: #fff;
  border-radius: 999px; padding: 1px 8px; font-family: inherit;
}
.mnb .section-title .badge.green { background: var(--accent-2); }

.mnb .scan-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.mnb .scan-card {
  background: var(--card); border: 1.5px solid var(--grid);
  border-radius: 8px; overflow: hidden; cursor: pointer;
  transition: transform .15s, box-shadow .15s;
}
.mnb .scan-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px var(--shadow); }
.mnb .scan-card.unindexed { border-color: var(--accent); }
.mnb .scan-card .thumb {
  height: 100px; overflow: hidden; background: var(--grid);
  display: flex; align-items: center; justify-content: center;
}
.mnb .scan-card .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.mnb .scan-card .info {
  padding: 8px 10px; font-size: 11px; color: var(--ink-soft);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.mnb .scan-card .info .status { font-weight: 600; font-size: 10.5px; }
.mnb .scan-card .info .status.new { color: var(--margin); }
.mnb .scan-card .info .status.indexed { color: var(--accent-2); }

/* Subject page header */
.mnb .subject-page-header {
  display: flex; align-items: baseline; gap: 16px;
  margin-bottom: 16px; padding-bottom: 12px;
  border-bottom: 2px solid var(--grid);
}
.mnb .subject-page-title {
  margin: 0; font-size: 20px; font-weight: 800; color: var(--ink);
}
.mnb .subject-page-stats {
  font-size: 13px; color: var(--ink-soft); font-weight: 500;
}
.mnb .subject-filtered-hint { color: var(--margin); font-weight: 600; }

/* Library */
.mnb .library-toolbar {
  display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin-bottom: 16px;
}
.mnb .search-box {
  display: flex; align-items: center; gap: 6px;
  border-bottom: 1.5px solid var(--grid); padding: 6px 4px;
  flex: 1 1 220px; min-width: 180px;
}
.mnb .search-box input { border: none; }
.mnb .date-filter-check {
  display: flex; align-items: center; gap: 5px;
  font-size: 12.5px; color: var(--ink-soft); font-weight: 600;
  white-space: nowrap; cursor: pointer; user-select: none;
}
.mnb .date-filter-check input[type="checkbox"] {
  width: 15px; height: 15px; cursor: pointer; accent-color: var(--accent-2);
}
.mnb .date-input {
  border: 1.5px solid var(--grid); border-radius: 6px;
  padding: 5px 8px; font-size: 12.5px; color: var(--ink);
  background: var(--paper); font-family: inherit;
  outline: none; width: 135px;
}
.mnb .date-input:focus { border-color: var(--accent-2); }
.mnb .date-input:disabled { opacity: 0.4; cursor: not-allowed; }
.mnb .date-sep {
  font-size: 12.5px; color: var(--pencil); font-weight: 600;
}
.mnb .clear-filter-btn {
  border: 1.5px dashed var(--margin); background: none; color: var(--margin);
  border-radius: 999px; padding: 5px 13px; font-size: 12.5px; font-weight: 600;
  cursor: pointer; white-space: nowrap; transition: all .12s;
}
.mnb .clear-filter-btn:hover { background: var(--margin); color: #fff; }
.mnb .date-error {
  font-size: 12px; color: var(--margin); font-weight: 600; white-space: nowrap;
}
.mnb .tag-filter-bar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }
.mnb .filter-pill {
  border: 1.5px solid var(--pencil); background: var(--paper); color: var(--ink-soft);
  border-radius: 999px; padding: 5px 13px; font-size: 12.5px; font-weight: 600;
  font-family: ui-monospace, "SF Mono", Consolas, monospace;
  cursor: pointer; transition: all .12s;
}
.mnb .filter-pill.active {
  border-color: var(--margin); background: var(--margin); color: #fff;
}
.mnb .count-badge { opacity: 0.65; font-weight: 400; margin-left: 3px; }
.mnb .grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 16px;
}
.mnb .card {
  background: var(--card); border: 1.5px solid var(--ink); border-radius: 8px;
  overflow: hidden; cursor: pointer; transition: transform .15s, box-shadow .15s;
  display: flex; flex-direction: column;
}
.mnb .card:hover { transform: translateY(-3px); box-shadow: 0 8px 18px var(--shadow); }
.mnb .card-thumb {
  height: 130px; overflow: hidden; border-bottom: 1.5px solid var(--grid);
  background: var(--grid);
}
.mnb .card-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.mnb .card-body { padding: 10px 12px 12px; flex: 1; display: flex; flex-direction: column; }
.mnb .card-title {
  font-weight: 700; font-size: 13.5px; margin: 0 0 6px;
  overflow: hidden; text-overflow: ellipsis; display: -webkit-box;
  -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}
.mnb .card-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: auto; }
.mnb .card-tags span {
  font-size: 10.5px; font-family: ui-monospace, monospace;
  background: #FFF6E0; border: 1px solid var(--accent); color: #6B5314;
  border-radius: 999px; padding: 2px 7px;
}
.mnb .card-meta {
  display: flex; justify-content: space-between; align-items: center;
  margin-top: 6px; gap: 6px;
}
.mnb .card-mastery { font-size: 10.5px; color: var(--accent-2); }
.mnb .card-practice { font-size: 10.5px; color: var(--ink-soft); }

/* Analysis overlay */
.mnb .analyze-overlay {
  margin-top: 16px; padding: 16px;
  border: 1.5px dashed var(--grid); border-radius: 10px;
  background: rgba(255, 255, 255, 0.72);
}
.mnb .analyze-img {
  width: 100%; max-height: 300px; object-fit: contain;
  border-radius: 8px; border: 1.5px solid var(--grid); margin-bottom: 14px;
}
.mnb .analyze-btn {
  width: 100%; padding: 10px 14px; border-radius: 7px;
  border: 1.5px solid var(--ink); background: var(--ink); color: var(--paper);
  font-weight: 700; font-size: 14px; cursor: pointer;
  display: flex; align-items: center; justify-content: center; gap: 8px;
  transition: opacity .15s;
}
.mnb .analyze-btn:hover { opacity: 0.85; }
.mnb .analyze-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.mnb .empty { text-align: center; padding: 50px 20px; color: var(--ink-soft); }
.mnb .empty svg { opacity: 0.4; margin-bottom: 10px; }

/* Modal */
.mnb .modal-overlay {
  position: fixed; inset: 0; background: rgba(37,54,84,0.45);
  display: flex; align-items: center; justify-content: center;
  padding: 20px; z-index: 50;
}
.mnb .modal {
  background: var(--card); border: 1.5px solid var(--ink); border-radius: 10px;
  max-width: 640px; width: 100%; max-height: 88vh; overflow-y: auto;
  padding: 22px; position: relative;
}
.mnb .modal-close {
  position: absolute; top: 14px; right: 14px;
  width: 30px; height: 30px; border-radius: 50%;
  border: 1.5px solid var(--ink); background: var(--paper);
  display: flex; align-items: center; justify-content: center; cursor: pointer;
}
.mnb .modal img {
  width: 100%; border-radius: 8px; border: 1.5px solid var(--grid); margin-bottom: 14px;
}
.mnb .modal h2 {
  font-family: "Songti SC", "STSong", serif;
  font-size: 19px; margin: 0 0 10px; padding-right: 30px;
}
.mnb .modal .summary { font-size: 13.5px; line-height: 1.7; color: var(--ink-soft); margin-bottom: 14px; }
.mnb .timestamp-row {
  display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 14px;
}
.mnb .timestamp {
  font-size: 12px; color: var(--pencil);
  font-family: ui-monospace, "SF Mono", Consolas, monospace;
}
.mnb .modal .tag-list { margin-bottom: 18px; }
.mnb .modal-actions {
  display: flex; justify-content: flex-end; gap: 10px;
  border-top: 1px dashed var(--grid); padding-top: 14px;
}
.mnb .del-btn {
  display: flex; align-items: center; gap: 6px;
  border: 1.5px solid var(--margin); background: none; color: var(--margin);
  padding: 7px 14px; border-radius: 7px; font-size: 13px; font-weight: 600; cursor: pointer;
}
.mnb .spin { animation: mnbspin 0.9s linear infinite; }
@keyframes mnbspin { to { transform: rotate(360deg); } }

/* Mastery & Practice */
.mnb .mastery-group { display: flex; flex-wrap: wrap; gap: 8px; }
.mnb .mastery-option {
  display: flex; align-items: center; gap: 5px;
  padding: 6px 12px; border-radius: 7px;
  border: 1.5px solid var(--grid); cursor: pointer;
  font-size: 13px; transition: all .15s;
}
.mnb .mastery-option:hover { border-color: var(--ink-soft); }
.mnb .mastery-option.active { border-color: var(--accent-2); background: #E8F5F2; }
.mnb .mastery-option input[type="radio"] { display: none; }
.mnb .practice-count {
  font-size: 28px; font-weight: 700; color: var(--ink);
  font-family: "Songti SC", "STSong", serif;
  min-width: 36px; text-align: center;
}
.mnb .practice-btn {
  padding: 8px 14px; border-radius: 7px;
  border: 1.5px solid var(--accent-2); background: var(--accent-2);
  color: #fff; font-weight: 700; font-size: 14px; cursor: pointer;
  transition: opacity .15s;
}
.mnb .practice-btn:hover { opacity: 0.85; }

/* Solution section */
.mnb .solution-section textarea {
  background: #FAFAF5; border: 1.5px dashed var(--grid);
  border-radius: 8px; padding: 10px 12px;
  font-size: 13.5px; min-height: 80px;
}
.mnb .solution-images {
  display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px;
}
.mnb .solution-img-wrapper {
  position: relative; width: 100px; height: 80px;
  border: 1.5px solid var(--grid); border-radius: 6px;
  overflow: hidden; background: var(--grid);
}
.mnb .solution-img-wrapper img {
  width: 100%; height: 100%; object-fit: cover; display: block;
  margin-bottom: 0; border: none; border-radius: 0;
}
.mnb .solution-img-delete {
  position: absolute; top: 2px; right: 2px;
  width: 20px; height: 20px; border-radius: 50%;
  border: none; background: rgba(199,75,75,0.85); color: #fff;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; font-size: 10px; padding: 0;
}
.mnb .solution-img-delete:hover { background: var(--margin); }
.mnb .solution-add-btn {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 6px 14px; border-radius: 7px;
  border: 1.5px dashed var(--pencil); background: var(--paper);
  color: var(--ink-soft); font-size: 12.5px; font-weight: 600;
  cursor: pointer; transition: all .15s;
}
.mnb .solution-add-btn:hover {
  border-color: var(--accent-2); color: var(--accent-2);
}

/* Inline tag edit */
.mnb .tag-edit-input {
  width: 80px; border: none; background: transparent;
  font-size: 12.5px; font-family: ui-monospace, "SF Mono", Consolas, monospace;
  color: #6B5314; font-weight: 600; outline: none; padding: 0;
  border-bottom: 1px dashed var(--accent);
}

@media (max-width: 520px) {
  .mnb .shell { padding-left: 24px; }
  .mnb .margin-rule { left: 40px; }
  .mnb .holes { display: none; }
  .mnb h1 { font-size: 24px; }
  .mnb .panel { padding: 16px; }
  .mnb .scan-grid { grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); }
}
`;

// ============== TAG PILL (with inline editing) ==============

function TagPill({ tag, onDelete, onEdit }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(tag);
  const inputRef = useRef(null);

  useEffect(() => {
    if (editing && inputRef.current) inputRef.current.focus();
  }, [editing]);

  function commit() {
    const v = value.trim();
    if (v && v !== tag) onEdit(tag, v);
    else setValue(tag);
    setEditing(false);
  }

  if (editing) {
    return (
      <span className="tag-pill">
        <input
          ref={inputRef}
          className="tag-edit-input"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit();
            if (e.key === 'Escape') { setValue(tag); setEditing(false); }
          }}
          onBlur={commit}
        />
        <button onClick={commit} title="确认"><Check size={11} /></button>
      </span>
    );
  }

  return (
    <span className="tag-pill editable" onDoubleClick={() => setEditing(true)}>
      {tag}
      <button onClick={() => setEditing(true)} title="编辑"><Edit3 size={10} /></button>
      <button onClick={() => onDelete(tag)} title="删除"><X size={11} /></button>
    </span>
  );
}

// ============== PROBLEM CARD ==============

const MASTERY_LABELS = {
  mastered: '✅ 已掌握',
  unfamiliar: '⚠️ 不熟悉',
  practice: '🔄 继续练习',
};

function ProblemCard({ problem, imageUrl, onClick }) {
  return (
    <div className="card" onClick={onClick}>
      <div className="card-thumb">
        <img src={imageUrl} alt={problem.title} loading="lazy" />
      </div>
      <div className="card-body">
        <p className="card-title">{problem.title || '未命名题目'}</p>
        <div className="card-tags">
          {(problem.tags || []).slice(0, 4).map((t) => (
            <span key={t}>{t}</span>
          ))}
        </div>
        <div className="card-meta">
          {problem.mastery && (
            <span className="card-mastery">{MASTERY_LABELS[problem.mastery] || problem.mastery}</span>
          )}
          {(problem.practice_count > 0) && (
            <span className="card-practice">练习 {problem.practice_count} 次</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ============== MAIN APP ==============

export default function App() {
  const [tab, setTab] = useState('scan');

  // --- Config state ---
  const [imageDir, setImageDir] = useState('');
  const [dirInput, setDirInput] = useState('');
  const [dirSaving, setDirSaving] = useState(false);
  const [dirMsg, setDirMsg] = useState('');

  // --- Scan state ---
  const [scanData, setScanData] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState(null);

  // --- Analysis state (for unindexed image) ---
  const [analyzingFile, setAnalyzingFile] = useState(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState(null);
  const [draft, setDraft] = useState(null);
  const [tagInput, setTagInput] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');

  // --- Library state ---
  const [allIndexed, setAllIndexed] = useState([]);
  const [totalIndexedCount, setTotalIndexedCount] = useState(0);
  const [libLoaded, setLibLoaded] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedTags, setSelectedTags] = useState([]);
  const [dateFilterEnabled, setDateFilterEnabled] = useState(false);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [activeSubject, setActiveSubject] = useState(() => {
    try { return localStorage.getItem('mnb_active_subject') || 'all'; } catch { return 'all'; }
  });
  const [subjects, setSubjects] = useState([]);
  const pendingSubjectRef = useRef(null);

  // --- Detail modal ---
  const [detail, setDetail] = useState(null);
  const [detailTagInput, setDetailTagInput] = useState('');
  const [detailSaving, setDetailSaving] = useState(false);
  const [detailError, setDetailError] = useState(null);
  const [detailDirty, setDetailDirty] = useState(false);
  const titleSavingRef = useRef(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [editTitleValue, setEditTitleValue] = useState('');
  const [detailNotes, setDetailNotes] = useState('');
  const detailNotesRef = useRef('');
  const [detailContent, setDetailContent] = useState('');
  const detailContentRef = useRef('');
  const [detailMastery, setDetailMastery] = useState('');
  const [detailPracticeCount, setDetailPracticeCount] = useState(0);
  const [solutionText, setSolutionText] = useState('');
  const solutionTextRef = useRef('');
  const [solutionImages, setSolutionImages] = useState([]);
  const solutionImagesRef = useRef([]);
  const solutionFileInputRef = useRef(null);
  const solutionTextareaRef = useRef(null);

  // --- Load configs on mount ---
  useEffect(() => {
    API.getConfig().then((c) => {
      if (c.image_dir) {
        setImageDir(c.image_dir);
        setDirInput(c.image_dir);
      }
    }).catch(() => { });
  }, []);

  // persist activeSubject
  useEffect(() => {
    try { localStorage.setItem('mnb_active_subject', activeSubject); } catch { }
  }, [activeSubject]);

  // --- Load library when tab changes ---
  useEffect(() => {
    if (tab === 'library' && !libLoaded) {
      // 若扫描页设置了 pending subject，则跳转到对应学科页
      if (pendingSubjectRef.current) {
        setActiveSubject(pendingSubjectRef.current);
        pendingSubjectRef.current = null;
      }
      loadLibrary();
    }
  }, [tab]);

  const debounceRef = useRef(null);

  async function loadLibrary(filterParams = {}) {
    try {
      const data = await API.getAllImages(filterParams);
      if (Array.isArray(data)) {
        setAllIndexed(data);
        setTotalIndexedCount(data.length);
      } else {
        setAllIndexed(data.items || []);
        setTotalIndexedCount(data.total_count ?? 0);
        if (data.subjects) setSubjects(data.subjects);
      }
    } catch (e) {
      console.error('load library failed', e);
    } finally {
      setLibLoaded(true);
    }
  }

  // 搜索条件变化时带防抖重新请求后端
  useEffect(() => {
    if (tab !== 'library' || !libLoaded) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      // 前端校验：开始日期大于结束日期时不发请求
      if (dateFilterEnabled && startDate && endDate && startDate > endDate) return;
      loadLibrary({
        query,
        subject: activeSubject === 'all' ? '' : activeSubject,
        dateEnabled: dateFilterEnabled,
        startDate: dateFilterEnabled ? startDate : '',
        endDate: dateFilterEnabled ? endDate : '',
      });
    }, 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query, dateFilterEnabled, startDate, endDate, activeSubject]);

  // --- Config actions ---
  async function saveImageDir() {
    setDirSaving(true);
    setDirMsg('');
    try {
      await API.saveImageDir(dirInput.trim());
      setImageDir(dirInput.trim());
      setDirMsg('目录已保存');
    } catch (e) {
      setDirMsg(e.message || '保存失败');
    } finally {
      setDirSaving(false);
    }
  }

  // --- Scan ---
  async function doScan() {
    if (!imageDir) return;
    console.log('[扫描] 开始扫描目录：', imageDir);
    setScanning(true);
    setScanError(null);
    setAnalyzingFile(null);
    setDraft(null);
    try {
      const data = await API.scan();
      console.log('[扫描] 结果：共', data.total, '张，已索引', data.indexed_count, '待索引', data.unindexed_count);
      // 兼容旧格式：若无 by_subject，从 flat lists 构造
      if (!data.by_subject) {
        data.by_subject = { '未分类': { indexed: data.indexed || [], unindexed: data.unindexed || [] } };
      }
      // 使用后端返回的 subject_order，否则按 key 排序
      if (!data.subject_order) {
        data.subject_order = Object.keys(data.by_subject);
      }
      setScanData(data);
    } catch (e) {
      console.error('[扫描] 失败：', e.message, e);
      setScanError(e.message || '扫描失败');
    } finally {
      setScanning(false);
    }
  }

  // --- Analysis (calls server-side AI) ---
  async function startAnalyze(filePath) {
    console.group('[Analysis] start:', filePath);
    setAnalyzingFile(filePath);
    setDraft(null);
    setAnalysisError(null);
    setSaveMsg('');
    setAnalyzing(true);
    try {
      console.log('[Analysis] calling server /api/analyze');
      const resp = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: filePath })
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        const errMsg = errData.detail || `HTTP ${resp.status}`;
        console.error('[Analysis] server error:', errMsg);
        throw new Error(errMsg);
      }

      const result = await resp.json();
      console.log('[Analysis] server result:', result);
      setDraft({
        title: '',
        summary: '',
        content: result.content || '',
        tags: Array.isArray(result.tags) ? result.tags : []
      });
      console.groupEnd();
    } catch (e) {
      console.error('[Analysis] exception:', e.message, e);
      console.groupEnd();
      setAnalysisError(e.message || 'AI analysis failed');
      setDraft({ title: '', summary: '', content: '', tags: [] });
    } finally {
      setAnalyzing(false);
    }
  }

  function addTag() {
    const t = tagInput.trim();
    if (!t || !draft) return;
    if (!draft.tags.includes(t)) setDraft({ ...draft, tags: [...draft.tags, t] });
    setTagInput('');
  }

  function removeTag(t) {
    if (!draft) return;
    setDraft({ ...draft, tags: draft.tags.filter((x) => x !== t) });
  }

  function editTag(oldTag, newTag) {
    if (!draft) return;
    const newTags = draft.tags.map((t) => (t === oldTag ? newTag : t));
    setDraft({ ...draft, tags: newTags });
  }

  async function saveIndex() {
    if (!analyzingFile || !draft) return;
    console.log('[保存] 开始保存索引：', analyzingFile, draft);
    setSaving(true);
    setSaveMsg('');
    try {
      await API.indexImage(analyzingFile, draft.title || '未命名题目', draft.summary || '', draft.content || '', draft.tags || [], '', '', 0);
      console.log('[保存] 索引保存成功，重新扫描目录');
      setSaveMsg('已保存索引');
      // 记住当前分析的图片所属学科，以便后续跳转
      const data = await API.scan();
      // 从新扫描结果中推断当前图片的学科
      if (data.by_subject) {
        for (const [subj, group] of Object.entries(data.by_subject)) {
          if ((group.indexed || []).some((img) => img.file_path === analyzingFile)) {
            pendingSubjectRef.current = subj;
            break;
          }
        }
      }
      setScanData(data);
      setLibLoaded(false);
      setTimeout(() => {
        setAnalyzingFile(null);
        setDraft(null);
        setSaveMsg('');
      }, 600);
    } catch (e) {
      console.error('[保存] 失败：', e.message, e);
      setSaveMsg('保存失败，请重试');
    } finally {
      setSaving(false);
    }
  }

  // --- Library ---
  const allTags = useMemo(() => {
    const map = new Map();
    allIndexed.forEach((p) => (p.tags || []).forEach((t) => map.set(t, (map.get(t) || 0) + 1)));
    return Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
  }, [allIndexed]);

  const filtered = useMemo(() => {
    return allIndexed.filter((p) => {
      if (selectedTags.length && !selectedTags.every((t) => (p.tags || []).includes(t))) return false;
      return true;
    });
  }, [allIndexed, selectedTags]);

  function switchSubject(subj) {
    setActiveSubject(subj);
    setSelectedTags([]);
  }

  function getSubjectLabel() {
    if (activeSubject === 'all') return '全部错题库';
    return `${activeSubject}错题库`;
  }

  function toggleTag(t) {
    setSelectedTags((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  }

  function clearAllFilters() {
    setQuery('');
    setSelectedTags([]);
    setDateFilterEnabled(false);
    setStartDate('');
    setEndDate('');
    setActiveSubject('all');
  }

  // --- Detail modal ---
  function openDetail(p) {
    const sol = (typeof p.solution === 'string' ? JSON.parse(p.solution || '{}') : (p.solution || {}));
    setDetail(p);
    setDetailTagInput('');
    setDetailError(null);
    setDetailDirty(false);
    setEditingTitle(false);
    setEditTitleValue(p.title || '');
    setDetailNotes(p.notes || '');
    detailNotesRef.current = p.notes || '';
    setDetailContent(p.content || '');
    detailContentRef.current = p.content || '';
    setDetailMastery(p.mastery || '');
    setDetailPracticeCount(p.practice_count || 0);
    setSolutionText(sol.text || '');
    solutionTextRef.current = sol.text || '';
    setSolutionImages(Array.isArray(sol.images) ? sol.images : []);
    solutionImagesRef.current = Array.isArray(sol.images) ? sol.images : [];
  }

  async function deleteFromIndex(filePath) {
    try {
      await API.deleteImage(filePath);
      setAllIndexed((prev) => prev.filter((p) => p.file_path !== filePath));
    } catch (e) {
      console.error('delete failed', e);
    }
    setDetail(null);
  }

  function closeDetailModal() {
    if (detailSaving) return;
    if (detailDirty) {
      setDetailError('当前有未保存修改，请先点击“保存修改”');
      return;
    }
    setDetail(null);
  }

  function applyDetailDraft(updates) {
    if (!detail) return;
    setDetail((prev) => ({ ...prev, ...updates }));
    setDetailDirty(true);
  }

  async function saveDetail() {
    if (!detail) return;
    setDetailError(null);
    setDetailSaving(true);
    try {
      const title = editTitleValue.trim() || '未命名题目';
      const content = detailContentRef.current;
      const notes = detailNotesRef.current;
      const solution = JSON.stringify({
        text: solutionTextRef.current,
        images: solutionImagesRef.current,
      });
      await API.updateImage(
        detail.file_path,
        title,
        detail.summary,
        content,
        detail.tags || [],
        notes,
        detailMastery,
        detailPracticeCount,
        detail.last_practiced_at,
        solution,
      );
      const updated = {
        ...detail,
        title,
        content,
        notes,
        mastery: detailMastery,
        practice_count: detailPracticeCount,
        solution,
      };
      setDetail(updated);
      setAllIndexed((prev) => prev.map((p) => (p.file_path === detail.file_path ? updated : p)));
      setDetailDirty(false);
    } catch (e) {
      console.error('update failed', e);
      setDetailError('保存失败 ' + (e.message || '未知错误'));
    } finally {
      setDetailSaving(false);
    }
  }

  function updateDetailTags(newTags) {
    applyDetailDraft({ tags: newTags });
  }

  async function saveDetailTitle() {
    if (titleSavingRef.current) return;
    titleSavingRef.current = true;
    try {
      const v = editTitleValue.trim();
      if (v) {
        setEditingTitle(false);
        setEditTitleValue(v);
        applyDetailDraft({ title: v });
      } else {
        setEditTitleValue(detail.title || '');
        setEditingTitle(false);
      }
    } finally {
      titleSavingRef.current = false;
    }
  }

  function saveDetailNotes() {
    applyDetailDraft({ notes: detailNotesRef.current });
  }

  function saveDetailContent() {
    applyDetailDraft({ content: detailContentRef.current });
  }

  function getSolutionFullPath(filename) {
    if (!detail || !filename) return '';
    const normalized = detail.file_path.replace(/\\/g, '/');
    const idx = normalized.lastIndexOf('/');
    if (idx === -1) return filename;
    return `${detail.file_path.slice(0, idx + 1)}${filename}`;
  }

  function saveSolution(text, images) {
    applyDetailDraft({ solution: JSON.stringify({ text, images }) });
  }

  function saveSolutionText() {
    saveSolution(solutionTextRef.current, solutionImagesRef.current);
  }

  async function uploadSolutionImage(base64Data, ext = 'png') {
    if (!detail) return;
    try {
      const resp = await fetch('/api/solution-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: detail.file_path, image_data: base64Data, ext })
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const result = await resp.json();
      const newImages = [...solutionImagesRef.current, result.filename];
      setSolutionImages(newImages);
      solutionImagesRef.current = newImages;
      saveSolution(solutionTextRef.current, newImages);
    } catch (e) {
      console.error('upload solution image failed', e);
      setDetailError('解答图片上传失败 ' + (e.message || '未知错误'));
    }
  }

  async function deleteSolutionImage(filename) {
    try {
      const filePath = getSolutionFullPath(filename);
      const resp = await fetch(`/api/solution-image?path=${encodeURIComponent(filePath)}`, { method: 'DELETE' });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const newImages = solutionImagesRef.current.filter((f) => f !== filename);
      setSolutionImages(newImages);
      solutionImagesRef.current = newImages;
      saveSolution(solutionTextRef.current, newImages);
    } catch (e) {
      console.error('delete solution image failed', e);
      setDetailError('解答图片删除失败 ' + (e.message || '未知错误'));
    }
  }

  function handleSolutionPaste(e) {
    const items = Array.from(e.clipboardData?.items || []);
    const imageItem = items.find((item) => item.type.startsWith('image/'));
    if (!imageItem) return;
    e.preventDefault();
    const file = imageItem.getAsFile();
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => uploadSolutionImage(reader.result, file.type.split('/')[1] || 'png');
    reader.readAsDataURL(file);
  }

  function handleSolutionFileSelect(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => uploadSolutionImage(reader.result, file.type.split('/')[1] || 'png');
    reader.readAsDataURL(file);
    e.target.value = '';
  }

  function saveDetailMastery(val) {
    setDetailMastery(val);
    applyDetailDraft({ mastery: val });
  }

  function incrementPractice() {
    const newCount = detailPracticeCount + 1;
    const now = new Date();
    const local = now.getFullYear() + '-' +
      String(now.getMonth() + 1).padStart(2, '0') + '-' +
      String(now.getDate()).padStart(2, '0') + ' ' +
      String(now.getHours()).padStart(2, '0') + ':' +
      String(now.getMinutes()).padStart(2, '0') + ':' +
      String(now.getSeconds()).padStart(2, '0');
    setDetailPracticeCount(newCount);
    applyDetailDraft({ practice_count: newCount, last_practiced_at: local });
  }

  function addDetailTag() {
    const t = detailTagInput.trim();
    if (!t || !detail) return;
    if (!(detail.tags || []).includes(t)) updateDetailTags([...(detail.tags || []), t]);
    setDetailTagInput('');
  }

  function removeDetailTag(t) {
    if (!detail) return;
    updateDetailTags((detail.tags || []).filter((x) => x !== t));
  }

  function editDetailTag(oldTag, newTag) {
    if (!detail) return;
    const newTags = (detail.tags || []).map((t) => (t === oldTag ? newTag : t));
    updateDetailTags(newTags);
  }

  // --- Render ---
  return (
    <div className="mnb">
      <style>{CSS}</style>
      <div className="holes">
        <div className="hole" /><div className="hole" /><div className="hole" /><div className="hole" />
      </div>
      <div className="margin-rule" />
      <div className="shell">
        <div className="header">
          <div>
            <h1>错题<span className="hl">本</span></h1>
            <div className="subtitle">目录扫描 · AI 打标签 · 按考点查题</div>
          </div>
          <div className="tabs">
            <button className={'tab-btn' + (tab === 'scan' ? ' active' : '')} onClick={() => setTab('scan')}>
              <FolderOpen size={14} style={{ marginRight: 4, verticalAlign: -2 }} />扫描
            </button>
            <button className={'tab-btn' + (tab === 'library' ? ' active' : '')} onClick={() => setTab('library')}>
              <BookOpen size={14} style={{ marginRight: 4, verticalAlign: -2 }} />
              错题库 {totalIndexedCount > 0 ? `(${totalIndexedCount})` : ''}
            </button>
            <button className={'tab-btn' + (tab === 'config' ? ' active' : '')} onClick={() => setTab('config')}>
              <Settings size={14} style={{ marginRight: 4, verticalAlign: -2 }} />配置
            </button>
          </div>
        </div>

        {/* ============ CONFIG TAB ============ */}
        {tab === 'config' && (
          <div className="panel">
            <div className="config-box">
              <h2 className="config-title">图片目录</h2>
              <p className="config-hint">
                设置存放错题图片的本地文件夹路径。程序将扫描该目录下的所有图片文件（支持 jpg / png / gif / webp / bmp）。
              </p>
              <div className="field">
                <label className="field-label">目录路径</label>
                <input type="text" value={dirInput}
                  onChange={(e) => setDirInput(e.target.value)}
                  placeholder="例如：C:\Users\me\Pictures\错题" />
              </div>
              <button className="save-btn" style={{ marginTop: 0 }} onClick={saveImageDir} disabled={dirSaving}>
                {dirSaving ? '保存中…' : '保存目录'}
              </button>
              {dirMsg && <div className={'save-msg' + (dirMsg.includes('失败') ? ' error' : '')}>{dirMsg}</div>}
            </div>
          </div>
        )}

        {/* ============ SCAN TAB ============ */}
        {tab === 'scan' && (
          <div className="panel">
            <div className="scan-header">
              {imageDir ? (
                <span className="scan-dir">{imageDir}</span>
              ) : (
                <span style={{ color: 'var(--margin)', fontSize: 13 }}>请先在"配置"页面设置图片目录</span>
              )}
              <button className="refresh-btn" onClick={doScan} disabled={scanning || !imageDir}>
                {scanning ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}
                {scanning ? '扫描中…' : '刷新扫描'}
              </button>
              {scanData && (
                <span className="scan-stats">
                  共 {scanData.total} 张 · 已索引 {scanData.indexed_count} · 待索引 {scanData.unindexed_count}
                </span>
              )}
            </div>
            {scanError && <div className="error-msg"><AlertCircle size={13} /> {scanError}</div>}

            {/* Analyzing overlay */}
            {analyzingFile && (
              <div className="analyze-overlay">
                <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                  <div style={{ flex: '0 0 240px' }}>
                    <img className="analyze-img" src={API.imageUrl(analyzingFile)} alt="分析中" />
                    {!draft && (
                      <button className="analyze-btn" onClick={() => startAnalyze(analyzingFile)} disabled={analyzing}>
                        {analyzing ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
                        {analyzing ? 'AI 分析中…' : 'AI 分析知识点'}
                      </button>
                    )}
                    {analysisError && <div className="error-msg"><AlertCircle size={13} /> {analysisError}</div>}
                    {draft && (
                      <button className="analyze-btn"
                        style={{ background: 'var(--paper)', color: 'var(--ink)', marginTop: 8 }}
                        onClick={() => startAnalyze(analyzingFile)} disabled={analyzing}>
                        {analyzing ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}重新分析
                      </button>
                    )}
                  </div>
                  {draft && (
                    <div style={{ flex: '1 1 280px' }}>
                      <div className="field">
                        <label className="field-label">题目标题</label>
                        <input type="text" value={draft.title}
                          onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                          placeholder="例如：相似三角形与中位线综合题" />
                      </div>
                      <div className="field">
                        <label className="field-label">题目复述</label>
                        <textarea rows={4} value={draft.summary}
                          onChange={(e) => setDraft({ ...draft, summary: e.target.value })}
                          placeholder="题目的文字描述，方便以后搜索" />
                      </div>
                      <div className="field">
                        <label className="field-label">题目内容</label>
                        <textarea rows={6} value={draft.content}
                          onChange={(e) => setDraft({ ...draft, content: e.target.value })}
                          placeholder="AI 从图片中提取的题目文字内容，可手动修正" />
                      </div>
                      <div className="field">
                        <label className="field-label">知识点标签（双击编辑，点击 × 删除）</label>
                        <div className="tag-list-vertical">
                          {draft.tags.map((t) => (
                            <div key={t} className="tag-row">
                              <TagPill tag={t} onDelete={removeTag} onEdit={editTag} />
                            </div>
                          ))}
                          {draft.tags.length === 0 && (
                            <span style={{ fontSize: 12.5, color: 'var(--ink-soft)' }}>还没有标签</span>
                          )}
                        </div>
                        <div className="tag-add-row">
                          <input type="text" placeholder="添加知识点，回车确认" value={tagInput}
                            onChange={(e) => setTagInput(e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag(); } }} />
                          <button onClick={addTag}><Plus size={14} /></button>
                        </div>
                      </div>
                      <button className="save-btn" onClick={saveIndex} disabled={saving}>
                        {saving ? '保存中…' : '保存索引'}
                      </button>
                      <button className="save-btn secondary" style={{ marginLeft: 8 }}
                        onClick={() => { setAnalyzingFile(null); setDraft(null); setAnalysisError(null); }}>
                        取消
                      </button>
                      {saveMsg && <div className="save-msg">{saveMsg}</div>}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Empty / No scan */}
            {!scanData && !scanning && !analyzingFile && imageDir && (
              <div className="empty"><RefreshCw size={36} /><p>点击"刷新扫描"查看目录中的图片</p></div>
            )}
            {!imageDir && (
              <div className="empty"><FolderOpen size={36} /><p>请先在"配置"页面设置图片存放目录</p></div>
            )}

            {/* Subject-grouped scan results (use subject_order from backend) */}
            {scanData && !analyzingFile && (scanData.subject_order || Object.keys(scanData.by_subject || {})).map((subject) => {
              const group = scanData.by_subject[subject]; if (!group) return null;
              return (
                <div key={subject} style={{ marginBottom: 24 }}>
                  <div className="section-title">{subject || '未分类'} <span className="badge" style={{ marginLeft: 8, fontSize: 11 }}>{((group.indexed || []).length + (group.unindexed || []).length)} 张</span></div>
                  {/* Unindexed in this subject */}
                  {(group.unindexed || []).length > 0 && (
                    <div style={{ marginBottom: 16 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--margin)', marginBottom: 8 }}>待索引 <span className="badge">{group.unindexed.length}</span></div>
                      <div className="scan-grid">
                        {group.unindexed.map((img) => (
                          <div key={img.file_path} className="scan-card unindexed"
                            onClick={() => startAnalyze(img.file_path)}>
                            <div className="thumb">
                              <img src={API.imageUrl(img.file_path)} alt={img.file_name} loading="lazy" />
                            </div>
                            <div className="info">
                              <span className="status new">● 待索引</span>
                              <div style={{ fontSize: 10.5, marginTop: 2 }}>{img.file_name}</div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {/* Indexed in this subject */}
                  {(group.indexed || []).length > 0 && (
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent-2)', marginBottom: 8 }}>已索引 <span className="badge green">{group.indexed.length}</span></div>
                      <div className="scan-grid">
                        {group.indexed.map((img) => (
                          <div key={img.file_path} className="scan-card"
                            onClick={() => openDetail(img)}>
                            <div className="thumb">
                              <img src={API.imageUrl(img.file_path)} alt={img.title} loading="lazy" />
                            </div>
                            <div className="info">
                              <span className="status indexed">● 已索引</span>
                              <div style={{ fontSize: 10.5, marginTop: 2, fontWeight: 600 }}>{img.title}</div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {(!group.indexed || group.indexed.length === 0) && (!group.unindexed || group.unindexed.length === 0) && (
                    <div style={{ fontSize: 12.5, color: 'var(--ink-soft)', padding: '8px 0' }}>该学科暂无图片</div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* ============ LIBRARY TAB ============ */}
        {tab === 'library' && (() => {
          const globalTotal = activeSubject === 'all'
            ? totalIndexedCount
            : subjects.reduce((sum, s) => sum + (s.total_count || 0), 0);
          return (
            <div className="panel">
              {!libLoaded ? (
                <div className="empty"><Loader2 size={28} className="spin" /><p>加载中…</p></div>
              ) : globalTotal === 0 ? (
                /* 空状态①：整个错题库没有任何已索引的错题 */
                <div className="empty"><BookOpen size={40} /><p>还没有索引任何错题，去"扫描"页面导入吧</p></div>
              ) : (
                <>
                  {/* ---- 学科标题 + 统计 ---- */}
                  <div className="subject-page-header">
                    <h2 className="subject-page-title">{getSubjectLabel()}</h2>
                    <span className="subject-page-stats">
                      共 {totalIndexedCount} 题
                      {filtered.length !== totalIndexedCount && (
                        <span className="subject-filtered-hint">，当前筛出 {filtered.length} 题</span>
                      )}
                    </span>
                  </div>

                  <div className="library-toolbar">
                    <div className="search-box">
                      <Search size={15} color="#57648A" />
                      <input type="text" placeholder="搜索标题、内容或标签" value={query}
                        onChange={(e) => setQuery(e.target.value)} />
                    </div>
                    <label className="date-filter-check">
                      <input type="checkbox" checked={dateFilterEnabled}
                        onChange={(e) => setDateFilterEnabled(e.target.checked)} />
                      按添加时间筛选
                    </label>
                    <input type="date" className="date-input" value={startDate}
                      onChange={(e) => setStartDate(e.target.value)}
                      disabled={!dateFilterEnabled} title="开始日期" />
                    <span className="date-sep">—</span>
                    <input type="date" className="date-input" value={endDate}
                      onChange={(e) => setEndDate(e.target.value)}
                      disabled={!dateFilterEnabled} title="结束日期" />
                    {(query || selectedTags.length > 0 || dateFilterEnabled || activeSubject !== 'all') && (
                      <button className="clear-filter-btn" onClick={clearAllFilters}>清空筛选</button>
                    )}
                    {dateFilterEnabled && startDate && endDate && startDate > endDate && (
                      <span className="date-error">开始日期不能大于结束日期</span>
                    )}
                  </div>

                  {/* 学科页签（始终显示） */}
                  <div className="tag-filter-bar" style={{ marginBottom: 12 }}>
                    <button
                      className={'filter-pill' + (activeSubject === 'all' ? ' active' : '')}
                      onClick={() => switchSubject('all')}>
                      全部<span className="count-badge">{globalTotal}</span>
                    </button>
                    {subjects.map((s) => (
                      <button key={s.name || s}
                        className={'filter-pill' + (activeSubject === (s.name || s) ? ' active' : '')}
                        onClick={() => switchSubject(s.name || s)}>
                        {s.name || s}<span className="count-badge">{s.total_count ?? s.count ?? ''}</span>
                      </button>
                    ))}
                  </div>

                  {/* 空状态②：当前学科有题但没筛出来 */}
                  {totalIndexedCount === 0 ? (
                    <div className="empty"><BookOpen size={36} /><p>该学科暂无已索引的错题</p></div>
                  ) : (
                    <>
                      {allTags.length > 0 && (
                        <div className="tag-filter-bar">
                          {allTags.map(([t, count]) => (
                            <button key={t}
                              className={'filter-pill' + (selectedTags.includes(t) ? ' active' : '')}
                              onClick={() => toggleTag(t)}>
                              {t}<span className="count-badge">{count}</span>
                            </button>
                          ))}
                          {selectedTags.length > 0 && (
                            <button className="filter-pill" onClick={() => setSelectedTags([])}
                              style={{ borderStyle: 'dashed' }}>清除标签 ×</button>
                          )}
                        </div>
                      )}
                      {/* 空状态③：有题但筛出来为空 */}
                      {filtered.length === 0 ? (
                        <div className="empty"><Search size={32} /><p>没有匹配的题目，试试调整筛选条件</p></div>
                      ) : (
                        <div className="grid">
                          {filtered.map((p) => (
                            <ProblemCard key={p.file_path} problem={p}
                              imageUrl={API.imageUrl(p.file_path)}
                              onClick={() => openDetail(p)} />
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </>
              )}
            </div>
          );
        })()}
      </div>

      {/* ============ DETAIL MODAL ============ */}
      {detail && (
        <div className="modal-overlay" onClick={closeDetailModal}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-close" onClick={closeDetailModal}><X size={16} /></div>
            <img src={API.imageUrl(detail.file_path)} alt={detail.title} />

            {/* 可编辑标题 */}
            {editingTitle ? (
              <div className="field" style={{ marginBottom: 10 }}>
                <input type="text" value={editTitleValue}
                  onChange={(e) => setEditTitleValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') saveDetailTitle();
                    if (e.key === 'Escape') { setEditTitleValue(detail.title || ''); setEditingTitle(false); }
                  }}
                  onBlur={saveDetailTitle}
                  autoFocus
                  style={{ fontSize: 19, fontWeight: 700, fontFamily: '"Songti SC", "STSong", serif' }} />
              </div>
            ) : (
              <h2 onClick={() => { setEditingTitle(true); setEditTitleValue(detail.title || ''); }}
                style={{ cursor: 'pointer' }} title="点击编辑标题">
                {detail.title || '未命名题目'} <Edit3 size={13} style={{ opacity: 0.4, verticalAlign: 'middle' }} />
              </h2>
            )}

            <div className="summary">{detail.summary}</div>

            <div className="field" style={{ marginBottom: 14 }}>
              <label className="field-label">题目内容</label>
              <textarea rows={6} value={detailContent}
                onChange={(e) => { setDetailContent(e.target.value); detailContentRef.current = e.target.value; }}
                onBlur={saveDetailContent}
                placeholder="题目文字内容" />
            </div>

            {/* 时间信息 */}
            <div className="timestamp-row">
              {detail.created_at && (
                <span className="timestamp">📅 添加于 {formatTime(detail.created_at)}</span>
              )}
              {detail.last_practiced_at && (
                <span className="timestamp">🕐 最近练习 {formatTime(detail.last_practiced_at)}</span>
              )}
            </div>

            {/* 标签列表 - 一行一个 */}
            <label className="field-label">知识点标签</label>
            <div className="tag-list-vertical">
              {(detail.tags || []).map((t) => (
                <div key={t} className="tag-row">
                  <TagPill tag={t} onDelete={removeDetailTag} onEdit={editDetailTag} />
                </div>
              ))}
              {(detail.tags || []).length === 0 && (
                <span style={{ fontSize: 12.5, color: 'var(--ink-soft)' }}>还没有标签</span>
              )}
            </div>
            <div className="tag-add-row" style={{ marginBottom: 18 }}>
              <input type="text" placeholder="添加知识点，回车确认" value={detailTagInput}
                onChange={(e) => setDetailTagInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addDetailTag(); } }} />
              <button onClick={addDetailTag}><Plus size={14} /></button>
            </div>

            {/* 备注栏 */}
            <div className="field">
              <label className="field-label">备注</label>
              <textarea rows={3} value={detailNotes}
                onChange={(e) => { setDetailNotes(e.target.value); detailNotesRef.current = e.target.value; }}
                onBlur={saveDetailNotes}
                placeholder="人工备注，记录解题思路或易错点…" />
            </div>

            <div className="field solution-section">
              <label className="field-label">解答</label>
              <textarea
                ref={solutionTextareaRef}
                rows={5}
                value={solutionText}
                onChange={(e) => { setSolutionText(e.target.value); solutionTextRef.current = e.target.value; }}
                onBlur={saveSolutionText}
                onPaste={handleSolutionPaste}
                placeholder="输入解题思路，或直接在这里粘贴截图…"
              />
              {solutionImages.length > 0 && (
                <div className="solution-images">
                  {solutionImages.map((filename) => (
                    <div key={filename} className="solution-img-wrapper">
                      <img src={API.imageUrl(getSolutionFullPath(filename))} alt={filename} />
                      <button className="solution-img-delete"
                        onClick={() => deleteSolutionImage(filename)}
                        title="删除解答图片">
                        <X size={10} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <input type="file" accept="image/*" ref={solutionFileInputRef}
                onChange={handleSolutionFileSelect} style={{ display: 'none' }} />
              <button className="solution-add-btn"
                onClick={() => solutionFileInputRef.current?.click()}>
                <Plus size={13} /> 添加图片
              </button>
            </div>

            {/* 掌握程度 + 练习计数 */}
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 14 }}>
              <div className="field" style={{ flex: '1 1 200px', marginBottom: 0 }}>
                <label className="field-label">掌握程度</label>
                <div className="mastery-group">
                  {[
                    { val: '', label: '未设置' },
                    { val: 'mastered', label: '✅ 已掌握' },
                    { val: 'unfamiliar', label: '⚠️ 不熟悉' },
                    { val: 'practice', label: '🔄 继续练习' },
                  ].map((opt) => (
                    <label key={opt.val} className={'mastery-option' + (detailMastery === opt.val ? ' active' : '')}>
                      <input type="radio" name="mastery" value={opt.val}
                        checked={detailMastery === opt.val}
                        onChange={() => saveDetailMastery(opt.val)} />
                      <span>{opt.label}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div className="field" style={{ flex: '0 0 auto', marginBottom: 0, textAlign: 'center' }}>
                <label className="field-label">练习次数</label>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="practice-count">{detailPracticeCount}</span>
                  <button className="practice-btn" onClick={incrementPractice} title="练习 +1">+1</button>
                </div>
              </div>
            </div>

            {detailError && <div className="save-msg error" style={{ marginTop: -10, marginBottom: 12 }}>{detailError}</div>}
            {detailSaving && <div className="save-msg" style={{ marginTop: -10, marginBottom: 12 }}>保存中…</div>}
            <div className="modal-actions">
              <button className="save-btn" style={{ marginTop: 0 }} onClick={saveDetail} disabled={detailSaving || !detailDirty}>
                {detailSaving ? '保存中…' : '保存修改'}
              </button>
              <button className="del-btn" onClick={() => deleteFromIndex(detail.file_path)}>
                <Trash2 size={14} /> 从索引中移除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
