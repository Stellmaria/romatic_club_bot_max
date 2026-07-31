from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_every_approved_exchange_edit_button_has_a_handler() -> None:
    catalog = _source("bot/handlers/auction/exchange/catalog.py")
    editor = _source("bot/handlers/auction/exchange/editor.py")

    for callback in (
        "ex_edit_mode:",
        "ex_edit_price:",
        "ex_edit_currency:",
        "ex_edit_comment:",
        "ex_edit_proof:",
    ):
        assert f'callback_data=f"{callback}' in catalog
        assert f'F.data.startswith("{callback}")' in editor

    assert 'F.data.startswith("ex_edit_mode_set:")' in editor
    assert 'F.data.startswith("ex_edit_currency_set:")' in editor


def test_approved_exchange_editor_is_registered() -> None:
    package = _source("bot/handlers/auction/exchange/__init__.py")

    assert "from .editor import router as editor_router" in package
    assert "router.include_router(editor_router)" in package


def test_editor_supports_price_comment_and_proof_input_states() -> None:
    editor = _source("bot/handlers/auction/exchange/editor.py")

    assert "class ApprovedExchangeEditFSM(StatesGroup):" in editor
    assert "waiting_price = State()" in editor
    assert "waiting_comment = State()" in editor
    assert "waiting_proof = State()" in editor
    assert "ApprovedExchangeEditFSM.waiting_price, F.text" in editor
    assert "ApprovedExchangeEditFSM.waiting_comment, F.text" in editor
    assert "ApprovedExchangeEditFSM.waiting_proof, F.photo" in editor
    assert "ApprovedExchangeEditFSM.waiting_proof, F.text" in editor


def test_repository_blocks_edits_after_publication_starts() -> None:
    repository = _source("bot/repositories/exchange_editor.py")

    assert "_EDITABLE_COLUMNS" in repository
    assert "AND status = 'approved'" in repository
    assert "AND deleted_at IS NULL" in repository
    assert "InvalidExchangeTransition" in repository


def test_whole_deck_mode_requires_the_full_deck_composition() -> None:
    service = _source("bot/services/exchange_editor.py")

    assert 'if normalized == "deck":' in service
    assert "deck_card_count" in service
    assert "batch_card_count" in service
    assert "actual != expected" in service
