import os
import time

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER PRIMARY KEY,
    mode TEXT,
    head_role_id INTEGER,
    server_name TEXT,
    gang_name TEXT,
    share INTEGER NOT NULL DEFAULT 0,
    rep_channel_id INTEGER,
    bl_channel_id INTEGER,
    bl_action TEXT NOT NULL DEFAULT 'none'
);
CREATE TABLE IF NOT EXISTS role_degrees (
    guild_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    degree INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);
CREATE TABLE IF NOT EXISTS degree_caps (
    guild_id INTEGER NOT NULL,
    degree INTEGER NOT NULL,
    cap TEXT NOT NULL,
    PRIMARY KEY (guild_id, degree, cap)
);
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id INTEGER UNIQUE,
    nickname TEXT COLLATE NOCASE UNIQUE,
    steam_id TEXT COLLATE NOCASE UNIQUE,
    steam_url TEXT,
    union_url TEXT,
    src_nickname INTEGER NOT NULL DEFAULT 0,
    src_steam_id INTEGER NOT NULL DEFAULT 0,
    src_steam_url INTEGER NOT NULL DEFAULT 0,
    src_union_url INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS punishments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    issuer_id INTEGER NOT NULL,
    issuer_text TEXT,
    reason TEXT NOT NULL,
    cond TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL,
    ended_at INTEGER,
    ended_by INTEGER,
    end_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_pun ON punishments (player_id, guild_id, status);
