import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.schema.db import get_connection, get_database_url, with_database_name
from src.schema.migrate import run_migrations
from src.schema.seed import seed as seed_db

TEST_DB_NAME = "mnemos_test"


@pytest.fixture(scope="session")
def test_database_url():
    admin_url = get_database_url()
    test_url = with_database_name(admin_url, TEST_DB_NAME)

    conn = get_connection(admin_url, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
            cur.execute(f"CREATE DATABASE {TEST_DB_NAME}")
    finally:
        conn.close()

    yield test_url

    conn = get_connection(admin_url, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
    finally:
        conn.close()


@pytest.fixture(scope="session")
def migrated_db(test_database_url):
    run_migrations(test_database_url)
    return test_database_url


@pytest.fixture(scope="session")
def seeded_db(migrated_db):
    seed_db(migrated_db)
    return migrated_db
