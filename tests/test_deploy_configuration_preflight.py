"""Regression contracts for the production deployment gate."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy/server/deploy.sh"


def test_configuration_preflight_runs_before_runtime_replacement() -> None:
    script = DEPLOY.read_text(encoding="utf-8")

    prepare_position = script.index('echo "Preparing $target_sha..."')
    preflight_position = script.index(
        'echo "Validating target configuration before replacing runtime..."'
    )
    deploy_position = script.index('echo "Deploying $target_sha..."')
    runtime_replaced_position = script.index("runtime_replaced=1")

    assert prepare_position < preflight_position < deploy_position
    assert deploy_position < runtime_replaced_position
    assert "BotProcessSettings.from_env" in script
    assert "UserbotProcessSettings.from_env" in script
    assert "from bot.core.settings import settings" not in script


def test_configuration_preflight_imports_public_settings_api() -> None:
    from bot.core.settings import BotProcessSettings, UserbotProcessSettings

    assert callable(BotProcessSettings.from_env)
    assert callable(UserbotProcessSettings.from_env)

    script = DEPLOY.read_text(encoding="utf-8")
    assert "from bot.core.settings import BotProcessSettings" in script
    assert "from bot.core.settings import UserbotProcessSettings" in script
    assert "from bot.core.settings import settings" not in script


def test_preflight_failure_leaves_running_containers_untouched() -> None:
    script = DEPLOY.read_text(encoding="utf-8")

    assert "code_switched=0" in script
    assert "runtime_replaced=0" in script
    assert "session_mutated=0" in script
    assert 'if [[ "$runtime_replaced" == "1" || "$session_was_mutated" == "1" ]]' in script
    assert "Running containers were not replaced; runtime left untouched." in script


def test_deploy_does_not_claim_to_guard_resident_supervisor_code() -> None:
    script = DEPLOY.read_text(encoding="utf-8")

    assert "BASH_SOURCE" not in script
    assert "Running deploy script does not match target commit" not in script
