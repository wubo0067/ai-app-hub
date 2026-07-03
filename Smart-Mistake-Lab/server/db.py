import,sqlite3,,,,
i:port json,..
import os
from log import logger
from datetime import datetime

def _now() -> str:
    """返回本地时间的 ISO 格式字符串"""
    return datetime.now().isoformat(sep=' ', timespec='seconds')

DB_PATH = os.path.join(os.path.dirname(__file__), 'data.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            file_name TEXT NOT NULL,
            title TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            content TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            notes TEXT DEFAULT '',
            mastery TEXT DEFAULT '',
            practice_count INTEGER DEFAULT 0,
            last_practiced_at TIMESTAMP,
            solution TEXT DEFAULT '',
            indexed_at TIMESTAMP,
            created_at TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    # 为已有数据库添加新字段（如果不存在）
    try:
        conn.execute('ALTER TABLE images ADD COLUMN notes TEXT DEFAULT ""')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE images ADD COLUMN mastery TEXT DEFAULT ""')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE images ADD COLUMN practice_count INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE images ADD COLUMN last_practiced_at TIMESTAMP')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE images ADD COLUMN solution TEXT DEFAULT ""')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE images ADD COLUMN content TEXT DEFAULT ""')
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


# --- Config ---

def get_config_value(key: str) -> str | None:
    conn = get_db()
    row = conn.execute('SELECT value FROM config WHERE key = ?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else None


def set_config_value(key: str, value: str):
    conn = get_db()
    conn.execute(
        'INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
        (key, value)
    )
    conn.commit()
    conn.close()


# --- Image CRUD ---

def get_all_indexed_paths() -> set[str]:
    conn = get_db()
    rows = conn.execute('SELECT file_path FROM images').fetchall()
    conn.close()
    return {r['file_path'] for r in rows}


def get_image_by_path(file_path: str) -> dict | None:
    conn = get_db()
    row = conn.execute('SELECT * FROM images WHERE file_path = ?', (file_path,)).fetchone()
    conn.close()
    if row:
        d = dict(row)
        d['tags'] = json.loads(d['tags'])
        d['solution'] = json.loads(d.get('solution') or '{}')
        return d
    return None


def get_all_images() -> list[dict]:
    conn = get_db()
    rows = conn.execute('SELECT * FROM images ORDER BY indexed_at DESC').fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['tags'] = json.loads(d['tags'])
        d['solution'] = json.loads(d.get('solution') or '{}')
        result.append(d)
    return result


def mark_indexed(file_path: str, title: str, summary: str, content: str, tags: list[str],
                 notes: str = '', mastery: str = '', practice_count: int = 0,
                      last_practiced_at: str | None = None, solution: str = ''):
    conn = get_db()
    conn.execute(
        '''INSERT OR REPLACE INTO images
          (file_path, file_name, title, summary, content, tags, notes, mastery, practice_count, last_practiced_at, solution, indexed_at, created_at)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
      (file_path, os.path.basename(file_path), title, summary, content,
            json.dumps(tags, ensure_ascii=False), notes, mastery, practice_count, last_practiced_at, solution, _now(), _now())
    )
    conn.commit()
    conn.close()


def update_image_meta(file_path: str, title: str | None = None,
                      summary: str | None = None, content: str | None = None,
                      tags: list[str] | None = None,
                      notes: str | None = None, mastery: str | None = None,
                      practice_count: int | None = None,
                      last_practiced_at: str | None = None,
                      solution: str | None = None):
    conn = get_db()
    updates = []
    params = []
    # 日志记录修改的字段和参数
    logger.info(f"Updating image meta for {file_path}: title={title}, summary={summary}, content={content}, tags={tags}, notes={notes}, mastery={mastery}, practice_count={practice_count}, last_practiced_at={last_practiced_at}, solution={solution}")
    if title is not None:
        updates.append('title = ?')
        params.append(title)
    if summary is not None:
        updates.append('summary = ?')
        params.append(summary)
    if content is not None:
        updates.append('content = ?')
        params.append(content)
    if tags is not None:
        updates.append('tags = ?')
        params.append(json.dumps(tags, ensure_ascii=False))
    if notes is not None:
        updates.append('notes = ?')
        params.append(notes)
    if mastery is not None:
        updates.append('mastery = ?')
        params.append(mastery)
    if practice_count is not None:
        updates.append('practice_count = ?')
        params.append(practice_count)
    if last_practiced_at is not None:
        updates.append('last_practiced_at = ?')
        params.append(last_practiced_at)
    if solution is not None:
        updates.append('solution = ?')
        params.append(solution)
    if updates:
        updates.append('indexed_at = ?')
        params.append(_now())
        params.append(file_path)
        conn.execute(
            f'UPDATE images SET {", ".join(updates)} WHERE file_path = ?',
            params
        )
        conn.commit()
    conn.close()


def delete_image(file_path: str):
    conn = get_db()
    conn.execute('DELETE FROM images WHERE file_path = ?', (file_path,))
    conn.commit()
    conn.close()
