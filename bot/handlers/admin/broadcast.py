from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext

from bot.handlers.admin.action_support.compat import send_admin_log
from bot.handlers.admin.helper.new.formatting import format_admin_action_log
from bot.handlers.admin.helper.new.keyboards import menu_keyboard
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.outbox import TelegramOutboxService
from db.legacy import get_all_users, log_audit_action
from bot.legacy_fsm import BroadcastFSM


def register_broadcast_handlers(router: Router):
    @router.message(F.text == "/broadcast", F.chat.type == "private")
    @admin_only
    async def start_broadcast(message: types.Message, state: FSMContext):
        await state.clear()
        await message.answer(
            "Введите текст рассылки (или 'назад' для выхода):",
            reply_markup=menu_keyboard(["⬅️ Назад"])
        )
        await state.set_state(BroadcastFSM.waiting_for_text)

    @router.message(BroadcastFSM.waiting_for_text, F.text.lower().in_(["назад", "отмена", "❌ отмена", "cancel"]),
                    F.chat.type == "private")
    async def cancel_broadcast(message: types.Message, state: FSMContext):
        await state.clear()
        await message.answer(
            "Рассылка отменена.\n\nДобро пожаловать в админ-панель! Выберите раздел:",
            reply_markup=menu_keyboard(
                ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
                ["📊 Статистика", "📣 Рассылка", "🚫 Логи"]
            )
        )

    @router.message(BroadcastFSM.waiting_for_text)
    async def send_broadcast(message: types.Message, state: FSMContext):
        await state.clear()
        users = await get_all_users()
        if not users:
            await message.answer(
                "В базе не найдено ни одного пользователя для рассылки.",
                reply_markup=types.ReplyKeyboardRemove()
            )
            return
        result = await (await TelegramOutboxService.create()).enqueue_copy_message_broadcast(
            topic="admin-broadcast",
            dedupe_scope=f"{message.chat.id}:{message.message_id}",
            recipients=(int(user["user_id"]) for user in users),
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        count = result.queued
        await message.answer(
            f"Рассылка поставлена в очередь для {count} пользователей.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        text = format_admin_action_log(
            action="broadcast",
            admin={"id": message.from_user.id, "username": message.from_user.username or message.from_user.full_name},
            message_text=message.text,
            recipients=count
        )
        await send_admin_log(message.bot, text)
        await log_audit_action(
            user_id=message.from_user.id,
            action_type="broadcast",
            auction_id=None,
            details=f"Текст рассылки: {message.text} | Поставлено в очередь: {count}"
        )
