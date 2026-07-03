import sqlite3
import json
import os

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
            tags TEXT DEFAULT '[]',
            indexed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
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
        result.append(d)
    return result


def mark_indexed(file_path: str, title: str, summary: str, tags: list[str]):
    conn = get_db()
    conn.execute(
        '''INSERT OR REPLACE INTO images
           (file_path, file_name, title, summary, tags, indexed_at)
           VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)''',
        (file_path, os.path.basename(file_path), title, summary, json.dumps(tags, ensure_ascii=False))
    )
    conn.commit()
    conn.close()


def update_image_meta(file_path: str, title: str | None = None,
                      summary: str | None = None, tags: list[str] | None = None):
    conn = get_db()
    updates = []
    params = []
    if title is not None:
        updates.append('title = ?')
        params.append(title)
    if summary is not None:
        updates.append('summary = ?')
        params.append(summary)
    if tags is not None:
        updates.append('tags = ?')
        params.append(json.dumps(tags, ensure_ascii=False))
    if updates:
        updates.append('indexed_at = CURRENT_TIMESTAMP')
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
