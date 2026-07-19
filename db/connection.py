"""
Opens the project's DuckDB file and ensures the schema exists.

Every other module should get its connection through get_connection()
rather than opening its own - this keeps the connection logic in one
place if it ever needs to change (e.g. read-only mode, a different
file location).
"""

import duckdb

from config import DB_PATH, SCHEMA_PATH


def get_connection() -> duckdb.DuckDBPyConnection:
    """
    Open (or create) the project's DuckDB file and make sure all tables
    from db/schema.sql exist. Safe to call repeatedly - CREATE TABLE IF
    NOT EXISTS statements in the schema mean this never overwrites data.
    """
    con = duckdb.connect(str(DB_PATH))

    with open(SCHEMA_PATH, "r") as f:
        schema_sql = f.read()
    con.execute(schema_sql)

    return con
