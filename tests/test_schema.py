"""
TaskFlow — Schema tests
Verifies all tables, columns, FKs, CHECK constraints, and seed data.
"""

import os
import pathlib
import sqlite3
import tempfile
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path):
    db = tmp_path / "taskflow_test.db"
    os.environ["DB_PATH"] = str(db)
    yield str(db)
    os.environ.pop("DB_PATH", None)


@pytest.fixture()
def conn(db_path):
    """Fresh DB with schema applied."""
    import importlib, sys

    # Re-import mcp_server to pick up the new DB_PATH
    if "mcp_server" in sys.modules:
        del sys.modules["mcp_server"]
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "servers"))
    import mcp_server  # noqa: F401 — triggers _ensure_db()

    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Schema: tables exist
# ---------------------------------------------------------------------------

EXPECTED_TABLES = [
    "pipeline_steps",
    "projects",
    "features",
    "definitions_of_done",
    "test_specs",
    "build_reports",
    "test_results",
    "retro_reports",
    "recommendations",
    "decisions",
    "decision_artefacts",
    "feature_backlog",
    "tasks",
    # brief-derived tables
    "project_outcomes",
    "success_metrics",
    "user_roles",
    "stakeholders",
    "key_workflows",
    "non_functional_requirements",
    "integrations",
    "project_risks",
    "release_phases",
    "brief_features",
]


@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_table_exists(conn, table):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    assert row is not None, f"Table '{table}' not found"


# ---------------------------------------------------------------------------
# Schema: pipeline_steps seed data
# ---------------------------------------------------------------------------


def test_pipeline_steps_count(conn):
    count = conn.execute("SELECT COUNT(*) FROM pipeline_steps").fetchone()[0]
    assert count == 13


def test_pipeline_steps_step_numbers(conn):
    rows = conn.execute(
        "SELECT step_number FROM pipeline_steps ORDER BY step_number"
    ).fetchall()
    assert [r[0] for r in rows] == list(range(1, 14))


def test_pipeline_steps_agent_roles(conn):
    rows = conn.execute(
        "SELECT step_number, agent_role FROM pipeline_steps ORDER BY step_number"
    ).fetchall()
    expected = {
        1: "product_manager",
        2: "pm_reviewer",
        3: "product_manager",
        4: "pm_reviewer",
        5: "tester",
        6: "test_reviewer",
        7: "builder",
        8: "tester",
        9: "documenter",
        10: "product_manager",
        11: "pm_reviewer",
        12: "product_manager",
        13: "pm_reviewer",
    }
    for r in rows:
        assert r["agent_role"] == expected[r["step_number"]]


def test_step9_no_approval(conn):
    """Step 9 (documenter) has requires_approval=0 — auto-advances."""
    row = conn.execute(
        "SELECT requires_approval FROM pipeline_steps WHERE step_number=9"
    ).fetchone()
    assert row["requires_approval"] == 0


def test_step13_spawns_step3(conn):
    """Step 13 on_approval_spawn is '[3]' — triggers cycle restart."""
    row = conn.execute(
        "SELECT on_approval_spawn FROM pipeline_steps WHERE step_number=13"
    ).fetchone()
    assert row["on_approval_spawn"] == "[3]"


def test_step4_spawns_per_feature(conn):
    row = conn.execute(
        "SELECT on_approval_spawn FROM pipeline_steps WHERE step_number=4"
    ).fetchone()
    assert row["on_approval_spawn"] == "per_feature"


# ---------------------------------------------------------------------------
# Schema: CHECK constraints
# ---------------------------------------------------------------------------


def test_tasks_status_check(conn):
    conn.execute("INSERT INTO projects (name, brief_text) VALUES ('p','b')")
    step = conn.execute("SELECT id FROM pipeline_steps WHERE step_number=3").fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tasks (project_id,step_id,agent_role,status) VALUES (1,?,?,'invalid_status')",
            (step["id"], "product_manager"),
        )


def test_tasks_agent_role_check(conn):
    step = conn.execute("SELECT id FROM pipeline_steps WHERE step_number=3").fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tasks (project_id,step_id,agent_role,status) VALUES (NULL,?,?,'pending')",
            (step["id"], "rogue_agent"),
        )


def test_projects_status_check(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO projects (name, brief_text, status) VALUES ('p','b','invalid')"
        )


def test_decision_artefacts_type_check(conn):
    conn.execute("INSERT INTO projects (name, brief_text) VALUES ('p','b')")
    conn.execute(
        "INSERT INTO features (project_id,title,description) VALUES (1,'F','D')"
    )
    conn.execute("INSERT INTO retro_reports (feature_id, summary) VALUES (1,'s')")
    conn.execute(
        "INSERT INTO recommendations (retro_report_id,description,recommendation_type) VALUES (1,'d','improve')"
    )
    conn.execute(
        "INSERT INTO decisions (recommendation_id,decision) VALUES (1,'do it')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO decision_artefacts (decision_id,artefact_type,title,content) VALUES (1,'bad_type','t','c')"
        )


def test_feature_backlog_status_check(conn):
    conn.execute("INSERT INTO projects (name, brief_text) VALUES ('p','b')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO feature_backlog (project_id,title,description,status) VALUES (1,'t','d','unknown')"
        )


# ---------------------------------------------------------------------------
# Schema: FK enforcement
# ---------------------------------------------------------------------------


def test_features_fk_project(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO features (project_id,title,description) VALUES (999,'t','d')"
        )
        conn.commit()


def test_tasks_fk_pipeline_steps(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tasks (project_id,step_id,agent_role,status) VALUES (NULL,9999,'product_manager','pending')"
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Schema: idempotency
# ---------------------------------------------------------------------------


def test_init_sql_idempotent(db_path):
    """Running init.sql twice must not raise errors."""
    init_sql = (pathlib.Path(__file__).parent.parent / "db" / "init.sql").read_text()
    conn = sqlite3.connect(db_path)
    conn.executescript(init_sql)
    conn.executescript(init_sql)  # second run — no error
    count = conn.execute("SELECT COUNT(*) FROM pipeline_steps").fetchone()[0]
    assert count == 13  # seed data not duplicated
    conn.close()
