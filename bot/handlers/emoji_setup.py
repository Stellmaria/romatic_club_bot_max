from aiogram import Router, F, types
from aiogram.filters import Command
from html import escape

# импортируй ИМЕННО оттуда, где у тебя helpers БД (судя по main.py — из db.db)
from db.db import execute, fetch, fetchrow

router = Router(name="emoji_setup")

def _first_custom_emoji_id(msg: types.Message) -> str | None:
    # 1) текстовые custom emoji
    for e in (msg.entities or []):
        if e.type == "custom_emoji" and e.custom_emoji_id:
            return e.custom_emoji_id
    for e in (getattr(msg, "caption_entities", None) or []):
        if e.type == "custom_emoji" and e.custom_emoji_id:
            return e.custom_emoji_id
    # 2) стикер-эмодзи (premium custom emoji прислан как sticker)
    st = getattr(msg, "sticker", None)
    if st and getattr(st, "is_custom_emoji", False) and getattr(st, "custom_emoji_id", None):
        return st.custom_emoji_id
    return None

@router.message(Command("em_migrate"))
async def em_migrate(message: types.Message):
    try:
        await execute("""
            CREATE TABLE IF NOT EXISTS custom_emojis (
              name TEXT PRIMARY KEY,
              emoji_id TEXT NOT NULL UNIQUE
            )
        """)
        await message.answer("✅ Таблица custom_emojis готова.")
    except Exception as e:
        await message.answer(f"❌ Не удалось создать таблицу: <code>{escape(str(e))}</code>", parse_mode="HTML")

@router.message(Command("em_add"))
async def em_add(message: types.Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not message.reply_to_message:
        await message.answer("Ответь на СООБЩЕНИЕ с прем-эмодзи и напиши: /em_add <name>")
        return
    name = parts[1].strip().lower()
    emoji_id = _first_custom_emoji_id(message.reply_to_message)
    if not emoji_id:
        await message.answer("В реплае не вижу premium custom emoji. Пришли именно прем-эмодзи, не обычный Unicode.")
        return
    try:
        await execute(
            "INSERT INTO custom_emojis(name, emoji_id) VALUES($1,$2) "
            "ON CONFLICT (name) DO UPDATE SET emoji_id=EXCLUDED.emoji_id",
            name, emoji_id
        )
    except Exception as e:
        await message.answer(
            "❌ Ошибка БД: <code>{}</code>\nПодозрение: нет таблицы — запусти /em_migrate."
            .format(escape(str(e))), parse_mode="HTML")
        return
    await message.answer(f"✅ Сохранено: {name} → <code>{emoji_id}</code>", parse_mode="HTML")

@router.message(Command("em_list"))
async def em_list(message: types.Message):
    try:
        rows = await fetch("SELECT name, emoji_id FROM custom_emojis ORDER BY name")
    except Exception as e:
        await message.answer(f"❌ Ошибка БД: <code>{escape(str(e))}</code>", parse_mode="HTML")
        return
    if not rows:
        await message.answer("Пусто. Добавь через /em_add по реплаю на прем-эмодзи.")
        return
    text = "<b>Сохранённые эмодзи</b>\n" + "\n".join(
        f"• <b>{r['name']}</b> → <code>{r['emoji_id']}</code>" for r in rows
    )
    await message.answer(text, parse_mode="HTML")

@router.message(Command("em_del"))
async def em_del(message: types.Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /em_del <name>")
        return
    name = parts[1].strip().lower()
    row = await fetchrow("DELETE FROM custom_emojis WHERE name=$1 RETURNING name", name)
    await message.answer("🗑 Удалено: {}".format(row["name"]) if row else "Такого имени нет.")
