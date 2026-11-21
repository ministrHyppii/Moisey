"""Проверки прав. Главная идея:

* владелец бота (OWNER_ID) — может всё, на любом сервере;
* глава сервера (роль из настройки) — может всё на своём сервере;
* остальные — по степеням 1–3. Степень 1 наследует возможности 2 и 3, степень 2 — возможности 3.
"""
from config import OWNER_ID


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def configured(s) -> bool:
    return bool(s["mode"] and s["head_role_id"])


async def is_head(db, member) -> bool:
    s = await db.settings(member.guild.id)
    return bool(s["head_role_id"]) and any(r.id == s["head_role_id"] for r in member.roles)


async def member_degree(db, member):
    """Лучшая (наименьшая) степень среди ролей участника или None."""
    degs = await db.role_degrees(member.guild.id)
    ds = [degs[r.id] for r in member.roles if r.id in degs]
    return min(ds) if ds else None


async def has_cap(db, member, cap) -> bool:
    if is_owner(member.id) or await is_head(db, member):
        return True
    d = await member_degree(db, member)
    if d is None:
        return False
    caps = await db.degree_caps(member.guild.id)
    return any(cap in caps.get(k, ()) for k in range(d, 4))


async def is_staff(db, member) -> bool:
    return is_owner(member.id) or await is_head(db, member) or await member_degree(db, member) is not None


async def can_configure(db, member) -> bool:
    """Настраивать бота: владелец бота, глава, владелец сервера;
    пока глава не назначен — ещё и администраторы (чтобы можно было пройти первый шаг)."""
    if is_owner(member.id) or member.id == member.guild.owner_id:
        return True
    if await is_head(db, member):
        return True
    s = await db.settings(member.guild.id)
    return not s["head_role_id"] and member.guild_permissions.administrator
