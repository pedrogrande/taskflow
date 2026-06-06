# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mcp>=1.2.0",
# ]
# ///
"""
TaskFlow — MCP Server (Phase 1: universal tools only)

DB_PATH is read from the DB_PATH environment variable (set by .mcp.json).
Schema is initialised automatically on first run via _ensure_db().
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH: str = os.environ.get(
    "DB_PATH",
    str(pathlib.Path(__file__).parent.parent / "db" / "taskflow.db"),
)

_INIT_SQL_PATH: pathlib.Path = pathlib.Path(__file__).parent.parent / "db" / "init.sql"

# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------

mcp = FastMCP("taskflow")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_conn() -> sqlite3.Connection:
    """Return a connection with Row factory and FK enforcement enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_db() -> None:
    """Create the database file and run init.sql if not already initialised."""
    sql = _INIT_SQL_PATH.read_text()
    conn = _get_conn()
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _rows_to_list(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [_row_to_dict(r) for r in rows]


def _brief_context(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    """Return all brief-derived tables for a project.

    Injected into read_task_context for every step so agents always have
    full project context without needing to re-read files.
    Returns empty collections when the project was created via start_project
    (free-text path) rather than ingest_brief.
    """
    return {
        "outcomes": _rows_to_list(
            conn.execute(
                "SELECT outcome FROM project_outcomes WHERE project_id=? ORDER BY order_index",
                (project_id,),
            ).fetchall()
        ),
        "success_metrics": _rows_to_list(
            conn.execute(
                "SELECT metric, current_state, target, how_measured FROM success_metrics WHERE project_id=?",
                (project_id,),
            ).fetchall()
        ),
        "user_roles": _rows_to_list(
            conn.execute(
                "SELECT role, description, primary_workflow FROM user_roles WHERE project_id=?",
                (project_id,),
            ).fetchall()
        ),
        "stakeholders": _rows_to_list(
            conn.execute(
                "SELECT name, title, authority FROM stakeholders WHERE project_id=?",
                (project_id,),
            ).fetchall()
        ),
        "key_workflows": _rows_to_list(
            conn.execute(
                "SELECT actor, trigger, steps, outcome FROM key_workflows WHERE project_id=? ORDER BY order_index",
                (project_id,),
            ).fetchall()
        ),
        "non_functional_requirements": _rows_to_list(
            conn.execute(
                "SELECT nfr_type, notes FROM non_functional_requirements WHERE project_id=?",
                (project_id,),
            ).fetchall()
        ),
        "integrations": _rows_to_list(
            conn.execute(
                "SELECT system, purpose, direction, auth_method, phase_1_required FROM integrations WHERE project_id=?",
                (project_id,),
            ).fetchall()
        ),
        "project_risks": _rows_to_list(
            conn.execute(
                "SELECT description, likelihood, impact, mitigation FROM project_risks WHERE project_id=?",
                (project_id,),
            ).fetchall()
        ),
        "release_phases": _rows_to_list(
            conn.execute(
                "SELECT phase_number, description, target_date FROM release_phases WHERE project_id=?",
                (project_id,),
            ).fetchall()
        ),
        "brief_features": _rows_to_list(
            conn.execute(
                "SELECT name, description, priority, phase FROM brief_features WHERE project_id=?",
                (project_id,),
            ).fetchall()
        ),
    }


# Run on import so the DB is always ready before any tool is called.
_ensure_db()

# ---------------------------------------------------------------------------
# Universal tools (all agents)
# ---------------------------------------------------------------------------


@mcp.tool()
def read_pending_tasks(agent_role: str) -> list[dict[str, Any]]:
    """Return all tasks with status='pending' for the given agent role.

    Returns a list of task records including step number and step name so the
    agent knows what kind of work each task represents.

    Args:
        agent_role: One of product_manager, pm_reviewer, tester, test_reviewer,
                    builder, documenter.
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                t.id,
                t.project_id,
                t.feature_id,
                t.step_id,
                ps.step_number,
                ps.name        AS step_name,
                t.agent_role,
                t.status,
                t.rejection_notes,
                t.retry_count,
                t.task_data,
                t.created_at
            FROM tasks t
            JOIN pipeline_steps ps ON ps.id = t.step_id
            WHERE t.agent_role = ? AND t.status = 'pending'
            ORDER BY t.created_at ASC
            """,
            (agent_role,),
        ).fetchall()
        return _rows_to_list(rows)
    finally:
        conn.close()


@mcp.tool()
def claim_task(task_id: int) -> dict[str, Any]:
    """Mark a task as in_progress to prevent two agents claiming the same task.

    Returns the updated task record.

    Args:
        task_id: The ID of the task to claim.
    """
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"Task {task_id} not found")
        if row["status"] != "pending":
            raise ValueError(
                f"Task {task_id} cannot be claimed: status is '{row['status']}'"
            )
        conn.execute("UPDATE tasks SET status = 'in_progress' WHERE id = ?", (task_id,))
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return _row_to_dict(updated)
    finally:
        conn.close()


