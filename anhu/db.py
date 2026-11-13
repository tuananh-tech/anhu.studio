"""Kết nối Postgres. Mỗi transaction gắn app.user_id -> RLS trong DB quyết định quyền truy cập."""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine, text

_engines = {}


def _url(u: str) -> str:
    return u.replace("postgresql://", "postgresql+psycopg://", 1) if u.startswith("postgresql://") else u


def engine(url: str):
    if url not in _engines:
        # connect_timeout: máy chủ CSDL không phản hồi -> báo lỗi thân thiện sau vài giây thay vì treo trang.
        _engines[url] = create_engine(_url(url), pool_pre_ping=True, future=True, pool_recycle=1800,
                                      connect_args={"connect_timeout": int(os.environ.get("ANHU_DB_CONNECT_TIMEOUT", "5"))})
    return _engines[url]


@contextmanager
def tx(url: str, uid=None, reason: str | None = None):
    with engine(url).begin() as conn:
        conn.execute(text("SELECT set_config('app.user_id', :u, true), set_config('app.reason', :r, true)"),
                     {"u": str(uid) if uid else "", "r": reason or ""})
        yield conn


def rows(conn, sql, **params):
    return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def one(conn, sql, **params):
    r = conn.execute(text(sql), params).first()
    return dict(r._mapping) if r else None


def scalar(conn, sql, **params):
    return conn.execute(text(sql), params).scalar()
