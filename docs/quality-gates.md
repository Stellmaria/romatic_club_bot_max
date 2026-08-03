# Quality gates

## Локальный запуск

Полный набор быстрых проверок, typing, architecture, security contracts и coverage:

```bash
python -m pip install -r requirements-dev.txt
python scripts/quality.py all --base origin/main
```

Для destructive integration suite нужен disposable PostgreSQL 17 и переменные из integration test contract:

```bash
python scripts/quality.py all --base origin/main --include-integration
```

Отдельные команды: `changed`, `typing`, `architecture`, `security`, `coverage`, `unit`, `integration`.

## Changed-file ratchet

Изменённые Python-файлы проходят `black --check` и Ruff families `E`, `F`, `I`, `B`, `BLE`, `ASYNC`, `S`, `SIM`, `UP`, `C4`, `PIE`, `RUF`, `C90`, `ARG`. Это блокирует новые wildcard/unused imports, broad exceptions, типовые async-ошибки, security-adjacent нарушения, лишний и недостижимый код и cyclomatic complexity выше 15.

Изменённые runtime-файлы дополнительно проходят `mypy --strict` без допуска новых ошибок и AST-проверку блокирующих вызовов внутри `async def`. Исключение допускается только на строке вызова с комментарием `quality: allow-blocking: <обоснование>`.

## Typing baseline

`quality/mypy-baseline.json` фиксирует число strict-mypy ошибок отдельно для domain/application и repositories. CI запрещает превышать baseline и запрещает увеличивать сам baseline относительно целевой ветки. При исправлении ошибок значение уменьшается в том же PR.

Проверяемые области:

- domain/application: `bot/domain`, `bot/use_cases`, `bot/application_models.py`, `bot/application_ports.py`;
- repositories: `bot/repositories`, `db/repositories`, `userbot/repositories.py`.

## Coverage baseline

`quality/coverage-baseline.json` содержит два независимых порога:

- общий branch-aware coverage runtime-пакетов;
- line coverage для domain/application.

CI запрещает падение фактического покрытия ниже порога и уменьшение самого порога относительно base branch. После роста покрытия baseline повышается до нового устойчивого значения.

## Architecture and security

Architecture job запускает import/AST contracts и поведенческие architecture tests. Они запрещают domain → framework/infrastructure, handler → low-level DB и новые обходы repository/application boundaries.

Security job отдельно запускает persistence exception и Telegram boundary contracts. Security-adjacent Ruff rules применяются к каждому изменённому Python-файлу.

Time-policy contract запускает `scripts/check_time_policy.py`: новые прямые вызовы системных часов, `pytz` и `dateutil.tz` запрещены, а legacy baseline может только уменьшаться. Полный контракт описан в `docs/TIME_POLICY.md`.

## Duration and flaky rate

Unit, coverage и PostgreSQL integration команды создают JUnit XML и JSON metrics в `var/quality`. Метрики содержат количество тестов, failures/errors/skips, длительность suite, число rerun/flaky markers и flaky rate. GitHub Actions публикует их как artifacts на 14 дней.

Heavy integration и static security jobs отделены от fast changed-code gate. Все Python jobs используют `setup-python` pip cache с dependency files как cache key input.

## Required checks

После стабилизации workflow ветка `main` должна требовать успешные checks:

- `changed-code-quality`;
- `typing-and-architecture`;
- `coverage`;
- `security-static`;
- `deployment-contract`;
- `server-supervisor-contract`;
- `test`;
- `postgres-integration`.
