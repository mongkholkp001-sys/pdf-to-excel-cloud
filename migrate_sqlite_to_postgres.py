"""One-time migration of system.db data into DATABASE_URL PostgreSQL.
Run only after DATABASE_URL is configured. Existing destination rows are not duplicated.
"""
import os, sqlite3
from pathlib import Path
import psycopg2

BASE = Path(__file__).resolve().parent
SQLITE_PATH = Path(os.environ.get('SQLITE_SOURCE', BASE / 'system.db'))
DATABASE_URL = os.environ.get('DATABASE_URL', '').replace('postgres://', 'postgresql://', 1)
TABLES = ['users', 'conversion_logs', 'extracted_records', 'user_devices', 'system_settings']

if not SQLITE_PATH.exists():
    raise SystemExit(f'SQLite source not found: {SQLITE_PATH}')
if not DATABASE_URL:
    raise SystemExit('DATABASE_URL is not set')

src = sqlite3.connect(SQLITE_PATH)
src.row_factory = sqlite3.Row
dst = psycopg2.connect(DATABASE_URL)
try:
    with dst.cursor() as cur:
        for table in TABLES:
            rows = src.execute(f'SELECT * FROM {table}').fetchall()
            if not rows:
                print(f'{table}: 0 rows')
                continue
            cols = rows[0].keys()
            col_sql = ','.join(cols)
            placeholders = ','.join(['%s'] * len(cols))
            conflict = 'key' if table == 'system_settings' else 'id'
            updates = ','.join(f'{c}=EXCLUDED.{c}' for c in cols if c != conflict)
            sql = f'INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) ON CONFLICT ({conflict}) DO UPDATE SET {updates}'
            for row in rows:
                cur.execute(sql, tuple(row[c] for c in cols))
            print(f'{table}: {len(rows)} rows')
        # Advance PostgreSQL sequences after explicit ID inserts.
        for table in TABLES[:-1]:
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'), COALESCE((SELECT MAX(id) FROM {table}),1), true)")
    dst.commit()
finally:
    src.close(); dst.close()
print('Migration completed successfully.')
