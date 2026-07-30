# Hotfix: `/addlot` stopped after lot selection

## Symptom

After selecting a card, arbitrary card, deck, subscription, or another lot type, the bot logged:

```text
NameError: name '_ask_for_currency' is not defined
```

The add-lot FSM therefore never advanced to `UserAddLotFSM.waiting_for_currency`.

## Cause

The legacy live router in `bot/handlers/auctions.py` still calls `_ask_for_currency(...)`, but the helper had only been retained in the newer modular router `bot/handlers/auction/submission.py`.

## Fix

A local `_ask_for_currency(...)` helper was restored in the live legacy router. It:

- reads the selected auction kind from FSM data;
- switches the FSM to `waiting_for_currency`;
- displays the standard currency keyboard;
- displays tea, diamonds, and tea-or-diamonds for reverse auctions;
- displays the same options plus custom combo for free auctions.

A regression test covers standard, reverse, and free transitions.