@mcp.tool()
def read_task_context(task_id: int) -> dict[str, Any]:
    """Return the records scoped to this task based on its step and feature.

    Each step returns a different set of records — only what the agent needs.
    See the architecture doc §4 for the full scoping rules.

    Args:
        task_id: The ID of the task whose context to retrieve.
    """
    conn = _get_conn()
    try:
        task = conn.execute(
            """
            SELECT t.*, ps.step_number, ps.name AS step_name, ps.agent_role AS step_agent_role
            FROM tasks t
            JOIN pipeline_steps ps ON ps.id = t.step_id
            WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        step = task["step_number"]
        project_id = task["project_id"]
        feature_id = task["feature_id"]

        ctx: dict[str, Any] = {"task": _row_to_dict(task)}

        # ----------------------------------------------------------------
        # Step 1 — ingest brief
        # brief file path lives in task_data; PM reads the file directly.
        # ----------------------------------------------------------------
        if step == 1:
            ctx["instruction"] = (
                "Read task_data for the brief file path. "
                "Use your file-read tool to read the brief, then call submit_project."
            )

        # ----------------------------------------------------------------
        # Step 2 — review project record
        # ----------------------------------------------------------------
        elif step == 2:
            project = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            ctx["project"] = _row_to_dict(project) if project else None
            ctx["brief"] = _brief_context(conn, project_id)

        # ----------------------------------------------------------------
        # Step 3 — define features (PM reads project + backlog)
        # ----------------------------------------------------------------
        elif step == 3:
            project = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            backlog = conn.execute(
                "SELECT * FROM feature_backlog WHERE project_id = ? AND status = 'pending'",
                (project_id,),
            ).fetchall()
            ctx["project"] = _row_to_dict(project) if project else None
            ctx["feature_backlog"] = _rows_to_list(backlog)
            ctx["brief"] = _brief_context(conn, project_id)

        # ----------------------------------------------------------------
        # Step 4 — review features + DoD
        # ----------------------------------------------------------------
        elif step == 4:
            project = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            features = conn.execute(
                "SELECT * FROM features WHERE project_id = ? ORDER BY order_index",
                (project_id,),
            ).fetchall()
            dod = conn.execute(
                """
                SELECT d.* FROM definitions_of_done d
                JOIN features f ON f.id = d.feature_id
                WHERE f.project_id = ?
                """,
                (project_id,),
            ).fetchall()
            ctx["project"] = _row_to_dict(project) if project else None
            ctx["features"] = _rows_to_list(features)
            ctx["definitions_of_done"] = _rows_to_list(dod)
            ctx["brief"] = _brief_context(conn, project_id)

        # ----------------------------------------------------------------
        # Step 5 — write test specs for this feature
        # ----------------------------------------------------------------
        elif step == 5:
            feature = conn.execute(
                "SELECT * FROM features WHERE id = ?", (feature_id,)
            ).fetchone()
            dod = conn.execute(
                "SELECT * FROM definitions_of_done WHERE feature_id = ?", (feature_id,)
            ).fetchall()
            ctx["feature"] = _row_to_dict(feature) if feature else None
            ctx["definitions_of_done"] = _rows_to_list(dod)
            if project_id:
                ctx["brief"] = _brief_context(conn, project_id)

        # ----------------------------------------------------------------
        # Step 6 — review test specs
        # ----------------------------------------------------------------
        elif step == 6:
            feature = conn.execute(
                "SELECT * FROM features WHERE id = ?", (feature_id,)
            ).fetchone()
            dod = conn.execute(
                "SELECT * FROM definitions_of_done WHERE feature_id = ?", (feature_id,)
            ).fetchall()
            specs = conn.execute(
                "SELECT * FROM test_specs WHERE feature_id = ? ORDER BY order_index",
                (feature_id,),
            ).fetchall()
            ctx["feature"] = _row_to_dict(feature) if feature else None
            ctx["definitions_of_done"] = _rows_to_list(dod)
            ctx["test_specs"] = _rows_to_list(specs)
            if project_id:
                ctx["brief"] = _brief_context(conn, project_id)

        # ----------------------------------------------------------------
        # Step 7 — build
        # ----------------------------------------------------------------
        elif step == 7:
            feature = conn.execute(
                "SELECT * FROM features WHERE id = ?", (feature_id,)
            ).fetchone()
            dod = conn.execute(
                "SELECT * FROM definitions_of_done WHERE feature_id = ?", (feature_id,)
            ).fetchall()
            specs = conn.execute(
                "SELECT * FROM test_specs WHERE feature_id = ? ORDER BY order_index",
                (feature_id,),
            ).fetchall()
            project = conn.execute(
                "SELECT id, name, brief_text FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            ctx["feature"] = _row_to_dict(feature) if feature else None
            ctx["definitions_of_done"] = _rows_to_list(dod)
            ctx["test_specs"] = _rows_to_list(specs)
            ctx["project_summary"] = _row_to_dict(project) if project else None
            if project_id:
                ctx["brief"] = _brief_context(conn, project_id)

        # ----------------------------------------------------------------
        # Step 8 — run tests
        # ----------------------------------------------------------------
        elif step == 8:
            feature = conn.execute(
                "SELECT * FROM features WHERE id = ?", (feature_id,)
            ).fetchone()
            dod = conn.execute(
                "SELECT * FROM definitions_of_done WHERE feature_id = ?", (feature_id,)
            ).fetchall()
            specs = conn.execute(
                "SELECT * FROM test_specs WHERE feature_id = ? ORDER BY order_index",
                (feature_id,),
            ).fetchall()
            build_report = conn.execute(
                "SELECT * FROM build_reports WHERE feature_id = ? ORDER BY created_at DESC LIMIT 1",
                (feature_id,),
            ).fetchone()
            ctx["feature"] = _row_to_dict(feature) if feature else None
            ctx["definitions_of_done"] = _rows_to_list(dod)
            ctx["test_specs"] = _rows_to_list(specs)
            ctx["build_report"] = _row_to_dict(build_report) if build_report else None
            if project_id:
                ctx["brief"] = _brief_context(conn, project_id)

        # ----------------------------------------------------------------
        # Step 9 — retrospective
        # ----------------------------------------------------------------
        elif step == 9:
            build_report = conn.execute(
                "SELECT * FROM build_reports WHERE feature_id = ? ORDER BY created_at DESC LIMIT 1",
                (feature_id,),
            ).fetchone()
            test_results = conn.execute(
                """
                SELECT tr.* FROM test_results tr
                JOIN test_specs ts ON ts.id = tr.test_spec_id
                WHERE ts.feature_id = ?
                ORDER BY tr.created_at DESC
                """,
                (feature_id,),
            ).fetchall()
            ctx["build_report"] = _row_to_dict(build_report) if build_report else None
            ctx["test_results"] = _rows_to_list(test_results)

        # ----------------------------------------------------------------
        # Step 10 — decisions
        # ----------------------------------------------------------------
        elif step == 10:
            retro = conn.execute(
                "SELECT * FROM retro_reports WHERE feature_id = ? ORDER BY created_at DESC LIMIT 1",
                (feature_id,),
            ).fetchone()
            recs = (
                conn.execute(
                    "SELECT * FROM recommendations WHERE retro_report_id = ?",
                    (retro["id"],),
                ).fetchall()
                if retro
                else []
            )
            ctx["retro_report"] = _row_to_dict(retro) if retro else None
            ctx["recommendations"] = _rows_to_list(recs)

        # ----------------------------------------------------------------
        # Step 11 — review decisions
        # ----------------------------------------------------------------
        elif step == 11:
            retro = conn.execute(
                "SELECT * FROM retro_reports WHERE feature_id = ? ORDER BY created_at DESC LIMIT 1",
                (feature_id,),
            ).fetchone()
            recs = (
                conn.execute(
                    "SELECT * FROM recommendations WHERE retro_report_id = ?",
                    (retro["id"],),
                ).fetchall()
                if retro
                else []
            )
            rec_ids = [r["id"] for r in recs]
            decisions: list[sqlite3.Row] = []
            for rid in rec_ids:
                decisions.extend(
                    conn.execute(
                        "SELECT * FROM decisions WHERE recommendation_id = ?", (rid,)
                    ).fetchall()
                )
            ctx["retro_report"] = _row_to_dict(retro) if retro else None
            ctx["recommendations"] = _rows_to_list(recs)
            ctx["decisions"] = _rows_to_list(decisions)

        # ----------------------------------------------------------------
        # Step 12 — implement decisions (PM writes artefacts + backlog entries)
        # ----------------------------------------------------------------
        elif step == 12:
            # Get decisions for this feature via retro → recommendations → decisions
            retro = conn.execute(
                "SELECT * FROM retro_reports WHERE feature_id = ? ORDER BY created_at DESC LIMIT 1",
                (feature_id,),
            ).fetchone()
            recs = (
                conn.execute(
                    "SELECT * FROM recommendations WHERE retro_report_id = ?",
                    (retro["id"],),
                ).fetchall()
                if retro
                else []
            )
            rec_ids = [r["id"] for r in recs]
            decisions = []
            for rid in rec_ids:
                decisions.extend(
                    conn.execute(
                        "SELECT * FROM decisions WHERE recommendation_id = ?", (rid,)
                    ).fetchall()
                )
            dec_ids = [d["id"] for d in decisions]
            artefacts: list[sqlite3.Row] = []
            for did in dec_ids:
                artefacts.extend(
                    conn.execute(
                        "SELECT * FROM decision_artefacts WHERE decision_id = ?", (did,)
                    ).fetchall()
                )
            ctx["decisions"] = _rows_to_list(decisions)
            ctx["existing_decision_artefacts"] = _rows_to_list(artefacts)

        # ----------------------------------------------------------------
        # Step 13 — final verification
        # ----------------------------------------------------------------
        elif step == 13:
            retro = conn.execute(
                "SELECT * FROM retro_reports WHERE feature_id = ? ORDER BY created_at DESC LIMIT 1",
                (feature_id,),
            ).fetchone()
            recs = (
                conn.execute(
                    "SELECT * FROM recommendations WHERE retro_report_id = ?",
                    (retro["id"],),
                ).fetchall()
                if retro
                else []
            )
            rec_ids = [r["id"] for r in recs]
            decisions = []
            for rid in rec_ids:
                decisions.extend(
                    conn.execute(
                        "SELECT * FROM decisions WHERE recommendation_id = ?", (rid,)
                    ).fetchall()
                )
            dec_ids = [d["id"] for d in decisions]
            artefacts = []
            for did in dec_ids:
                artefacts.extend(
                    conn.execute(
                        "SELECT * FROM decision_artefacts WHERE decision_id = ?", (did,)
                    ).fetchall()
                )
            backlog = conn.execute(
                "SELECT * FROM feature_backlog WHERE project_id = ? AND status = 'pending'",
                (project_id,),
            ).fetchall()
            ctx["decisions"] = _rows_to_list(decisions)
            ctx["decision_artefacts"] = _rows_to_list(artefacts)
            ctx["feature_backlog"] = _rows_to_list(backlog)
            # Step 13 is the final gate — include success metrics for verification
            ctx["brief"] = _brief_context(conn, project_id)

        return ctx

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal cascade helper
# ---------------------------------------------------------------------------

_RETRY_LIMIT = 3


def _spawn_tasks(
    conn: sqlite3.Connection,
    project_id: int,
    step_numbers: list[int],
    feature_id: int | None = None,
) -> list[int]:
    """Insert one pending task per step_number. Returns list of new task IDs."""
    new_ids: list[int] = []
    for sn in step_numbers:
        step = conn.execute(
            "SELECT * FROM pipeline_steps WHERE step_number = ?", (sn,)
        ).fetchone()
        if step is None:
            raise ValueError(f"pipeline_steps has no step_number={sn}")
        cur = conn.execute(
            """
            INSERT INTO tasks (project_id, feature_id, step_id, agent_role, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (project_id, feature_id, step["id"], step["agent_role"]),
        )
        new_ids.append(cur.lastrowid)
    return new_ids


# ---------------------------------------------------------------------------
# Pipeline bootstrap tool (used by /start-project skill)
# ---------------------------------------------------------------------------


@mcp.tool()
def ingest_brief(brief_json: str) -> dict[str, Any]:
    """Parse project brief JSON (from the HTML form) and store all structured data.

    Creates the project record, populates all brief-derived tables, and spawns
    the step-3 task. Use this instead of start_project when the client has
    completed the project brief form and provided the downloaded JSON file.

    Args:
        brief_json: The full JSON string from the project-brief-form.html download.
    """
    try:
        data = json.loads(brief_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    identity = data.get("project_identity", {})
    goals = data.get("goals", {})
    users = data.get("users", {})
    nfr = data.get("non_functional", {})
    platforms = data.get("platforms", {})
    design = data.get("design", {})
    timeline = data.get("timeline", {})
    deadline = timeline.get("deadline", {})
    dm = users.get("decision_maker", {})

    name = (identity.get("name") or "").strip() or "Untitled Project"

    conn = _get_conn()
    try:
        # --- projects row (scalars from brief) ---
        cur = conn.execute(
            """
            INSERT INTO projects (
                name, brief_text,
                organisation, industry, problem, success_definition, out_of_scope,
                decision_maker_name, decision_maker_contact, acceptance_testers,
                hosting, design_source, design_references, brand, maintenance,
                deadline_date, deadline_type, deadline_reason, platforms
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                name,
                json.dumps(data, ensure_ascii=False),
                identity.get("organisation"),
                identity.get("industry"),
                identity.get("problem"),
                identity.get("success_definition"),
                identity.get("out_of_scope"),
                dm.get("name"),
                dm.get("contact"),
                users.get("acceptance_testers"),
                platforms.get("hosting"),
                design.get("source"),
                design.get("references"),
                design.get("brand"),
                design.get("maintenance"),
                deadline.get("date") or None,
                deadline.get("type") or None,
                deadline.get("reason"),
                json.dumps(platforms.get("targets", [])),
            ),
        )
        project_id = cur.lastrowid

        # --- project_outcomes ---
        for i, outcome in enumerate(goals.get("outcomes", [])):
            if outcome:
                conn.execute(
                    "INSERT INTO project_outcomes (project_id, outcome, order_index) VALUES (?,?,?)",
                    (project_id, outcome, i),
                )

        # --- success_metrics ---
        for m in goals.get("metrics", []):
            if any(m.get(k) for k in ("metric", "target")):
                conn.execute(
                    """
                    INSERT INTO success_metrics
                        (project_id, metric, current_state, target, how_measured)
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        project_id,
                        m.get("metric"),
                        m.get("current_state"),
                        m.get("target"),
                        m.get("how_measured"),
                    ),
                )

        # --- user_roles ---
        for r in users.get("roles", []):
            if r.get("role"):
                conn.execute(
                    """
                    INSERT INTO user_roles (project_id, role, description, primary_workflow)
                    VALUES (?,?,?,?)
                    """,
                    (
                        project_id,
                        r.get("role"),
                        r.get("description"),
                        r.get("primary_workflow"),
                    ),
                )

        # --- stakeholders ---
        for s in users.get("stakeholders", []):
            if s.get("name"):
                conn.execute(
                    "INSERT INTO stakeholders (project_id, name, title, authority) VALUES (?,?,?,?)",
                    (project_id, s.get("name"), s.get("title"), s.get("authority")),
                )

        # --- key_workflows ---
        for i, w in enumerate(data.get("workflows", [])):
            if w.get("actor") or w.get("steps"):
                conn.execute(
                    """
                    INSERT INTO key_workflows
                        (project_id, actor, trigger, steps, outcome, order_index)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (
                        project_id,
                        w.get("actor"),
                        w.get("trigger"),
                        w.get("steps"),
                        w.get("outcome"),
                        i,
                    ),
                )

        # --- non_functional_requirements (enabled ones only) ---
        for nfr_type, val in nfr.items():
            if isinstance(val, dict) and val.get("required"):
                conn.execute(
                    """
                    INSERT INTO non_functional_requirements (project_id, nfr_type, notes)
                    VALUES (?,?,?)
                    """,
                    (project_id, nfr_type, val.get("notes")),
                )

        # --- integrations ---
        for integ in data.get("integrations", {}).get("systems", []):
            if integ.get("system"):
                conn.execute(
                    """
                    INSERT INTO integrations
                        (project_id, system, purpose, direction, auth_method, phase_1_required)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (
                        project_id,
                        integ.get("system"),
                        integ.get("purpose"),
                        integ.get("direction"),
                        integ.get("auth_method"),
                        1 if integ.get("phase_1_required") == "yes" else 0,
                    ),
                )

        # --- project_risks ---
        for risk in data.get("risks", []):
            if risk.get("description"):
                conn.execute(
                    """
                    INSERT INTO project_risks
                        (project_id, description, likelihood, impact, mitigation)
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        project_id,
                        risk.get("description"),
                        risk.get("likelihood"),
                        risk.get("impact"),
                        risk.get("mitigation"),
                    ),
                )

        # --- release_phases ---
        for phase in timeline.get("release_phases", []):
            if phase.get("description") or phase.get("phase"):
                conn.execute(
                    """
                    INSERT INTO release_phases
                        (project_id, phase_number, description, target_date)
                    VALUES (?,?,?,?)
                    """,
                    (
                        project_id,
                        phase.get("phase"),
                        phase.get("description"),
                        phase.get("target_date") or None,
                    ),
                )

        # --- brief_features (form suggestions; PM refines these at step 3) ---
        for feat in data.get("features", []):
            if feat.get("name"):
                conn.execute(
                    """
                    INSERT INTO brief_features
                        (project_id, name, description, priority, phase)
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        project_id,
                        feat.get("name"),
                        feat.get("description"),
                        feat.get("priority"),
                        feat.get("phase"),
                    ),
                )

        # Spawn step-3 task
        new_ids = _spawn_tasks(conn, project_id, [3])
        conn.commit()

        project = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        counts = {
            "outcomes": conn.execute(
                "SELECT COUNT(*) FROM project_outcomes WHERE project_id=?",
                (project_id,),
            ).fetchone()[0],
            "success_metrics": conn.execute(
                "SELECT COUNT(*) FROM success_metrics WHERE project_id=?", (project_id,)
            ).fetchone()[0],
            "user_roles": conn.execute(
                "SELECT COUNT(*) FROM user_roles WHERE project_id=?", (project_id,)
            ).fetchone()[0],
            "workflows": conn.execute(
                "SELECT COUNT(*) FROM key_workflows WHERE project_id=?", (project_id,)
            ).fetchone()[0],
            "nfr": conn.execute(
                "SELECT COUNT(*) FROM non_functional_requirements WHERE project_id=?",
                (project_id,),
            ).fetchone()[0],
            "integrations": conn.execute(
                "SELECT COUNT(*) FROM integrations WHERE project_id=?", (project_id,)
            ).fetchone()[0],
            "risks": conn.execute(
                "SELECT COUNT(*) FROM project_risks WHERE project_id=?", (project_id,)
            ).fetchone()[0],
            "brief_features": conn.execute(
                "SELECT COUNT(*) FROM brief_features WHERE project_id=?", (project_id,)
            ).fetchone()[0],
        }
        return {
            "project": _row_to_dict(project),
            "spawned_task_ids": new_ids,
            "ingested": counts,
            "message": f"Project '{name}' created from brief JSON. Step-3 task seeded for the product_manager.",
        }
    finally:
        conn.close()


