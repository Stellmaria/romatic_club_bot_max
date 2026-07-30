from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.validate_private_dump import DumpValidationError, validate_dump


def test_private_dump_validator_accepts_matching_insert_files() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        schema = root / "schema.sql"
        schema.write_text(
            "CREATE TABLE public.users (user_id bigint);\n"
            "CREATE TABLE public.settings (user_id bigint);\n",
            encoding="utf-8",
        )
        dump = root / "dump"
        dump.mkdir()
        (dump / "users.sql").write_text(
            "INSERT INTO public.users (user_id) VALUES (1);\n",
            encoding="utf-8",
        )
        (dump / "settings.sql").write_text(";", encoding="utf-8")

        manifest = validate_dump(dump, schema)

    assert [item.table for item in manifest] == ["settings", "users"]
    assert sum(item.insert_statements for item in manifest) == 1


def test_private_dump_validator_rejects_cross_table_or_destructive_sql() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        schema = root / "schema.sql"
        schema.write_text("CREATE TABLE public.users (user_id bigint);", encoding="utf-8")
        dump = root / "dump"
        dump.mkdir()
        (dump / "users.sql").write_text("DROP TABLE public.users;", encoding="utf-8")

        try:
            validate_dump(dump, schema)
        except DumpValidationError as error:
            assert "forbidden" in str(error)
        else:  # pragma: no cover
            raise AssertionError("destructive dump was accepted")


def test_private_dump_validator_rejects_mutating_and_procedural_statements() -> None:
    forbidden_statements = (
        "DELETE FROM public.users;",
        "UPDATE public.users SET user_id = 2;",
        "DO $$ BEGIN RAISE NOTICE 'unexpected'; END $$;",
        "CALL public.restore_hook();",
        "\\! powershell -Command whoami",
    )

    for statement in forbidden_statements:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            schema = root / "schema.sql"
            schema.write_text(
                "CREATE TABLE public.users (user_id bigint);",
                encoding="utf-8",
            )
            dump = root / "dump"
            dump.mkdir()
            (dump / "users.sql").write_text(statement, encoding="utf-8")

            try:
                validate_dump(dump, schema)
            except DumpValidationError as error:
                assert "forbidden" in str(error)
            else:  # pragma: no cover
                raise AssertionError(f"forbidden statement was accepted: {statement}")


def test_private_dump_validator_cannot_hide_statement_after_insert() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        schema = root / "schema.sql"
        schema.write_text("CREATE TABLE public.users (name text);", encoding="utf-8")
        dump = root / "dump"
        dump.mkdir()
        (dump / "users.sql").write_text(
            "INSERT INTO public.users (name) VALUES ('safe; DELETE text'); "
            "DELETE FROM public.users;",
            encoding="utf-8",
        )

        try:
            validate_dump(dump, schema)
        except DumpValidationError as error:
            assert "delete" in str(error)
        else:  # pragma: no cover
            raise AssertionError("second destructive statement was accepted")


def test_private_dump_validator_accepts_comments_and_semicolons_inside_values() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        schema = root / "schema.sql"
        schema.write_text("CREATE TABLE public.users (name text);", encoding="utf-8")
        dump = root / "dump"
        dump.mkdir()
        (dump / "users.sql").write_text(
            "-- data-only export\n"
            "INSERT INTO public.users (name) VALUES "
            "('semi;colon'), ('DROP TABLE is text'), ($tag$dollar;value$tag$);\n"
            "/* trailing comment */",
            encoding="utf-8",
        )

        manifest = validate_dump(dump, schema)

    assert manifest[0].insert_statements == 1


def test_private_dump_validator_rejects_insert_select_and_function_calls() -> None:
    statements = (
        "INSERT INTO public.users (user_id) SELECT 1;",
        "INSERT INTO public.users (user_id) VALUES (pg_sleep(1));",
    )

    for statement in statements:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            schema = root / "schema.sql"
            schema.write_text(
                "CREATE TABLE public.users (user_id bigint);",
                encoding="utf-8",
            )
            dump = root / "dump"
            dump.mkdir()
            (dump / "users.sql").write_text(statement, encoding="utf-8")

            try:
                validate_dump(dump, schema)
            except DumpValidationError as error:
                assert "INSERT" in str(error)
            else:  # pragma: no cover
                raise AssertionError(f"executable INSERT was accepted: {statement}")
