"""Share a finished run with a teammate: one zip with its checkpoints, agent-bus messages and media.

    python -m malav.share export client_909b00769f          -> media/client_909b00769f.zip
    python -m malav.share import path/to/client_909b00769f.zip

Import is additive (insert-or-ignore) and copies media into backend/media/, so the run shows up
in Mission Control, Creative Studio and the Agent Theater exactly where it was left.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

from malav.media import MEDIA

CHECKPOINTS = os.getenv("RUNE_CHECKPOINTS", "checkpoints.db")
BUS = os.getenv("RUNE_DB", "rune.db")
# name inside the zip -> (local db, tables keyed by the run id)
TABLES = {"checkpoints.db": (CHECKPOINTS, [("checkpoints", "thread_id"), ("writes", "thread_id")]),
          "bus.db": (BUS, [("agent_messages", "client_id"), ("runs", "client_id"), ("insights", "client_id"), ("optimizer_actions", "client_id")])}


def _copy(src: str, dst: sqlite3.Connection, cid: str, tables: list[tuple[str, str]]) -> int:
    """Copy one run's rows from src (a db path) into dst, creating tables as in src."""
    dst.execute("attach database ? as src", (src,))
    n = 0
    for table, key in tables:
        sql = dst.execute("select sql from src.sqlite_master where type='table' and name=?", (table,)).fetchone()
        if not sql:
            continue
        dst.execute(sql[0].replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1).replace("create table", "create table if not exists", 1))
        n += dst.execute(f"insert or ignore into main.{table} select * from src.{table} where {key} = ?", (cid,)).rowcount
    dst.commit()
    dst.execute("detach database src")
    return n


def export(cid: str) -> Path:
    out = MEDIA / f"{cid}.zip"
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, (db, tables) in TABLES.items():
            part = Path(tmp) / name
            with closing(sqlite3.connect(part)) as con:
                rows = _copy(db, con, cid, tables)
            z.write(part, f"db/{name}")
            print(f"{db}: {rows} rows")
        for f in (MEDIA / cid).rglob("*"):
            if f.is_file():
                z.write(f, f"media/{cid}/{f.relative_to(MEDIA / cid).as_posix()}")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return out


def import_(path: str) -> None:
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(path) as z:
        z.extractall(tmp)
        cid = next(Path(tmp, "media").iterdir()).name
        for name, (db, tables) in TABLES.items():
            part = Path(tmp, "db", name)
            if part.exists():
                with closing(sqlite3.connect(db)) as con:
                    print(f"{db}: {_copy(str(part), con, cid, tables)} rows added")
        shutil.copytree(Path(tmp, "media", cid), MEDIA / cid, dirs_exist_ok=True)
    print(f"imported {cid}: open http://localhost:3000/run?client={cid}")


if __name__ == "__main__":
    cmd, arg = sys.argv[1], sys.argv[2]
    export(arg) if cmd == "export" else import_(arg)