CREATE TABLE IF NOT EXISTS tables_posted (
    guild_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, kind)
);
"""

PLAYER_FIELDS = ("nickname", "steam_id", "steam_url", "union_url")


class Database:
    def __init__(self, path):
        self.path = path
        self.conn = None

    async def connect(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()

    # ── низкоуровневые помощники ─────────────────────────────
    async def one(self, sql, args=()):
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchone()

    async def all(self, sql, args=()):
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchall()

    async def run(self, sql, args=()):
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur.lastrowid

    # ── настройки сервера ────────────────────────────────────
    async def settings(self, gid):
        row = await self.one("SELECT * FROM guild_settings WHERE guild_id=?", (gid,))
        if row is None:
            await self.run("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (gid,))
            row = await self.one("SELECT * FROM guild_settings WHERE guild_id=?", (gid,))
        return row

    async def set_settings(self, gid, **kw):
        await self.settings(gid)
        cols = ", ".join(f"{k}=?" for k in kw)
        await self.run(f"UPDATE guild_settings SET {cols} WHERE guild_id=?", (*kw.values(), gid))

    async def shared_guilds(self):
        rows = await self.all("SELECT guild_id FROM guild_settings WHERE share=1")
        return [r["guild_id"] for r in rows]

    # ── степени и возможности ────────────────────────────────
    async def role_degrees(self, gid):
        rows = await self.all("SELECT role_id, degree FROM role_degrees WHERE guild_id=?", (gid,))
        return {r["role_id"]: r["degree"] for r in rows}

    async def bind_roles(self, gid, degree, role_ids):
        """Заменяет список ролей степени. Роль, уже привязанная к другой степени, переезжает."""
        await self.conn.execute("DELETE FROM role_degrees WHERE guild_id=? AND degree=?", (gid, degree))
        for rid in role_ids:
            await self.conn.execute("INSERT OR REPLACE INTO role_degrees VALUES (?,?,?)", (gid, rid, degree))
        await self.conn.commit()

    async def bind_role(self, gid, role_id, degree):
        if degree is None:
            await self.run("DELETE FROM role_degrees WHERE guild_id=? AND role_id=?", (gid, role_id))
        else:
            await self.run("INSERT OR REPLACE INTO role_degrees VALUES (?,?,?)", (gid, role_id, degree))

    async def degree_caps(self, gid):
        out = {1: set(), 2: set(), 3: set()}
        for r in await self.all("SELECT degree, cap FROM degree_caps WHERE guild_id=?", (gid,)):
            out.setdefault(r["degree"], set()).add(r["cap"])
        return out

    async def set_caps(self, gid, degree, caps):
        await self.conn.execute("DELETE FROM degree_caps WHERE guild_id=? AND degree=?", (gid, degree))
        for c in caps:
            await self.conn.execute("INSERT OR IGNORE INTO degree_caps VALUES (?,?,?)", (gid, degree, c))
        await self.conn.commit()

    # ── игроки ───────────────────────────────────────────────
    async def player(self, pid):
        return await self.one("SELECT * FROM players WHERE id=?", (pid,))

    async def player_by_discord(self, did):
        return await self.one("SELECT * FROM players WHERE discord_id=?", (did,))

    async def player_by_nick(self, nick):
        return await self.one("SELECT * FROM players WHERE nickname=?", (nick,))

    async def player_by_steam(self, steam_id):
        return await self.one("SELECT * FROM players WHERE steam_id=?", (steam_id,))

    async def create_player(self, discord_id=None, nickname=None, steam_id=None, src=0):
        pid = await self.run(
            "INSERT INTO players (discord_id, nickname, steam_id, src_nickname, src_steam_id, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (discord_id, nickname, steam_id, src if nickname else 0, src if steam_id else 0, int(time.time())),
        )
        return await self.player(pid)

    async def set_field(self, pid, field, value, src):
        assert field in PLAYER_FIELDS
        await self.run(f"UPDATE players SET {field}=?, src_{field}=? WHERE id=?", (value, src, pid))

    async def merge(self, drop_id, keep_id):
        """Вливает карточку drop в keep (наказания переезжают, пустые поля заполняются)."""
        drop, keep = await self.player(drop_id), await self.player(keep_id)
        await self.conn.execute("UPDATE punishments SET player_id=? WHERE player_id=?", (keep_id, drop_id))
        await self.conn.execute("DELETE FROM players WHERE id=?", (drop_id,))
        sets = {"discord_id": drop["discord_id"]}
        for f in ("steam_id", "steam_url", "union_url"):
            if keep[f] is None and drop[f] is not None:
                sets[f] = drop[f]
                sets["src_" + f] = drop["src_" + f]
        cols = ", ".join(f"{k}=?" for k in sets)
        await self.conn.execute(f"UPDATE players SET {cols} WHERE id=?", (*sets.values(), keep_id))
        await self.conn.commit()

    async def set_nickname(self, pid, nick, src):
        """-> (ok, сообщение|None, id выжившей карточки)"""
        me = await self.player(pid)
        if nick:
            other = await self.player_by_nick(nick)
            if other and other["id"] != pid:
                if other["discord_id"] is None and me["discord_id"] is not None:
                    await self.merge(pid, other["id"])
                    await self.set_field(other["id"], "nickname", nick, src)
                    return True, "карточка объединена с ранее созданной по этому нику", other["id"]
                return False, "этот ник уже занят другой карточкой", pid
        await self.set_field(pid, "nickname", nick, src)
        return True, None, pid

    # ── наказания ────────────────────────────────────────────
    async def add_punishment(self, gid, pid, kind, issuer_id, issuer_text, reason, cond=None):
        return await self.run(
            "INSERT INTO punishments (guild_id, player_id, kind, issuer_id, issuer_text, reason, cond, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (gid, pid, kind, issuer_id, issuer_text, reason, cond, int(time.time())),
        )

    async def active(self, gid, pid, kind):
        return await self.all(
            "SELECT * FROM punishments WHERE guild_id=? AND player_id=? AND kind=? AND status='active' ORDER BY id",
            (gid, pid, kind),
        )

    async def end(self, ids, status, by=None, reason=None):
        if not ids:
            return
        q = ",".join("?" * len(ids))
        await self.run(
            f"UPDATE punishments SET status=?, ended_at=?, ended_by=?, end_reason=? WHERE id IN ({q})",
            (status, int(time.time()), by, reason, *ids),
        )

    async def visible(self, pid, gid, statuses=("active",), everything=False):
        """Наказания, которые можно показать на сервере gid: свои + сервера с включённым обменом."""
        st = ",".join("?" * len(statuses))
        if everything:
            return await self.all(
                f"SELECT * FROM punishments WHERE player_id=? AND status IN ({st}) ORDER BY created_at DESC, id DESC",
                (pid, *statuses),
            )
        gids = list({gid, *await self.shared_guilds()})
        g = ",".join("?" * len(gids))
        return await self.all(
            f"SELECT * FROM punishments WHERE player_id=? AND guild_id IN ({g}) AND status IN ({st})"
            " ORDER BY created_at DESC, id DESC",
            (pid, *gids, *statuses),
        )

    # ── опубликованные таблицы ───────────────────────────────
    async def save_table(self, gid, kind, channel_id, message_id):
        await self.run("INSERT OR REPLACE INTO tables_posted VALUES (?,?,?,?)", (gid, kind, channel_id, message_id))

    async def get_table(self, gid, kind):
        return await self.one("SELECT * FROM tables_posted WHERE guild_id=? AND kind=?", (gid, kind))