@mcp.tool()
def start_project(
    name: str,
    brief_text: str,
    brief_path: str | None = None,
) -> dict[str, Any]:
    """Bootstrap a new project from free-text or file brief and seed the step-3 task.

    Use this for chat-initiated projects where no form JSON is available.
    For structured brief JSON from the HTML form, use ingest_brief instead.

    Args:
        name:       Project name.
        brief_text: Full brief content (always stored; read by all agents via read_task_context).
        brief_path: Optional source file path for traceability.
    """
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO projects (name, brief_text, brief_path) VALUES (?, ?, ?)",
            (name, brief_text, brief_path),
        )
        project_id = cur.lastrowid

        # Spawn step-3 (define features) — skipping 1+2 since /start-project
        # acts as the PM for brief ingestion and the skill IS the review.
        # Per architecture: steps 1-2 are pre-cycle setup triggered by /start-project.
        new_ids = _spawn_tasks(conn, project_id, [3])

        conn.commit()

        project = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return {
            "project": _row_to_dict(project),
            "spawned_task_ids": new_ids,
            "message": f"Project '{name}' created. Step-3 task seeded for the product_manager.",
        }
    finally:
        conn.close()


@mcp.tool()
def read_pipeline_status(project_id: int) -> dict[str, Any]:
    """Return the current pipeline state for a project.

    Shows all tasks grouped by status and step, so a human can see what
    is done, in-progress, pending, blocked, or rejected.

    Args:
        project_id: The project ID to query.
    """
    conn = _get_conn()
    try:
        project = conn.execute(
            "SELECT id, name, status, created_at FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if project is None:
            raise ValueError(f"Project {project_id} not found")

        tasks = conn.execute(
            """
            SELECT
                t.id, t.feature_id, t.status, t.retry_count,
                t.rejection_notes, t.created_at, t.completed_at,
                ps.step_number, ps.name AS step_name, ps.agent_role,
                f.title AS feature_title
            FROM tasks t
            JOIN pipeline_steps ps ON ps.id = t.step_id
            LEFT JOIN features f ON f.id = t.feature_id
            WHERE t.project_id = ?
            ORDER BY ps.step_number ASC, t.created_at ASC
            """,
            (project_id,),
        ).fetchall()

        by_status: dict[str, list] = {
            "pending": [],
            "in_progress": [],
            "done": [],
            "rejected": [],
            "blocked": [],
        }
        for t in tasks:
            entry = {
                "task_id": t["id"],
                "step": t["step_number"],
                "step_name": t["step_name"],
                "agent_role": t["agent_role"],
                "feature": t["feature_title"],
                "retry_count": t["retry_count"],
            }
            if t["rejection_notes"]:
                entry["rejection_notes"] = t["rejection_notes"]
            by_status[t["status"]].append(entry)

        features = conn.execute(
            "SELECT id, title FROM features WHERE project_id = ? ORDER BY order_index",
            (project_id,),
        ).fetchall()

        return {
            "project": _row_to_dict(project),
            "features": _rows_to_list(features),
            "tasks_by_status": by_status,
            "summary": {s: len(v) for s, v in by_status.items()},
        }
    finally:
        conn.close()


@mcp.tool()
def list_projects() -> list[dict[str, Any]]:
    """List all projects with their ID, name, status, and task summary.

    Used by /pipeline-status and /my-tasks to let the user choose a project.
    """
    conn = _get_conn()
    try:
        projects = conn.execute(
            "SELECT id, name, status, created_at FROM projects ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for p in projects:
            counts = conn.execute(
                """
                SELECT status, COUNT(*) as n FROM tasks WHERE project_id = ? GROUP BY status
                """,
                (p["id"],),
            ).fetchall()
            task_summary = {r["status"]: r["n"] for r in counts}
            row = _row_to_dict(p)
            row["task_summary"] = task_summary
            result.append(row)
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PM worker tools
# ---------------------------------------------------------------------------


@mcp.tool()
def submit_project(
    task_id: int,
    name: str,
    brief_text: str,
    brief_path: str | None = None,
) -> dict[str, Any]:
    """Create the project record and mark the step-1 task done.

    Call this after reading the brief. No approval gate is triggered here —
    pm_reviewer will review once the approve_task tool is called on the step-2 task.

    Args:
        task_id:    The step-1 task ID (from claim_task).
        name:       Project name.
        brief_text: Full brief content (always stored; accessible to agents without file access).
        brief_path: Optional source file path for traceability.
    """
    conn = _get_conn()
    try:
        task = conn.execute(
            "SELECT t.*, ps.step_number FROM tasks t JOIN pipeline_steps ps ON ps.id = t.step_id WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        if task["step_number"] != 1:
            raise ValueError(
                f"submit_project requires a step-1 task; got step {task['step_number']}"
            )
        if task["status"] != "in_progress":
            raise ValueError(
                f"Task {task_id} must be claimed (in_progress) before submitting"
            )

        cur = conn.execute(
            "INSERT INTO projects (name, brief_text, brief_path) VALUES (?, ?, ?)",
            (name, brief_text, brief_path),
        )
        project_id = cur.lastrowid

        # Back-fill project_id on the step-1 task (it was NULL before the project existed)
        conn.execute(
            "UPDATE tasks SET project_id = ?, status = 'done', completed_at = datetime('now','utc') WHERE id = ?",
            (project_id, task_id),
        )

        # Spawn step-2 reviewer task
        new_ids = _spawn_tasks(conn, project_id, [2])

        conn.commit()

        project = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return {
            "project": _row_to_dict(project),
            "task_done": task_id,
            "spawned_task_ids": new_ids,
        }
    finally:
        conn.close()


@mcp.tool()
def submit_features(
    task_id: int,
    features: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create feature and definition-of-done records and mark the step-3 task done.

    Each feature dict must have: title, description. Optional: source_requirement_text,
    order_index, definitions_of_done (list of {criterion, verifiable}).

    Args:
        task_id:  The step-3 task ID (from claim_task).
        features: List of feature dicts.
    """
    conn = _get_conn()
    try:
        task = conn.execute(
            "SELECT t.*, ps.step_number FROM tasks t JOIN pipeline_steps ps ON ps.id = t.step_id WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        if task["step_number"] != 3:
            raise ValueError(
                f"submit_features requires a step-3 task; got step {task['step_number']}"
            )
        if task["status"] != "in_progress":
            raise ValueError(
                f"Task {task_id} must be claimed (in_progress) before submitting"
            )

        project_id = task["project_id"]
        created_features: list[dict] = []

        for idx, f in enumerate(features):
            cur = conn.execute(
                """
                INSERT INTO features (project_id, title, description, source_requirement_text, order_index)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    f["title"],
                    f["description"],
                    f.get("source_requirement_text"),
                    f.get("order_index", idx),
                ),
            )
            feature_id = cur.lastrowid
            dod_list = f.get("definitions_of_done", [])
            for d in dod_list:
                conn.execute(
                    "INSERT INTO definitions_of_done (feature_id, criterion, verifiable) VALUES (?, ?, ?)",
                    (feature_id, d["criterion"], int(d.get("verifiable", 1))),
                )
            created_features.append({"feature_id": feature_id, "title": f["title"]})

        # Spawn step-4 reviewer task
        new_ids = _spawn_tasks(conn, project_id, [4])

        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = datetime('now','utc') WHERE id = ?",
            (task_id,),
        )
        conn.commit()

        return {
            "created_features": created_features,
            "task_done": task_id,
            "spawned_task_ids": new_ids,
        }
    finally:
        conn.close()


@mcp.tool()
def read_backlog(project_id: int) -> list[dict[str, Any]]:
    """Return pending feature backlog items for a project.

    Call this in step 3 alongside reviewing requirements to decide what features
    to promote into the active pipeline for this cycle.

    Args:
        project_id: The project ID.
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM feature_backlog WHERE project_id = ? AND status = 'pending' ORDER BY priority DESC, created_at ASC",
            (project_id,),
        ).fetchall()
        return _rows_to_list(rows)
    finally:
        conn.close()


@mcp.tool()
def promote_backlog_item(
    backlog_id: int,
    title: str,
    description: str,
    source_requirement_text: str | None = None,
    order_index: int = 0,
) -> dict[str, Any]:
    """Convert a feature backlog item into a features row for the current cycle.

    Sets the backlog item status to 'promoted'. The created feature will be
    picked up when step 4 (review features) runs.

    Args:
        backlog_id:              The feature_backlog row ID.
        title:                   Feature title (can refine the backlog item title).
        description:             Feature description.
        source_requirement_text: Optional requirement text for traceability.
        order_index:             Display order within the project.
    """
    conn = _get_conn()
    try:
        item = conn.execute(
            "SELECT * FROM feature_backlog WHERE id = ?", (backlog_id,)
        ).fetchone()
        if item is None:
            raise ValueError(f"Backlog item {backlog_id} not found")
        if item["status"] != "pending":
            raise ValueError(
                f"Backlog item {backlog_id} has status '{item['status']}'; only 'pending' items can be promoted"
            )

        cur = conn.execute(
            """
            INSERT INTO features (project_id, title, description, source_requirement_text, order_index)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                item["project_id"],
                title,
                description,
                source_requirement_text,
                order_index,
            ),
        )
        feature_id = cur.lastrowid

        conn.execute(
            "UPDATE feature_backlog SET status = 'promoted' WHERE id = ?", (backlog_id,)
        )
        conn.commit()

        feature = conn.execute(
            "SELECT * FROM features WHERE id = ?", (feature_id,)
        ).fetchone()
        return {
            "feature": _row_to_dict(feature),
            "backlog_item_status": "promoted",
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reviewer tools
# ---------------------------------------------------------------------------


@mcp.tool()
def approve_task(task_id: int, notes: str | None = None) -> dict[str, Any]:
    """Approve a reviewer task and spawn the next pipeline task(s).

    Cascade logic (all in one transaction):
      - Marks this reviewer task done.
      - Reads on_approval_spawn from pipeline_steps.
      - If spawn = JSON array of step numbers → insert one task per step number.
      - If spawn = 'per_feature' → insert one task per feature in this project.
      - If spawn = '[3]' (step 13) → next cycle begins.

    Args:
        task_id: The reviewer task ID to approve.
        notes:   Optional approval notes stored on the task.
    """
    conn = _get_conn()
    try:
        task = conn.execute(
            """
            SELECT t.*, ps.step_number, ps.on_approval_spawn, ps.requires_approval
            FROM tasks t JOIN pipeline_steps ps ON ps.id = t.step_id
            WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        if task["status"] not in ("in_progress", "pending"):
            raise ValueError(
                f"Task {task_id} cannot be approved: status is '{task['status']}'"
            )

        project_id = task["project_id"]
        spawn_spec = task["on_approval_spawn"]
        new_ids: list[int] = []

        if spawn_spec:
            if spawn_spec == "per_feature":
                features = conn.execute(
                    "SELECT id FROM features WHERE project_id = ?", (project_id,)
                ).fetchall()
                step = conn.execute(
                    "SELECT * FROM pipeline_steps WHERE step_number = 5"
                ).fetchone()
                for feat in features:
                    cur = conn.execute(
                        """
                        INSERT INTO tasks (project_id, feature_id, step_id, agent_role, status)
                        VALUES (?, ?, ?, ?, 'pending')
                        """,
                        (project_id, feat["id"], step["id"], step["agent_role"]),
                    )
                    new_ids.append(cur.lastrowid)
            else:
                step_numbers = json.loads(spawn_spec)
                new_ids = _spawn_tasks(
                    conn, project_id, step_numbers, feature_id=task["feature_id"]
                )

        conn.execute(
            """
            UPDATE tasks
            SET status = 'done', rejection_notes = ?, completed_at = datetime('now','utc')
            WHERE id = ?
            """,
            (notes, task_id),
        )
        conn.commit()

        return {
            "approved_task_id": task_id,
            "step_number": task["step_number"],
            "spawned_task_ids": new_ids,
            "notes": notes,
        }
    finally:
        conn.close()


@mcp.tool()
def reject_task(task_id: int, notes: str) -> dict[str, Any]:
    """Reject a task and re-create the appropriate worker task with rejection notes.

    For reviewer tasks: marks this task rejected and re-creates the preceding
    worker step task.
    For worker tasks: marks this task rejected and re-creates the same step task.

    The new task's retry_count = previous worker task's retry_count + 1.
    At retry_count = 3 the task is set to 'blocked' instead of retrying.

    Args:
        task_id: The task ID to reject.
        notes:   Rejection feedback injected into the new task's rejection_notes.
    """
    conn = _get_conn()
    try:
        task = conn.execute(
            """
            SELECT t.*, ps.step_number, ps.requires_approval
            FROM tasks t JOIN pipeline_steps ps ON ps.id = t.step_id
            WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        if task["status"] not in ("in_progress", "pending"):
            raise ValueError(
                f"Task {task_id} cannot be rejected: status is '{task['status']}'"
            )

        project_id = task["project_id"]
        current_step = task["step_number"]
        feature_id = task["feature_id"]

        # Reviewer steps (requires_approval = 0 means this IS a reviewer step
        # that doesn't itself need further approval — its job is to approve/reject
        # the preceding worker). Worker steps have requires_approval = 1.
        # Reviewer step numbers: 2, 4, 6, 11, 13
        # Worker step numbers:   1, 3, 5, 7, 8, 9, 10, 12
        is_reviewer_step = current_step in (2, 4, 6, 11, 13)

        if is_reviewer_step:
            # Re-create the preceding worker step
            worker_step_number = current_step - 1
        else:
            worker_step_number = current_step

        new_retry = task["retry_count"] + 1

        # Mark current task rejected
        conn.execute(
            """
            UPDATE tasks
            SET status = 'rejected', rejection_notes = ?, completed_at = datetime('now','utc')
            WHERE id = ?
            """,
            (notes, task_id),
        )

        if new_retry >= _RETRY_LIMIT:
            # Create blocked task instead of pending
            worker_step = conn.execute(
                "SELECT * FROM pipeline_steps WHERE step_number = ?",
                (worker_step_number,),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO tasks
                    (project_id, feature_id, step_id, agent_role, status, rejection_notes, retry_count)
                VALUES (?, ?, ?, ?, 'blocked', ?, ?)
                """,
                (
                    project_id,
                    feature_id,
                    worker_step["id"],
                    worker_step["agent_role"],
                    notes,
                    new_retry,
                ),
            )
            conn.commit()
            return {
                "rejected_task_id": task_id,
                "result": "blocked",
                "retry_count": new_retry,
                "notes": notes,
            }
        else:
            worker_step = conn.execute(
                "SELECT * FROM pipeline_steps WHERE step_number = ?",
                (worker_step_number,),
            ).fetchone()
            cur = conn.execute(
                """
                INSERT INTO tasks
                    (project_id, feature_id, step_id, agent_role, status, rejection_notes, retry_count)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    project_id,
                    feature_id,
                    worker_step["id"],
                    worker_step["agent_role"],
                    notes,
                    new_retry,
                ),
            )
            new_task_id = cur.lastrowid
            conn.commit()
            return {
                "rejected_task_id": task_id,
                "result": "retrying",
                "new_task_id": new_task_id,
                "retry_count": new_retry,
                "notes": notes,
            }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tester tools
# ---------------------------------------------------------------------------


@mcp.tool()
def submit_test_specs(
    task_id: int,
    specs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create test spec records and mark the step-5 task done.

    Each spec dict must have: description, expected_result.
    Optional: rationale, strategy, order_index.

    Args:
        task_id: The step-5 task ID (from claim_task).
        specs:   List of test spec dicts.
    """
    conn = _get_conn()
    try:
        task = conn.execute(
            "SELECT t.*, ps.step_number FROM tasks t JOIN pipeline_steps ps ON ps.id = t.step_id WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        if task["step_number"] != 5:
            raise ValueError(
                f"submit_test_specs requires a step-5 task; got step {task['step_number']}"
            )
        if task["status"] != "in_progress":
            raise ValueError(
                f"Task {task_id} must be claimed (in_progress) before submitting"
            )

        feature_id = task["feature_id"]
        project_id = task["project_id"]
        created: list[dict] = []

        for idx, spec in enumerate(specs):
            cur = conn.execute(
                """
                INSERT INTO test_specs
                    (feature_id, description, rationale, strategy, expected_result, order_index)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    feature_id,
                    spec["description"],
                    spec.get("rationale"),
                    spec.get("strategy"),
                    spec["expected_result"],
                    spec.get("order_index", idx),
                ),
            )
            created.append(
                {"test_spec_id": cur.lastrowid, "description": spec["description"]}
            )

        # Spawn step-6 test_reviewer task
        new_ids = _spawn_tasks(conn, project_id, [6], feature_id=feature_id)

        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = datetime('now','utc') WHERE id = ?",
            (task_id,),
        )
        conn.commit()

        return {
            "created_test_specs": created,
            "task_done": task_id,
            "spawned_task_ids": new_ids,
        }
    finally:
        conn.close()


