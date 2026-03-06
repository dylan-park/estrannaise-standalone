"""SQLite database — adapted from HA integration, config_entry_id removed."""

from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path

import aiosqlite

from .pk import (
    PATCH_WEAR_DAYS,
    PK_PARAMETERS,
    compute_e2_at_time,
    compute_steady_state_e2_at_time,
)

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS doses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    model TEXT NOT NULL,
    dose_mg REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS blood_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    level_pg_ml REAL NOT NULL,
    notes TEXT,
    on_schedule INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doses_ts ON doses(timestamp);
CREATE INDEX IF NOT EXISTS idx_blood_tests_ts ON blood_tests(timestamp);
"""


class EstrannaisDatabase:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def async_setup(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA busy_timeout = 5000")
        await self._db.executescript(CREATE_TABLES)
        await self._db.commit()
        cur = await self._db.execute("PRAGMA user_version")
        row = await cur.fetchone()
        if (row[0] if row else 0) < 2:
            try:
                await self._db.execute(
                    "ALTER TABLE blood_tests ADD COLUMN on_schedule INTEGER"
                )
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    raise
            await self._db.execute("PRAGMA user_version = 2")
            await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ── Doses ────────────────────────────────────────────────────────────────

    async def get_all_doses(self) -> list[dict]:
        cur = await self._db.execute(
            "SELECT id, timestamp, model, dose_mg, source FROM doses ORDER BY timestamp"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def add_dose(
        self, model: str, dose_mg: float, timestamp: float, source: str = "manual"
    ) -> int:
        async with self._write_lock:
            cur = await self._db.execute(
                "INSERT INTO doses (timestamp, model, dose_mg, source, created_at) VALUES (?,?,?,?,?)",
                (timestamp, model, dose_mg, source, time.time()),
            )
            await self._db.commit()
            return cur.lastrowid

    async def delete_dose(self, dose_id: int) -> bool:
        async with self._write_lock:
            cur = await self._db.execute("DELETE FROM doses WHERE id=?", (dose_id,))
            await self._db.commit()
            return cur.rowcount > 0

    async def get_auto_dose_timestamps(self) -> set[float]:
        cur = await self._db.execute(
            "SELECT timestamp FROM doses WHERE source='automatic'"
        )
        return {r[0] for r in await cur.fetchall()}

    async def prune_stale_doses(self, retention_days: float = 90.0) -> None:
        if retention_days <= 0:
            return
        cutoff = time.time() - retention_days * 86400
        async with self._write_lock:
            await self._db.execute(
                "DELETE FROM doses WHERE source='automatic' AND timestamp<?", (cutoff,)
            )
            await self._db.commit()

    # ── Blood tests ──────────────────────────────────────────────────────────

    async def get_all_blood_tests(self) -> list[dict]:
        cur = await self._db.execute(
            "SELECT id, timestamp, level_pg_ml, notes, on_schedule FROM blood_tests ORDER BY timestamp"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def add_blood_test(
        self,
        level_pg_ml: float,
        timestamp: float,
        notes: str | None = None,
        on_schedule: bool | None = None,
    ) -> int:
        async with self._write_lock:
            cur = await self._db.execute(
                "INSERT INTO blood_tests (timestamp, level_pg_ml, notes, on_schedule, created_at) VALUES (?,?,?,?,?)",
                (
                    timestamp,
                    level_pg_ml,
                    notes,
                    None if on_schedule is None else int(on_schedule),
                    time.time(),
                ),
            )
            await self._db.commit()
            return cur.lastrowid

    async def delete_blood_test(self, test_id: int) -> bool:
        async with self._write_lock:
            cur = await self._db.execute(
                "DELETE FROM blood_tests WHERE id=?", (test_id,)
            )
            await self._db.commit()
            return cur.rowcount > 0

    async def clear_all(self) -> None:
        async with self._write_lock:
            await self._db.execute("DELETE FROM doses")
            await self._db.execute("DELETE FROM blood_tests")
            await self._db.commit()

    # ── Scaling factor ───────────────────────────────────────────────────────

    async def compute_scaling_factor(
        self, all_doses: list[dict], all_configs: list[dict]
    ) -> tuple[float, float]:
        blood_tests = await self.get_all_blood_tests()
        if not blood_tests:
            return 1.0, 0.0
        now = time.time()
        weights, ratios = [], []
        for bt in blood_tests:
            measured = bt["level_pg_ml"]
            if measured <= 0:
                continue
            predicted = compute_e2_at_time(bt["timestamp"], all_doses)
            if predicted < 1.0 and not bt.get("on_schedule") and all_configs:
                predicted = compute_steady_state_e2_at_time(
                    bt["timestamp"], all_configs
                )
            if predicted < 1.0:
                continue
            age_days = max(0, (now - bt["timestamp"]) / 86400)
            weights.append(math.exp(-0.05 * age_days))
            ratios.append(measured / predicted)
        if not weights:
            return 1.0, 0.0
        total_w = sum(weights)
        scaling = max(
            0.0, min(2.0, sum(w * r for w, r in zip(weights, ratios)) / total_w)
        )
        variance = 0.0
        if len(weights) >= 2:
            variance = (
                sum(w * (r - scaling) ** 2 for w, r in zip(weights, ratios)) / total_w
            )
        return round(scaling, 4), round(variance, 6)
