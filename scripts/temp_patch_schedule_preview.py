from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:80]!r}")
    file_path.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "userbot/schedule_publication.py",
    "from telethon import TelegramClient\n",
    "from telethon import Button, TelegramClient\n",
)
replace_once(
    "userbot/schedule_publication.py",
    "    get_emoji_assets,\n    get_publication_review,\n    get_schedule_lots_for_day,\n    mark_publication_published,\n",
    "    get_emoji_assets,\n    get_preview_target,\n    get_publication_review,\n    get_schedule_lots_for_day,\n    mark_publication_published,\n    record_pending_preview,\n",
)
replace_once(
    "userbot/schedule_publication.py",
    '''async def preview_schedule_announcement(\n    target_date: date,\n    *,\n    config: UserbotSettings,\n) -> RenderedScheduleAnnouncement | None:\n    return await base.preview_schedule_announcement(target_date, config=config)\n\n\ndef _as_moscow(value: datetime) -> datetime:\n''',
    '''async def preview_schedule_announcement(\n    target_date: date,\n    *,\n    config: UserbotSettings,\n) -> RenderedScheduleAnnouncement | None:\n    return await base.preview_schedule_announcement(target_date, config=config)\n\n\ndef render_schedule_configuration_warning(\n    target_date: date,\n    issues: Sequence[str],\n) -> str:\n    trimmed = "; ".join(issues)[:3500]\n    return (\n        "⚠️ Расписание показано, но данные заполнены не полностью\\n\\n"\n        f"Дата: {target_date:%d.%m.%Y}\\n"\n        f"Нужно исправить: {trimmed}\\n\\n"\n        "Исправьте карточки через /schedule_setup и проверьте /schedule_audit."\n    )\n\n\nasync def schedule_preview_issues(target_date: date) -> tuple[str, ...]:\n    lots = await get_schedule_lots_for_day(target_date)\n    if not lots:\n        return ()\n    assets = await get_emoji_assets()\n    return base.schedule_configuration_issues(lots, assets)\n\n\nasync def _send_schedule_configuration_warning(\n    telegram_client: TelegramClient,\n    *,\n    chat_id: int,\n    thread_id: int | None,\n    target_date: date,\n    issues: Sequence[str],\n) -> None:\n    await telegram_client.send_message(\n        int(chat_id),\n        render_schedule_configuration_warning(target_date, issues),\n        link_preview=False,\n        reply_to=int(thread_id) if thread_id else None,\n    )\n\n\nasync def send_schedule_review_preview(\n    telegram_client: TelegramClient,\n    target_date: date,\n) -> int | None:\n    existing = await get_publication_review(target_date)\n    if existing and existing.get("preview_message_id"):\n        return int(existing["preview_message_id"])\n\n    target = await get_preview_target()\n    if not target:\n        raise ScheduleEmojiConfigurationError(\n            "не настроена админская ветка для проверки расписания"\n        )\n\n    lots = await get_schedule_lots_for_day(target_date)\n    if not lots:\n        return None\n    assets = await get_emoji_assets()\n    issues = base.schedule_configuration_issues(lots, assets)\n    rendered = render_schedule_announcement(target_date, lots, assets)\n    buttons = [\n        [\n            Button.inline(\n                "✅ Всё верно",\n                data=f"sched:approve:{target_date.isoformat()}".encode(),\n            ),\n            Button.inline(\n                "❌ Отклонить",\n                data=f"sched:reject:{target_date.isoformat()}".encode(),\n            ),\n        ]\n    ]\n    chat_id = int(target["chat_id"])\n    thread_id = int(target["thread_id"]) if target.get("thread_id") else None\n    message = await telegram_client.send_message(\n        chat_id,\n        rendered.text,\n        formatting_entities=list(rendered.entities),\n        buttons=buttons,\n        link_preview=False,\n        reply_to=thread_id,\n    )\n    await record_pending_preview(\n        target_date,\n        chat_id=chat_id,\n        thread_id=thread_id,\n        message_id=int(message.id),\n    )\n    if issues:\n        await _send_schedule_configuration_warning(\n            telegram_client,\n            chat_id=chat_id,\n            thread_id=thread_id,\n            target_date=target_date,\n            issues=issues,\n        )\n    return int(message.id)\n\n\ndef _as_moscow(value: datetime) -> datetime:\n''',
)
replace_once(
    "userbot/schedule_publication.py",
    "                        preview_message_id = await base.send_schedule_review_preview(\n",
    "                        preview_message_id = await send_schedule_review_preview(\n",
)
replace_once(
    "userbot/schedule_publication.py",
    '    "preview_schedule_announcement",\n    "publish_schedule_announcement",\n    "render_schedule_announcement",\n',
    '    "preview_schedule_announcement",\n    "publish_schedule_announcement",\n    "render_schedule_announcement",\n    "render_schedule_configuration_warning",\n',
)
replace_once(
    "userbot/schedule_publication.py",
    '    "schedule_announcement_watchdog",\n    "schedule_publication_is_ready",\n',
    '    "schedule_announcement_watchdog",\n    "schedule_preview_issues",\n    "schedule_publication_is_ready",\n',
)
replace_once(
    "userbot/schedule_publication.py",
    '    "schedule_publication_ready_at",\n    "store_emoji_assignments",\n',
    '    "schedule_publication_ready_at",\n    "send_schedule_review_preview",\n    "store_emoji_assignments",\n',
)