@mcp.tool()
def submit_test_results(
    task_id: int,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Record test results for a step-8 task.

    If all results pass → marks task done and spawns step-9 (documenter).
    If any fail → increments retry_count. At retry_count >= RETRY_LIMIT, sets
    status to 'blocked'. Otherwise re-creates the step-8 task for another run.

    Each result dict must have: test_spec_id, passed (bool/int).
    Optional: notes.

    Args:
        task_id: The step-8 task ID (from claim_task).
        results: List of test result dicts.
    """
    conn = _get_conn()
    try:
        task = conn.execute(
            "SELECT t.*, ps.step_number FROM tasks t JOIN pipeline_steps ps ON ps.id = t.step_id WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        if task["step_number"] != 8:
            raise ValueError(
                f"submit_test_results requires a step-8 task; got step {task['step_number']}"
            )
        if task["status"] != "in_progress":
            raise ValueError(
                f"Task {task_id} must be claimed (in_progress) before submitting"
            )

        feature_id = task["feature_id"]
        project_id = task["project_id"]

        # Need the latest build_report for this feature
        build_report = conn.execute(
            "SELECT id FROM build_reports WHERE feature_id = ? ORDER BY created_at DESC LIMIT 1",
            (feature_id,),
        ).fetchone()
        if build_report is None:
            raise ValueError(
                f"No build report found for feature {feature_id}. Run the builder step first."
            )

        build_report_id = build_report["id"]
        all_passed = True
        created: list[dict] = []

        for r in results:
            passed = int(bool(r["passed"]))
            if not passed:
                all_passed = False
            cur = conn.execute(
                """
                INSERT INTO test_results (test_spec_id, build_report_id, passed, notes)
                VALUES (?, ?, ?, ?)
                """,
                (r["test_spec_id"], build_report_id, passed, r.get("notes")),
            )
            created.append(
                {
                    "test_result_id": cur.lastrowid,
                    "test_spec_id": r["test_spec_id"],
                    "passed": passed,
                }
            )

        if all_passed:
            conn.execute(
                "UPDATE tasks SET status = 'done', completed_at = datetime('now','utc') WHERE id = ?",
                (task_id,),
            )
            # Auto-spawn step-9 (no approval gate)
            new_ids = _spawn_tasks(conn, project_id, [9], feature_id=feature_id)
            conn.commit()
            return {
                "all_passed": True,
                "created_test_results": created,
                "task_done": task_id,
                "spawned_task_ids": new_ids,
            }
        else:
            # Tests failed — retry or block
            new_retry = task["retry_count"] + 1
            conn.execute(
                "UPDATE tasks SET status = 'rejected', completed_at = datetime('now','utc') WHERE id = ?",
                (task_id,),
            )

            if new_retry >= _RETRY_LIMIT:
                step8 = conn.execute(
                    "SELECT * FROM pipeline_steps WHERE step_number = 8"
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO tasks
                        (project_id, feature_id, step_id, agent_role, status,
                         rejection_notes, retry_count)
                    VALUES (?, ?, ?, ?, 'blocked', ?, ?)
                    """,
                    (
                        project_id,
                        feature_id,
                        step8["id"],
                        step8["agent_role"],
                        "Tests failed — retry limit reached",
                        new_retry,
                    ),
                )
                conn.commit()
                return {
                    "all_passed": False,
                    "created_test_results": created,
                    "result": "blocked",
                    "retry_count": new_retry,
                }
            else:
                step8 = conn.execute(
                    "SELECT * FROM pipeline_steps WHERE step_number = 8"
                ).fetchone()
                cur = conn.execute(
                    """
                    INSERT INTO tasks
                        (project_id, feature_id, step_id, agent_role, status,
                         rejection_notes, retry_count)
                    VALUES (?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        project_id,
                        feature_id,
                        step8["id"],
                        step8["agent_role"],
                        "Tests failed — retry build and test",
                        new_retry,
                    ),
                )
                new_task_id = cur.lastrowid
                conn.commit()
                return {
                    "all_passed": False,
                    "created_test_results": created,
                    "result": "retrying",
                    "new_task_id": new_task_id,
                    "retry_count": new_retry,
                }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Builder tools
# ---------------------------------------------------------------------------


@mcp.tool()
def submit_build_report(
    task_id: int,
    summary: str,
    issues: str | None = None,
    wins: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create a build report record and mark the step-7 task done.

    The build report is required before submit_test_results can be called.
    Approving this task (step-7 requires_approval=1) spawns step-8.

    Args:
        task_id: The step-7 task ID (from claim_task).
        summary: Summary of the build — what was implemented.
        issues:  Any issues encountered during the build.
        wins:    Highlights or things that went well.
        notes:   Any additional notes.
    """
    conn = _get_conn()
    try:
        task = conn.execute(
            "SELECT t.*, ps.step_number FROM tasks t JOIN pipeline_steps ps ON ps.id = t.step_id WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        if task["step_number"] != 7:
            raise ValueError(
                f"submit_build_report requires a step-7 task; got step {task['step_number']}"
            )
        if task["status"] != "in_progress":
            raise ValueError(
                f"Task {task_id} must be claimed (in_progress) before submitting"
            )

        feature_id = task["feature_id"]
        project_id = task["project_id"]

        cur = conn.execute(
            """
            INSERT INTO build_reports (feature_id, summary, issues, wins, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (feature_id, summary, issues, wins, notes),
        )
        report_id = cur.lastrowid

        # Spawn step-8 reviewer task (step-7 requires_approval=1 → pm_reviewer approves)
        new_ids = _spawn_tasks(conn, project_id, [8], feature_id=feature_id)

        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = datetime('now','utc') WHERE id = ?",
            (task_id,),
        )
        conn.commit()

        report = conn.execute(
            "SELECT * FROM build_reports WHERE id = ?", (report_id,)
        ).fetchone()
        return {
            "build_report": _row_to_dict(report),
            "task_done": task_id,
            "spawned_task_ids": new_ids,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Documenter tools
# ---------------------------------------------------------------------------


@mcp.tool()
def submit_retro(
    task_id: int,
    summary: str,
    recommendations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a retro report + recommendation records and auto-spawn step 10.

    Step 9 has no approval gate — this tool directly spawns the step-10
    (decisions) task for the product manager.

    Each recommendation dict must have: description, recommendation_type.

    Args:
        task_id:         The step-9 task ID (from claim_task).
        summary:         Retrospective summary for this feature.
        recommendations: List of recommendation dicts.
    """
    conn = _get_conn()
    try:
        task = conn.execute(
            "SELECT t.*, ps.step_number FROM tasks t JOIN pipeline_steps ps ON ps.id = t.step_id WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        if task["step_number"] != 9:
            raise ValueError(
                f"submit_retro requires a step-9 task; got step {task['step_number']}"
            )
        if task["status"] != "in_progress":
            raise ValueError(
                f"Task {task_id} must be claimed (in_progress) before submitting"
            )

        feature_id = task["feature_id"]
        project_id = task["project_id"]

        cur = conn.execute(
            "INSERT INTO retro_reports (feature_id, summary) VALUES (?, ?)",
            (feature_id, summary),
        )
        retro_id = cur.lastrowid

        created_recs: list[dict] = []
        for rec in recommendations:
            r = conn.execute(
                "INSERT INTO recommendations (retro_report_id, description, recommendation_type) VALUES (?, ?, ?)",
                (retro_id, rec["description"], rec["recommendation_type"]),
            )
            created_recs.append(
                {"recommendation_id": r.lastrowid, "description": rec["description"]}
            )

        # Auto-spawn step 10 (no approval gate on step 9)
        new_ids = _spawn_tasks(conn, project_id, [10], feature_id=feature_id)

        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = datetime('now','utc') WHERE id = ?",
            (task_id,),
        )
        conn.commit()

        retro = conn.execute(
            "SELECT * FROM retro_reports WHERE id = ?", (retro_id,)
        ).fetchone()
        return {
            "retro_report": _row_to_dict(retro),
            "created_recommendations": created_recs,
            "task_done": task_id,
            "spawned_task_ids": new_ids,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PM decision tools (steps 10 & 12)
# ---------------------------------------------------------------------------


@mcp.tool()
def submit_decisions(
    task_id: int,
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create decision records and mark the step-10 task done.

    Any decision with decision_type = 'new_feature' also inserts a
    feature_backlog row for consideration in the next cycle.

    Each decision dict must have: recommendation_id, decision, decision_type.
    Optional: rationale.

    Valid decision_type values: 'implement', 'new_feature', 'defer', 'reject', 'document'.

    Args:
        task_id:   The step-10 task ID (from claim_task).
        decisions: List of decision dicts.
    """
    conn = _get_conn()
    try:
        task = conn.execute(
            "SELECT t.*, ps.step_number FROM tasks t JOIN pipeline_steps ps ON ps.id = t.step_id WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        if task["step_number"] != 10:
            raise ValueError(
                f"submit_decisions requires a step-10 task; got step {task['step_number']}"
            )
        if task["status"] != "in_progress":
            raise ValueError(
                f"Task {task_id} must be claimed (in_progress) before submitting"
            )

        project_id = task["project_id"]
        feature_id = task["feature_id"]
        created_decisions: list[dict] = []
        created_backlog: list[dict] = []

        for d in decisions:
            cur = conn.execute(
                "INSERT INTO decisions (recommendation_id, decision, rationale) VALUES (?, ?, ?)",
                (d["recommendation_id"], d["decision"], d.get("rationale")),
            )
            decision_id = cur.lastrowid
            created_decisions.append(
                {"decision_id": decision_id, "decision": d["decision"]}
            )

            if d.get("decision_type") == "new_feature":
                bl = conn.execute(
                    """
                    INSERT INTO feature_backlog
                        (project_id, title, description, source_decision_id,
                         source_recommendation_id, priority)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        d.get("backlog_title", d["decision"]),
                        d.get("backlog_description", d.get("rationale", "")),
                        decision_id,
                        d["recommendation_id"],
                        d.get("priority", 0),
                    ),
                )
                created_backlog.append(
                    {
                        "backlog_id": bl.lastrowid,
                        "title": d.get("backlog_title", d["decision"]),
                    }
                )

        # Spawn step-11 reviewer task
        new_ids = _spawn_tasks(conn, project_id, [11], feature_id=feature_id)

        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = datetime('now','utc') WHERE id = ?",
            (task_id,),
        )
        conn.commit()

        return {
            "created_decisions": created_decisions,
            "created_backlog_items": created_backlog,
            "task_done": task_id,
            "spawned_task_ids": new_ids,
        }
    finally:
        conn.close()


@mcp.tool()
def submit_decision_artefact(
    task_id: int,
    decision_id: int,
    artefact_type: str,
    title: str,
    content: str,
) -> dict[str, Any]:
    """Create a decision artefact record in step 12.

    Used by the PM to capture patterns, gotchas, notes, or constraints
    discovered while implementing decisions.

    Valid artefact_type values: 'pattern', 'gotcha', 'note', 'constraint', 'other'.

    Args:
        task_id:       The step-12 task ID (must be in_progress).
        decision_id:   The decision this artefact relates to.
        artefact_type: One of: pattern, gotcha, note, constraint, other.
        title:         Short title for the artefact.
        content:       Full content — the pattern, gotcha, note, or constraint text.
    """
    conn = _get_conn()
    try:
        task = conn.execute(
            "SELECT t.*, ps.step_number FROM tasks t JOIN pipeline_steps ps ON ps.id = t.step_id WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        if task["step_number"] != 12:
            raise ValueError(
                f"submit_decision_artefact requires a step-12 task; got step {task['step_number']}"
            )
        if task["status"] != "in_progress":
            raise ValueError(
                f"Task {task_id} must be claimed (in_progress) before submitting"
            )

        valid_types = {"pattern", "gotcha", "note", "constraint", "other"}
        if artefact_type not in valid_types:
            raise ValueError(
                f"artefact_type must be one of {valid_types}; got '{artefact_type}'"
            )

        cur = conn.execute(
            """
            INSERT INTO decision_artefacts (decision_id, artefact_type, title, content)
            VALUES (?, ?, ?, ?)
            """,
            (decision_id, artefact_type, title, content),
        )
        artefact_id = cur.lastrowid
        conn.commit()

        artefact = conn.execute(
            "SELECT * FROM decision_artefacts WHERE id = ?", (artefact_id,)
        ).fetchone()
        return {"decision_artefact": _row_to_dict(artefact)}
    finally:
        conn.close()


@mcp.tool()
def complete_decisions_task(task_id: int) -> dict[str, Any]:
    """Mark a step-12 task done and spawn step-13 (final verification).

    Call this after all submit_decision_artefact calls are complete.
    Step 12 requires approval, so a pm_reviewer step-13 task is spawned.

    Args:
        task_id: The step-12 task ID (must be in_progress).
    """
    conn = _get_conn()
    try:
        task = conn.execute(
            "SELECT t.*, ps.step_number FROM tasks t JOIN pipeline_steps ps ON ps.id = t.step_id WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        if task["step_number"] != 12:
            raise ValueError(
                f"complete_decisions_task requires a step-12 task; got step {task['step_number']}"
            )
        if task["status"] != "in_progress":
            raise ValueError(
                f"Task {task_id} must be claimed (in_progress) before completing"
            )

        project_id = task["project_id"]
        feature_id = task["feature_id"]

        new_ids = _spawn_tasks(conn, project_id, [13], feature_id=feature_id)

        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = datetime('now','utc') WHERE id = ?",
            (task_id,),
        )
        conn.commit()

        return {
            "task_done": task_id,
            "spawned_task_ids": new_ids,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
