"""
Unified Async Database Client for Sifdine Discord Bot.
Connects to Turso Cloud Database (libSQL) via HTTP / Hrana v2 Pipeline.
Includes an automatic fallback to local aiosqlite if Turso credentials are not configured.
"""

import os
import aiohttp
import asyncio
import base64
from typing import Any, Optional, Sequence, Union, List, Dict


class Row:
    """A row object supporting both index-based and name-based column access (like sqlite3.Row)."""
    __slots__ = ('_values', '_mapping')

    def __init__(self, values: Sequence[Any], mapping: Dict[str, int]):
        self._values = tuple(values)
        self._mapping = mapping

    def __getitem__(self, item: Union[int, str]) -> Any:
        if isinstance(item, str):
            idx = self._mapping.get(item.lower())
            if idx is None:
                raise KeyError(f"No such column: '{item}'")
            return self._values[idx]
        return self._values[item]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return f"Row{self._values}"

    def get(self, key: str, default: Any = None) -> Any:
        idx = self._mapping.get(key.lower())
        if idx is None:
            return default
        return self._values[idx]

    def keys(self) -> List[str]:
        return list(self._mapping.keys())


class TursoCursor:
    """Async cursor interface matching aiosqlite."""
    def __init__(self, cols: List[Dict[str, Any]], raw_rows: List[List[Dict[str, Any]]], affected_rows: int = 0, last_insert_rowid: Optional[int] = None):
        self.col_names = [c["name"] for c in cols] if cols else []
        self._col_mapping = {name.lower(): idx for idx, name in enumerate(self.col_names)}
        self.rows: List[Row] = []
        for r in raw_rows:
            decoded_values = [TursoClient._decode_value(v) for v in r]
            self.rows.append(Row(decoded_values, self._col_mapping))
        self._iter_idx = 0
        self.rowcount = affected_rows
        self.lastrowid = last_insert_rowid

    async def fetchone(self) -> Optional[Row]:
        if self._iter_idx < len(self.rows):
            row = self.rows[self._iter_idx]
            self._iter_idx += 1
            return row
        return None

    async def fetchall(self) -> List[Row]:
        remaining = self.rows[self._iter_idx:]
        self._iter_idx = len(self.rows)
        return remaining

    def __aiter__(self):
        return self

    async def __anext__(self) -> Row:
        row = await self.fetchone()
        if row is None:
            raise StopAsyncIteration
        return row

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class _AsyncExecuteContextManager:
    """Supports both `await db.execute(...)` and `async with db.execute(...) as cursor:`."""
    __slots__ = ('client', 'sql', 'params', '_cursor')

    def __init__(self, client: 'TursoClient', sql: str, params: Sequence[Any] = ()):
        self.client = client
        self.sql = sql
        self.params = params
        self._cursor: Optional[TursoCursor] = None

    def __await__(self):
        return self.client._execute_internal(self.sql, self.params).__await__()

    async def __aenter__(self) -> TursoCursor:
        self._cursor = await self.client._execute_internal(self.sql, self.params)
        return self._cursor

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class TursoClient:
    """Async Turso Database Client using HTTP / Hrana v2 pipeline."""

    def __init__(self, database_url: str, auth_token: str):
        # Convert libsql:// to https://
        if database_url.startswith("libsql://"):
            self.base_url = "https://" + database_url[len("libsql://"):]
        elif database_url.startswith("http://") or database_url.startswith("https://"):
            self.base_url = database_url
        else:
            self.base_url = f"https://{database_url}"

        self.auth_token = auth_token
        self.session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=15, connect=5)
            self.session = aiohttp.ClientSession(timeout=timeout)

    @staticmethod
    def _encode_value(val: Any) -> Dict[str, Any]:
        if val is None:
            return {"type": "null"}
        elif isinstance(val, bool):
            return {"type": "integer", "value": "1" if val else "0"}
        elif isinstance(val, int):
            return {"type": "integer", "value": str(val)}
        elif isinstance(val, float):
            return {"type": "float", "value": val}
        elif isinstance(val, str):
            return {"type": "text", "value": val}
        elif isinstance(val, (bytes, bytearray)):
            return {"type": "blob", "base64": base64.b64encode(val).decode("ascii")}
        else:
            return {"type": "text", "value": str(val)}

    @staticmethod
    def _decode_value(raw: Dict[str, Any]) -> Any:
        v_type = raw.get("type", "null")
        if v_type == "null":
            return None
        elif v_type == "integer":
            return int(raw.get("value", 0))
        elif v_type == "float":
            return float(raw.get("value", 0.0))
        elif v_type == "text":
            return raw.get("value", "")
        elif v_type == "blob":
            b64_str = raw.get("base64", "")
            return base64.b64decode(b64_str)
        return raw.get("value")

    def _build_stmt(self, sql: str, params: Sequence[Any] = ()) -> Dict[str, Any]:
        stmt: Dict[str, Any] = {"sql": sql}
        if params:
            stmt["args"] = [self._encode_value(p) for p in params]
        return stmt

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _AsyncExecuteContextManager:
        return _AsyncExecuteContextManager(self, sql, params)

    async def _execute_internal(self, sql: str, params: Sequence[Any] = ()) -> TursoCursor:
        await self._ensure_session()
        payload = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": self._build_stmt(sql, params)
                }
            ]
        }
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }

        # Auto-retry once on transient network blip
        for attempt in range(2):
            try:
                async with self.session.post(f"{self.base_url}/v2/pipeline", headers=headers, json=payload) as resp:
                    data = await resp.json()
                    if resp.status != 200:
                        err_msg = data.get("message", str(data))
                        raise RuntimeError(f"Turso API error (HTTP {resp.status}): {err_msg}")

                    results = data.get("results", [])
                    if not results:
                        return TursoCursor([], [])

                    res = results[0]
                    if res.get("type") == "error":
                        raise RuntimeError(f"Turso SQL error: {res.get('error', {}).get('message', 'Unknown error')}")

                    exec_res = res.get("response", {}).get("result", {})
                    cols = exec_res.get("cols", [])
                    rows = exec_res.get("rows", [])
                    affected = exec_res.get("affected_row_count", 0)
                    last_id = exec_res.get("last_insert_rowid")
                    return TursoCursor(cols, rows, affected, last_id)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == 1:
                    raise e
                await asyncio.sleep(0.5)

    async def executemany(self, sql: str, params_seq: Sequence[Sequence[Any]]) -> None:
        if not params_seq:
            return
        await self._ensure_session()
        requests = [
            {"type": "execute", "stmt": self._build_stmt(sql, p)}
            for p in params_seq
        ]
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }
        async with self.session.post(f"{self.base_url}/v2/pipeline", headers=headers, json={"requests": requests}) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"Turso executemany error: {data}")

    async def commit(self) -> None:
        """No-op for HTTP pipeline as each request auto-commits by default."""
        pass

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()


async def create_database_client() -> Union[TursoClient, Any]:
    """
    Factory function to initialize the database client.
    Returns TursoClient if TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are set,
    otherwise falls back to local aiosqlite for local development.
    """
    from dotenv import load_dotenv
    load_dotenv()

    turso_url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    turso_token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()

    if turso_url and turso_token:
        print("[Database] Initializing Turso Cloud Database client (libSQL)...")
        client = TursoClient(turso_url, turso_token)
        # Test connection
        cursor = await client.execute("SELECT 1;")
        row = await cursor.fetchone()
        if row and row[0] == 1:
            print("[Database] Connected successfully to Turso Cloud Database! 🚀")
        return client

    print("[Database] Turso credentials not found. Falling back to local aiosqlite (bot_database.db)...")
    import aiosqlite
    db = await aiosqlite.connect("bot_database.db")
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    return db