replace_once(
    "userbot/schedule_review_service.py",
    '''async def get_schedule_review(target_date: date) -> dict[str, Any] | None:\n    return await get_publication_review(target_date)\n\n\nasync def decide_schedule_review(\n''',
    '''async def get_schedule_review(target_date: date) -> dict[str, Any] | None:\n    return await get_publication_review(target_date)\n\n\nasync def get_schedule_review_target() -> dict[str, Any] | None:\n    return await get_preview_target()\n\n\nasync def decide_schedule_review(\n''',
)
replace_once(
    "userbot/schedule_review_service.py",
    '    "get_schedule_review",\n    "schedule_review_snapshot",\n',
    '    "get_schedule_review",\n    "get_schedule_review_target",\n    "schedule_review_snapshot",\n',
)

replace_once(
    "userbot/handlers/schedule_admin.py",
    '''    missing_required_emoji_keys,\n    preview_schedule_announcement,\n    store_emoji_assignments,\n''',
    '''    missing_required_emoji_keys,\n    preview_schedule_announcement,\n    render_schedule_configuration_warning,\n    schedule_preview_issues,\n    store_emoji_assignments,\n''',
)
replace_once(
    "userbot/handlers/schedule_admin.py",
    '''    decide_schedule_review,\n    get_schedule_review,\n    schedule_review_snapshot,\n''',
    '''    decide_schedule_review,\n    get_schedule_review,\n    get_schedule_review_target,\n    schedule_review_snapshot,\n''',
)
replace_once(
    "userbot/handlers/schedule_admin.py",
    '''async def on_schedule_admin_command(\n    event: events.NewMessage.Event,\n    *,\n    config: UserbotSettings,\n) -> None:\n    if not getattr(event, "is_private", False):\n        return\n    if not await _is_authorized(event, config):\n        return\n''',
    '''def _event_thread_id(event: object) -> int | None:\n    message = getattr(event, "message", None)\n    reply_to = getattr(message, "reply_to", None)\n    top_id = getattr(reply_to, "reply_to_top_id", None)\n    if top_id:\n        return int(top_id)\n    if getattr(reply_to, "forum_topic", False):\n        reply_id = getattr(reply_to, "reply_to_msg_id", None)\n        if reply_id:\n            return int(reply_id)\n    return None\n\n\nasync def _is_allowed_command_chat(event: object) -> bool:\n    if getattr(event, "is_private", False):\n        return True\n    target = await get_schedule_review_target()\n    if not target:\n        return False\n    chat_id = getattr(event, "chat_id", None)\n    if not chat_id or int(chat_id) != int(target["chat_id"]):\n        return False\n    configured_thread = target.get("thread_id")\n    if not configured_thread:\n        return True\n    return _event_thread_id(event) == int(configured_thread)\n\n\nasync def on_schedule_admin_command(\n    event: events.NewMessage.Event,\n    *,\n    config: UserbotSettings,\n) -> None:\n    if not await _is_authorized(event, config):\n        return\n    if not await _is_allowed_command_chat(event):\n        return\n''',
)
replace_once(
    "userbot/handlers/schedule_admin.py",
    '''        await event.client.send_message(\n            event.chat_id,\n            rendered.text,\n            formatting_entities=list(rendered.entities),\n            link_preview=False,\n        )\n        return\n''',
    '''        reply_to = (\n            int(event.message.id) if not getattr(event, "is_private", False) else None\n        )\n        await event.client.send_message(\n            event.chat_id,\n            rendered.text,\n            formatting_entities=list(rendered.entities),\n            link_preview=False,\n            reply_to=reply_to,\n        )\n        issues = await schedule_preview_issues(target_date)\n        if issues:\n            await event.client.send_message(\n                event.chat_id,\n                render_schedule_configuration_warning(target_date, issues),\n                link_preview=False,\n                reply_to=reply_to,\n            )\n        return\n''',
)

