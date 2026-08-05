#!/usr/bin/env python3
from __future__ import annotations

"""Initialize the stock discovery SQLite database."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.db import DB_PATH, db_session, initialize_database, list_tables, table_columns


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化股票发现系统 SQLite 数据库")
    parser.add_argument("--db", default=str(DB_PATH), help="数据库路径，默认 data/stock_discovery.db")
    args = parser.parse_args()

    db_path = Path(args.db)
    initialize_database(db_path)

    with db_session(db_path) as conn:
        tables = list_tables(conn)
        print(f"数据库已初始化: {db_path}")
        print(f"表数量: {len(tables)}")
        for table in tables:
            print(f"  {table}: {len(table_columns(conn, table))} columns")


if __name__ == "__main__":
    main()

