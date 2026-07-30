from __future__ import annotations

import ast
from enum import Enum
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_currency_helper():
    source = (ROOT / "bot/handlers/auctions.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_ask_for_currency"
    )
    helper.decorator_list = []
    helper.returns = None
    for arg in (*helper.args.posonlyargs, *helper.args.args, *helper.args.kwonlyargs):
        arg.annotation = None

    class AuctionKind(str, Enum):
        STANDARD = "standard"
        REVERSE = "reverse"
        FREE = "free"

    class UserAddLotFSM:
        waiting_for_currency = "waiting_for_currency"

    namespace = {
        "AuctionKind": AuctionKind,
        "UserAddLotFSM": UserAddLotFSM,
        "auction_currency_kb": lambda kind: f"keyboard:{kind}",
    }
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(ROOT), "exec"), namespace)
    return namespace["_ask_for_currency"]


class _State:
    def __init__(self, kind: str | None):
        self.data = {"auction_kind": kind}
        self.current_state = None

    async def get_data(self):
        return self.data

    async def set_state(self, value):
        self.current_state = value


class _Message:
    def __init__(self):
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected_prompt"),
    [
        (None, "Выберите валюту:"),
        ("standard", "Выберите валюту:"),
        ("reverse", "Выберите валюту обратного аукциона:"),
        ("free", "Выберите, в какой валюте принимать предложения:"),
    ],
)
async def test_addlot_selection_advances_to_currency(kind, expected_prompt):
    helper = _load_currency_helper()
    state = _State(kind)
    message = _Message()

    await helper(message, state)

    assert state.current_state == "waiting_for_currency"
    assert message.answers == [
        (expected_prompt, {"reply_markup": f"keyboard:{kind or 'standard'}"})
    ]
