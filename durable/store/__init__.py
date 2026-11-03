"""Persistence layer.

M0 only needs connection handling and the DDL. Repository functions (create
workflow, transition a job run, append events) arrive in M1, and they must go
through :mod:`durable.model` rather than issuing raw ``UPDATE ... SET state``.
"""

from .db import connect, default_db_path, initialize, open_project
from .schema import DDL, EXPECTED_TABLES, SCHEMA_VERSION, apply, current_version

__all__ = [
    "DDL",
    "EXPECTED_TABLES",
    "SCHEMA_VERSION",
    "apply",
    "connect",
    "current_version",
    "default_db_path",
    "initialize",
    "open_project",
]
