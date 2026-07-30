HOTFIX: TypeError в _send_user_pending_lot_preview(custom_offer_terms=...)

1. Полностью остановите бот.
2. Положите APPLY_ADDLOT_HOTFIX.cmd и apply_addlot_preview_hotfix.py в одну папку.
3. Запустите APPLY_ADDLOT_HOTFIX.cmd.
4. Дождитесь сообщения «HOTFIX УСПЕШНО ПРИМЕНЁН».
5. Снова запустите E:\python\main\refactored_project_phase6\main.py.

Скрипт:
- исправляет именно E:\python\main\refactored_project_phase6\bot\handlers\auctions.py;
- создаёт резервную копию рядом с исходным файлом;
- добавляет custom_offer_terms в сигнатуру функции;
- проверяет синтаксис;
- удаляет __pycache__.
