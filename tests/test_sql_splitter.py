"""Tests for the .sql statement splitter.

The first load run died because a regex-based splitter stripped a `--` that was
inside a string literal, breaking the quoting of a COMMENT ON body. These lock
in the cases that matter: a splitter that mangles DDL fails loudly at execute
time if you are lucky, and silently changes semantics if you are not.
"""

from __future__ import annotations

from pathlib import Path

from rrip.db.connection import sql_statements


def split(tmp_path: Path, sql: str) -> list[str]:
    f = tmp_path / "t.sql"
    f.write_text(sql, encoding="utf-8")
    return list(sql_statements(f))


def test_splits_on_semicolons(tmp_path: Path) -> None:
    out = split(tmp_path, "SELECT 1; SELECT 2; SELECT 3;")
    assert out == ["SELECT 1", "SELECT 2", "SELECT 3"]


def test_trailing_statement_without_semicolon(tmp_path: Path) -> None:
    assert split(tmp_path, "SELECT 1; SELECT 2") == ["SELECT 1", "SELECT 2"]


def test_strips_line_comments(tmp_path: Path) -> None:
    out = split(tmp_path, "-- a comment\nSELECT 1;\n-- another\nSELECT 2;")
    assert out == ["SELECT 1", "SELECT 2"]


def test_strips_block_comments(tmp_path: Path) -> None:
    assert split(tmp_path, "/* hi */ SELECT 1; /* there\nmulti */ SELECT 2;") == [
        "SELECT 1", "SELECT 2"]


def test_double_dash_inside_string_literal_is_preserved(tmp_path: Path) -> None:
    """The regression. A `--` inside a literal is data, not a comment."""
    sql = "COMMENT ON TABLE t IS 'verified exact -- on all rows';"
    out = split(tmp_path, sql)
    assert len(out) == 1
    assert "-- on all rows" in out[0]
    assert out[0].count("'") == 2


def test_semicolon_inside_string_literal_does_not_split(tmp_path: Path) -> None:
    out = split(tmp_path, "SELECT 'a;b';")
    assert out == ["SELECT 'a;b'"]


def test_escaped_quote_inside_literal(tmp_path: Path) -> None:
    out = split(tmp_path, "SELECT 'it''s fine; really';")
    assert out == ["SELECT 'it''s fine; really'"]


def test_multiline_literal_concatenation_survives(tmp_path: Path) -> None:
    """Postgres concatenates newline-separated literals; both must survive."""
    sql = "COMMENT ON TABLE t IS\n  'first -- part '\n  'second part';"
    out = split(tmp_path, sql)
    assert len(out) == 1
    assert "first -- part " in out[0]
    assert "second part" in out[0]


def test_dollar_quoted_block_with_semicolons(tmp_path: Path) -> None:
    sql = """
    DO $$
    DECLARE w int;
    BEGIN
        FOR w IN 1..3 LOOP
            EXECUTE format('CREATE TABLE t%s ()', w);
        END LOOP;
    END $$;
    SELECT 1;
    """
    out = split(tmp_path, sql)
    assert len(out) == 2
    assert out[0].startswith("DO $$")
    assert "END LOOP;" in out[0]
    assert out[1] == "SELECT 1"


def test_tagged_dollar_quote(tmp_path: Path) -> None:
    out = split(tmp_path, "DO $body$ BEGIN a; b; END $body$; SELECT 1;")
    assert len(out) == 2
    assert "a; b;" in out[0]


def test_comment_inside_dollar_block_is_kept(tmp_path: Path) -> None:
    out = split(tmp_path, "DO $$ BEGIN -- keep me\n NULL; END $$;")
    assert "-- keep me" in out[0]


def test_real_ddl_files_parse(tmp_path: Path) -> None:
    """Every shipped DDL file must split into a plausible statement list."""
    for name in ("05_control.sql", "10_dimensions.sql", "20_facts.sql",
                 "30_constraints.sql", "40_indexes.sql", "00_tuning.sql"):
        stmts = list(sql_statements(Path("sql/ddl") / name))
        assert stmts, f"{name} produced no statements"
        for s in stmts:
            # Unbalanced quotes are the specific corruption we are guarding on.
            assert s.count("'") % 2 == 0, f"unbalanced quotes in {name}: {s[:120]}"
