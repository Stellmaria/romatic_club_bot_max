# Quality gates

## Локальный запуск

Полный набор быстрых проверок, typing, architecture, security contracts и coverage:

```bash
python -m pip install -r requirements-dev.txt
python scripts/quality.py all --base origin/main
```

CI устанавливает полностью зафиксированный граф зависимостей из `requirements.lock` через `uv`. После изменения `requirements.txt` или `requirements-dev.txt` lock-файл нужно обновить в том же PR.

Для destructive integration suite нужен disposable PostgreSQL 17 и переменные из integration test contract:

```bash
python scripts/quality.py all --base origin/main --include-integration
```

Отдельные команды: `changed`, `typing`, `architecture`, `security`, `coverage`, `unit`, `integration`.

## Sharded unit and coverage pipeline

Pull request запускает unit/regression suite один раз в четырёх детерминированных matrix shards. `scripts/ci_test_shard.py` распределяет все non-integration test-файлы по размеру исходников, проверяет отсутствие пропусков и дубликатов и запускает каждый shard сразу с branch-aware coverage.

Каждый shard публикует собственный JUnit XML и `.coverage.<index>`. После успешного завершения всех shards job `coverage` объединяет файлы через `scripts/ci_coverage_report.py`, формирует общие метрики и применяет существующий coverage ratchet. Отдельного повторного запуска полного suite ради coverage нет.

Job `test` остаётся стабильным агрегатором для branch protection: он проверяет успешность `preflight` и всей shard-матрицы. Имена required checks `test` и `coverage` не меняются.

Workflow запускается для `pull_request` и для push только в `main`, поэтому один commit ветки PR не создаёт дублирующий push-run. `concurrency.cancel-in-progress` отменяет устаревшие прогоны после новых commit.

## Preflight

Перед завершением shard-матрицы отдельный preflight проверяет:

- полноту shard-плана;
- компиляцию Python-исходников и тестов;
- сборку и установку wheel;
- Ruff и project-specific persistence/database/Telegram/handler contracts.

Ошибки упаковки, импорта и статических границ обнаруживаются без ожидания полного набора тестов.

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

Shard JUnit-файлы объединяются в общие unit metrics. Coverage и PostgreSQL integration также создают JSON metrics в `var/quality`. Метрики содержат количество тестов, failures/errors/skips, суммарную длительность suite, число rerun/flaky markers и flaky rate. GitHub Actions публикует их как artifacts.

Dependency-heavy Python jobs используют `uv` cache с `requirements.lock` как ключом. Deployment build использует Docker Buildx GitHub Actions layer cache.

## Required checks

Ветка `main` требует успешные checks:

- `changed-code-quality`;
- `typing-and-architecture`;
- `coverage`;
- `security-static`;
- `deployment-contract`;
- `server-supervisor-contract`;
- `test`;
- `postgres-integration`.
