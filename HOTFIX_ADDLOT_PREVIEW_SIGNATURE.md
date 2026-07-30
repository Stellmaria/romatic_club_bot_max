# Hotfix: создание лота после выбора валюты

Исправлена сигнатура `_send_user_pending_lot_preview`: функция теперь принимает
`custom_offer_terms` и передаёт его в `currency_choices_label`.

Это устраняет ошибку:

```text
TypeError: _send_user_pending_lot_preview() got an unexpected keyword argument 'custom_offer_terms'
```

Проверка: `PYTHONPATH=. python -m pytest -q tests/test_hotfix_addlot_preset_transition.py tests/test_auction_type_reverse_free_currencies.py`.
