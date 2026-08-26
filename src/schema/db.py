"""Connection helpers for CockroachDB: raw psycopg2 connections and a
SQLAlchemy engine/session factory, both driven by DATABASE_URL."""

import os
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

import certifi
import psycopg2
import psycopg2.extensions
import psycopg2.extras
import psycopg2.pool
from dotenv import load_dotenv
from pgvector.psycopg2 import register_vector
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

# get_connection() used to open a brand-new connection (full verify-full TLS
# handshake to CockroachDB Cloud) on every single call - including inside
# commit.py's per-retry-attempt loop, so one contested resolution could open
# several connections in a row. Pooled per database_url below; maxconn is a
# real calibration knob (see MNEMOS_DB_POOL_MAXCONN) - the real Block 2C
# stress tests exercised up to 200 concurrent writers, each holding at most
# one connection at a time, so the default here is set well above that.
_DB_POOL_MAXCONN = int(os.environ.get("MNEMOS_DB_POOL_MAXCONN", "250"))
_pools: dict[str, psycopg2.pool.ThreadedConnectionPool] = {}
_pools_lock = threading.Lock()


class _PooledConnection:
    """Proxies every attribute to the real psycopg2 connection except
    close(), which returns it to the pool instead of tearing down the
    session. psycopg2 connection objects don't allow monkeypatching .close
    directly (it's a read-only slot on the C type), hence this wrapper
    rather than reassigning the attribute.

    Rolls back before returning to the pool if a transaction was left open
    or aborted (e.g. a caller raised without committing) - otherwise that
    state would leak into whichever caller borrows this connection next."""

    def __init__(self, pool: psycopg2.pool.ThreadedConnectionPool, conn):
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_conn", conn)

    def close(self) -> None:
        conn = self._conn
        try:
            if conn.get_transaction_status() != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
                conn.rollback()
        except psycopg2.Error:
            pass
        self._pool.putconn(conn)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


def _get_pool(url: str) -> psycopg2.pool.ThreadedConnectionPool:
    pool = _pools.get(url)
    if pool is not None:
        return pool
    with _pools_lock:
        pool = _pools.get(url)
        if pool is None:
            connect_kwargs = {}
            if "sslrootcert" not in url:
                connect_kwargs["sslrootcert"] = certifi.where()
            pool = psycopg2.pool.ThreadedConnectionPool(1, _DB_POOL_MAXCONN, url, connect_timeout=10, **connect_kwargs)
            _pools[url] = pool
    return pool


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (copy .env.example to .env and fill it in)")
    return url


def with_database_name(database_url: str, dbname: str) -> str:
    """Return database_url pointed at a different database name on the same cluster."""
    parts = urlsplit(database_url)
    return urlunsplit(parts._replace(path=f"/{dbname}"))


def get_connection(database_url: str | None = None, autocommit: bool = False):
    """A pooled psycopg2 connection (see _PooledConnection above) with the
    pgvector adapter registered. Call .close() as always - it returns the
    connection to the pool rather than closing the underlying session.

    Passes sslrootcert=certifi's CA bundle when the DSN doesn't already set one:
    psycopg2-binary's bundled libpq doesn't reliably read the Windows cert store
    via sslrootcert=system, so CockroachDB Cloud's verify-full mode needs this.
    """
    url = database_url or get_database_url()
    pool = _get_pool(url)
    conn = pool.getconn()
    conn.autocommit = autocommit
    register_vector(conn)
    psycopg2.extras.register_uuid(conn_or_curs=conn)
    return _PooledConnection(pool, conn)


@contextmanager
def connection(database_url: str | None = None, autocommit: bool = False):
    conn = get_connection(database_url, autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()


def make_engine(database_url: str | None = None, **kwargs):
    url = database_url or get_database_url()
    sqlalchemy_url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    connect_args = kwargs.pop("connect_args", {})
    if "sslrootcert" not in url:
        connect_args.setdefault("sslrootcert", certifi.where())
    return create_engine(sqlalchemy_url, connect_args=connect_args, **kwargs)


def get_sessionmaker(database_url: str | None = None, **engine_kwargs):
    return sessionmaker(bind=make_engine(database_url, **engine_kwargs), expire_on_commit=False)
