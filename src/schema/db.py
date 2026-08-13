"""Connection helpers for CockroachDB: raw psycopg2 connections and a
SQLAlchemy engine/session factory, both driven by DATABASE_URL."""

import os
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

import certifi
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from pgvector.psycopg2 import register_vector
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()


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
    """Raw psycopg2 connection with the pgvector adapter registered.

    Passes sslrootcert=certifi's CA bundle when the DSN doesn't already set one:
    psycopg2-binary's bundled libpq doesn't reliably read the Windows cert store
    via sslrootcert=system, so CockroachDB Cloud's verify-full mode needs this.
    """
    url = database_url or get_database_url()
    connect_kwargs = {}
    if "sslrootcert" not in url:
        connect_kwargs["sslrootcert"] = certifi.where()

    conn = psycopg2.connect(url, connect_timeout=10, **connect_kwargs)
    conn.autocommit = autocommit
    register_vector(conn)
    psycopg2.extras.register_uuid(conn_or_curs=conn)
    return conn


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
