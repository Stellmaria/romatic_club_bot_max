from aiogram import F, types

from bot.handlers.admin.helper.admin_constants import ADMIN_MESSAGES, SYSTEM_MESSAGES
from bot.handlers.admin.helper.admin_service import remove_admin_role
from bot.handlers.admin.action_support.compat import add_admin_role
from bot.handlers.admin.helper.new.formatting import format_admins_list
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.utils_admin import resolve_admin_action_args
from db.db import list_admins


def register_user_admins(router):
    @router.message(F.text.startswith("/add_admin"))
    @admin_only
    async def add_admin_cmd(message: types.Message, **kwargs):
        user_id, user = await resolve_admin_action_args(message, "/add_admin <user_id или @username> <пароль>")
        if not user_id:
            return
        username = user["username"] if user else None
        await add_admin_role(user_id, username, message.from_user.id)
        await message.answer(ADMIN_MESSAGES["user_now_admin"].format(user_id=user_id), parse_mode="HTML")

    @router.message(F.text.startswith("/remove_admin"))
    @admin_only
    async def remove_admin_cmd(message: types.Message, **kwargs):
        user_id, user = await resolve_admin_action_args(message, "/remove_admin <user_id или @username> <пароль>")
        if not user_id:
            return
        if user_id == message.from_user.id:
            await message.answer(SYSTEM_MESSAGES["cannot_delete_self"])
            return
        await remove_admin_role(user_id, message.from_user.id)
        await message.answer(ADMIN_MESSAGES["user_removed_admin"].format(user_id=user_id))

    @router.message(F.text == "/admins")
    @admin_only
    async def list_admins_cmd(message: types.Message, **kwargs):
        admins = await list_admins()
        if not admins:
            await message.answer(ADMIN_MESSAGES["no_admins"])
            return
        msg = format_admins_list(admins)
        await message.answer(msg, parse_mode="HTML")
