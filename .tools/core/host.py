from __future__ import annotations

import sqlite3
import sys

from . import registry, storage
from .constants import python_support
from .instance import InstanceContext


def status(context: InstanceContext) -> dict:
    db_path = storage.bootstrap(context)
    connection = sqlite3.connect(db_path)
    try:
        database_uuid = connection.execute("SELECT instance_uuid FROM instances").fetchone()[0]
        database_schema = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()
    tool_count = len(registry.discover(context))
    support, detail = python_support()
    return {
        "ok": True,
        "bound": True,
        "instance_uuid": context.instance_uuid,
        "instance_schema": context.schema_version,
        "database_schema": database_schema,
        "database_identity_matches": database_uuid == context.instance_uuid,
        "product_version": context.product_version,
        "target_relation": context.target_relation,
        "target_root": str(context.target_root),
        "state_database": "state/workbench.sqlite3",
        "tool_count": tool_count,
        "python": {
            "version": "%d.%d.%d" % sys.version_info[:3],
            "support": support,
        },
        "limitations": [] if detail is None else [detail],
    }
