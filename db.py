"""Neon PostgreSQL இணைப்பு — connection pool + அறுந்த இணைப்புக்கு ஒரு முறை மீள்முயற்சி."""
import os
import threading

from psycopg2 import pool, OperationalError, InterfaceError
from psycopg2.extras import RealDictCursor

_pool = None
_lock = threading.Lock()


def _get_pool():
    global _pool
    with _lock:
        if _pool is None:
            _pool = pool.ThreadedConnectionPool(
                1, int(os.environ.get("DB_POOL_MAX", "4")),
                os.environ["DATABASE_URL"],
                keepalives=1, keepalives_idle=30,
                keepalives_interval=10, keepalives_count=3,
                connect_timeout=15,
            )
        return _pool


def run(fn):
    """fn(conn) ஒரே transaction-ல் இயங்கும். வெற்றி -> commit, பிழை -> rollback.
    Neon idle ஆனபின் இணைப்பு அறுந்திருந்தால் புதிய இணைப்புடன் ஒருமுறை மீண்டும் முயலும்."""
    last = None
    for _ in range(2):
        p = _get_pool()
        conn = p.getconn()
        discard = False
        try:
            result = fn(conn)
            conn.commit()
            return result
        except (OperationalError, InterfaceError) as e:
            last = e
            discard = True
            try:
                conn.rollback()
            except Exception:
                pass
        except Exception:
            try:
                conn.rollback()
            except Exception:
                discard = True
            raise
        finally:
            p.putconn(conn, close=discard)
    raise last


def fetch_all(sql, params=None):
    def f(conn):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    return run(f)


def fetch_one(sql, params=None):
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    def f(conn):
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount
    return run(f)