access_test = Path("tests/test_schedule_admin_access.py")
access_content = access_test.read_text(encoding="utf-8")
access_content += '''\n\n@pytest.mark.asyncio\nasync def test_schedule_command_is_allowed_in_configured_admin_thread(monkeypatch) -> None:\n    async def fake_target():\n        return {"chat_id": -100123, "thread_id": 77}\n\n    monkeypatch.setattr(schedule_admin, "get_schedule_review_target", fake_target)\n    event = SimpleNamespace(\n        is_private=False,\n        chat_id=-100123,\n        message=SimpleNamespace(\n            reply_to=SimpleNamespace(\n                reply_to_top_id=77,\n                forum_topic=True,\n                reply_to_msg_id=77,\n            )\n        ),\n    )\n\n    assert await schedule_admin._is_allowed_command_chat(event)\n\n\n@pytest.mark.asyncio\n@pytest.mark.parametrize(\n    ("chat_id", "thread_id"),\n    [(-100999, 77), (-100123, 88)],\n)\nasync def test_schedule_command_is_rejected_outside_configured_admin_thread(\n    monkeypatch,\n    chat_id: int,\n    thread_id: int,\n) -> None:\n    async def fake_target():\n        return {"chat_id": -100123, "thread_id": 77}\n\n    monkeypatch.setattr(schedule_admin, "get_schedule_review_target", fake_target)\n    event = SimpleNamespace(\n        is_private=False,\n        chat_id=chat_id,\n        message=SimpleNamespace(\n            reply_to=SimpleNamespace(\n                reply_to_top_id=thread_id,\n                forum_topic=True,\n                reply_to_msg_id=thread_id,\n            )\n        ),\n    )\n\n    assert not await schedule_admin._is_allowed_command_chat(event)\n'''
access_test.write_text(access_content, encoding="utf-8")

Path("tests/test_schedule_preview_graceful_degradation.py").write_text(
    '''# ruff: noqa: RUF001\nfrom __future__ import annotations\n\nfrom datetime import UTC, date, datetime\nfrom types import SimpleNamespace\nfrom unittest.mock import AsyncMock\n\nimport pytest\n\nfrom userbot import schedule_publication\n\n\nclass FakeClient:\n    def __init__(self) -> None:\n        self.calls: list[tuple[int, str, dict[str, object]]] = []\n\n    async def send_message(\n        self,\n        chat_id: int,\n        text: str,\n        **kwargs: object,\n    ) -> SimpleNamespace:\n        self.calls.append((chat_id, text, kwargs))\n        return SimpleNamespace(id=100 + len(self.calls))\n\n\n@pytest.mark.asyncio\nasync def test_incomplete_schedule_sends_preview_then_separate_warning(monkeypatch) -> None:\n    target_date = date(2026, 8, 6)\n    lots = [\n        {\n            "auction_id": 9244,\n            "hero_name": "Неизвестная карточка",\n            "card_name": "Неизвестная карточка",\n            "start_time": datetime(2026, 8, 6, 9, 0, tzinfo=UTC),\n            "obtain_amount": 0,\n            "obtain_type": "diamonds",\n            "currency": "diamonds",\n        }\n    ]\n\n    async def no_review(_target_date: date):\n        return None\n\n    async def preview_target():\n        return {"chat_id": -100123, "thread_id": 77}\n\n    async def schedule_lots(_target_date: date):\n        return lots\n\n    async def emoji_assets():\n        return {"header": 1, "card": 2, "diamond": 3}\n\n    record_preview = AsyncMock()\n    monkeypatch.setattr(schedule_publication, "get_publication_review", no_review)\n    monkeypatch.setattr(schedule_publication, "get_preview_target", preview_target)\n    monkeypatch.setattr(schedule_publication, "get_schedule_lots_for_day", schedule_lots)\n    monkeypatch.setattr(schedule_publication, "get_emoji_assets", emoji_assets)\n    monkeypatch.setattr(schedule_publication, "record_pending_preview", record_preview)\n    client = FakeClient()\n\n    message_id = await schedule_publication.send_schedule_review_preview(\n        client,\n        target_date,\n    )\n\n    assert message_id == 101\n    assert len(client.calls) == 2\n    preview_chat, preview_text, preview_kwargs = client.calls[0]\n    warning_chat, warning_text, warning_kwargs = client.calls[1]\n    assert preview_chat == warning_chat == -100123\n    assert preview_text.startswith("🦋 АНОНС НА 6 АВГУСТА 🦋")\n    assert preview_kwargs["reply_to"] == 77\n    assert preview_kwargs["buttons"]\n    assert "Расписание показано" in warning_text\n    assert "лот 9244: не определена колода" in warning_text\n    assert "/schedule_setup" in warning_text\n    assert "/schedule_audit" in warning_text\n    assert warning_kwargs["reply_to"] == 77\n    record_preview.assert_awaited_once_with(\n        target_date,\n        chat_id=-100123,\n        thread_id=77,\n        message_id=101,\n    )\n''',
    encoding="utf-8",
)
