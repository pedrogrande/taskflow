"""
TaskFlow — MCP server tool tests
Covers all tools, cascade logic, retry/block, per_feature spawn, cycle restart,
feature_backlog population, and decision_artefacts creation.
"""

import os
import pathlib
import sqlite3
import sys
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def server(tmp_path):
    """Fresh mcp_server module with an isolated DB per test."""
    db = tmp_path / "taskflow.db"
    os.environ["DB_PATH"] = str(db)
    if "mcp_server" in sys.modules:
        del sys.modules["mcp_server"]
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "servers"))
    import mcp_server

    yield mcp_server
    os.environ.pop("DB_PATH", None)


@pytest.fixture()
def seeded(server):
    """Server + a seeded project running from start_project (step-3 task ready)."""
    r = server.start_project("Test Project", "A comprehensive brief.", "/tmp/brief.md")
    return server, r["project"]["id"]


@pytest.fixture()
def with_features(seeded):
    """Project with 2 features submitted, at step-4 gate."""
    s, pid = seeded
    t3 = s.claim_task(s.read_pending_tasks("product_manager")[0]["id"])
    s.submit_features(
        t3["id"],
        [
            {
                "title": "Feature A",
                "description": "Desc A",
                "definitions_of_done": [{"criterion": "Returns 200", "verifiable": 1}],
            },
            {
                "title": "Feature B",
                "description": "Desc B",
                "definitions_of_done": [
                    {"criterion": "Returns 404 on missing", "verifiable": 1}
                ],
            },
        ],
    )
    return s, pid


@pytest.fixture()
def step5_ready(with_features):
    """Both features through step-4 approval → 2 step-5 tasks pending."""
    s, pid = with_features
    t4 = s.claim_task(s.read_pending_tasks("pm_reviewer")[0]["id"])
    s.approve_task(t4["id"])
    return s, pid


# ---------------------------------------------------------------------------
# Universal tools
# ---------------------------------------------------------------------------


def test_read_pending_tasks_empty(server):
    assert server.read_pending_tasks("product_manager") == []


def test_read_pending_tasks_after_seed(seeded):
    s, _ = seeded
    tasks = s.read_pending_tasks("product_manager")
    assert len(tasks) == 1
    assert tasks[0]["step_number"] == 3


def test_claim_task(seeded):
    s, _ = seeded
    task_id = s.read_pending_tasks("product_manager")[0]["id"]
    result = s.claim_task(task_id)
    assert result["status"] == "in_progress"


def test_claim_task_not_pending_raises(seeded):
    s, _ = seeded
    task_id = s.read_pending_tasks("product_manager")[0]["id"]
    s.claim_task(task_id)
    with pytest.raises(ValueError, match="cannot be claimed"):
        s.claim_task(task_id)


def test_read_task_context_step3(seeded):
    s, pid = seeded
    task = s.read_pending_tasks("product_manager")[0]
    s.claim_task(task["id"])
    ctx = s.read_task_context(task["id"])
    assert ctx["task"]["step_number"] == 3
    assert "project" in ctx
    assert ctx["project"]["id"] == pid


# ---------------------------------------------------------------------------
# start_project / list_projects / read_pipeline_status
# ---------------------------------------------------------------------------


def test_start_project_creates_project(server):
    r = server.start_project("My App", "Brief text.", "/path/brief.md")
    assert r["project"]["name"] == "My App"
    assert r["project"]["brief_text"] == "Brief text."
    assert r["project"]["brief_path"] == "/path/brief.md"


def test_start_project_spawns_step3(server):
    r = server.start_project("P", "B")
    tasks = server.read_pending_tasks("product_manager")
    assert len(tasks) == 1
    assert tasks[0]["step_number"] == 3


def test_start_project_no_brief_path(server):
    r = server.start_project("P", "B")
    assert r["project"]["brief_path"] is None


def test_list_projects_empty(server):
    assert server.list_projects() == []


def test_list_projects_with_project(seeded):
    s, pid = seeded
    projs = s.list_projects()
    assert len(projs) == 1
    assert projs[0]["id"] == pid


def test_read_pipeline_status(seeded):
    s, pid = seeded
    status = s.read_pipeline_status(pid)
    assert status["project"]["id"] == pid
    assert status["summary"]["pending"] == 1


def test_read_pipeline_status_not_found(server):
    with pytest.raises(ValueError, match="not found"):
        server.read_pipeline_status(999)


# ---------------------------------------------------------------------------
# submit_features + cascade
# ---------------------------------------------------------------------------


def test_submit_features_creates_records(seeded):
    s, pid = seeded
    t3 = s.claim_task(s.read_pending_tasks("product_manager")[0]["id"])
    r = s.submit_features(
        t3["id"],
        [
            {
                "title": "F1",
                "description": "D1",
                "definitions_of_done": [{"criterion": "c1", "verifiable": 1}],
            },
        ],
    )
    assert len(r["created_features"]) == 1
    assert r["created_features"][0]["title"] == "F1"


def test_submit_features_wrong_step_raises(seeded):
    s, _ = seeded
    # Step 3 task claimed; then try submitting features with wrong step
    t3 = s.claim_task(s.read_pending_tasks("product_manager")[0]["id"])
    # Manually create a step-5 task to get wrong step id
    with pytest.raises(ValueError, match="step-3"):
        # Pass t3 but patch: just call with a step-10 task (wrong step)
        import sqlite3 as sl

        db = os.environ["DB_PATH"]
        c = sl.connect(db)
        c.execute("PRAGMA foreign_keys = ON")
        step10 = c.execute(
            "SELECT * FROM pipeline_steps WHERE step_number=10"
        ).fetchone()
        proj = c.execute("SELECT id FROM projects LIMIT 1").fetchone()
        cur = c.execute(
            "INSERT INTO tasks (project_id,step_id,agent_role,status) VALUES (?,?,?,'pending')",
            (proj[0], step10[0], "product_manager"),
        )
        fake_id = cur.lastrowid
        c.commit()
        c.close()
        t_fake = s.claim_task(fake_id)
        s.submit_features(t_fake["id"], [])


def test_approve_step4_per_feature_spawn(with_features):
    s, pid = with_features
    t4 = s.claim_task(s.read_pending_tasks("pm_reviewer")[0]["id"])
    r = s.approve_task(t4["id"])
    tester_tasks = s.read_pending_tasks("tester")
    assert len(tester_tasks) == 2
    assert all(t["step_number"] == 5 for t in tester_tasks)


# ---------------------------------------------------------------------------
# Test specs + test_reviewer cascade
# ---------------------------------------------------------------------------


def test_submit_test_specs(step5_ready):
    s, _ = step5_ready
    tasks = s.read_pending_tasks("tester")
    t5 = s.claim_task(tasks[0]["id"])
    r = s.submit_test_specs(
        t5["id"],
        [
            {"description": "Returns 200", "expected_result": "HTTP 200"},
        ],
    )
    assert len(r["created_test_specs"]) == 1
    # Spawns step-6 for test_reviewer
    rev_tasks = s.read_pending_tasks("test_reviewer")
    assert len(rev_tasks) >= 1
    assert rev_tasks[0]["step_number"] == 6


def test_approve_step6_spawns_builder(step5_ready):
    s, _ = step5_ready
    t5 = s.claim_task(s.read_pending_tasks("tester")[0]["id"])
    feature_id = t5["feature_id"]
    r5 = s.submit_test_specs(t5["id"], [{"description": "D", "expected_result": "E"}])
    t6 = s.claim_task(s.read_pending_tasks("test_reviewer")[0]["id"])
    s.approve_task(t6["id"])
    builder_tasks = s.read_pending_tasks("builder")
    assert any(
        t["step_number"] == 7 and t["feature_id"] == feature_id for t in builder_tasks
    )


# ---------------------------------------------------------------------------
# Build report + test loop
# ---------------------------------------------------------------------------


def _setup_to_step8(s, feature_id):
    """Helper: push through steps 5-7 for a given feature, return spec_ids."""
    # Find pending step-5 for this feature
    tasks = [t for t in s.read_pending_tasks("tester") if t["feature_id"] == feature_id]
    t5 = s.claim_task(tasks[0]["id"])
    r5 = s.submit_test_specs(
        t5["id"],
        [
            {"description": "Spec A", "expected_result": "HTTP 200"},
            {"description": "Spec B", "expected_result": "HTTP 400"},
        ],
    )
    spec_ids = [sp["test_spec_id"] for sp in r5["created_test_specs"]]
    t6 = s.claim_task(s.read_pending_tasks("test_reviewer")[0]["id"])
    s.approve_task(t6["id"])
    t7 = s.claim_task(s.read_pending_tasks("builder")[0]["id"])
    s.submit_build_report(t7["id"], "Build complete")
    return spec_ids


def test_submit_build_report(step5_ready):
    s, _ = step5_ready
    tasks = s.read_pending_tasks("tester")
    feature_id = tasks[0]["feature_id"]
    spec_ids = _setup_to_step8(s, feature_id)
    step8_tasks = s.read_pending_tasks("tester")
    assert any(t["step_number"] == 8 for t in step8_tasks)


def test_test_loop_passing(step5_ready):
    s, _ = step5_ready
    feature_id = s.read_pending_tasks("tester")[0]["feature_id"]
    spec_ids = _setup_to_step8(s, feature_id)
    t8 = s.claim_task(
        [t for t in s.read_pending_tasks("tester") if t["step_number"] == 8][0]["id"]
    )
    r = s.submit_test_results(
        t8["id"],
        [
            {"test_spec_id": spec_ids[0], "passed": True},
            {"test_spec_id": spec_ids[1], "passed": True},
        ],
    )
    assert r["all_passed"] is True
    # Should spawn step-9
    doc_tasks = s.read_pending_tasks("documenter")
    assert any(t["step_number"] == 9 for t in doc_tasks)


def test_test_loop_retry_on_failure(step5_ready):
    s, _ = step5_ready
    feature_id = s.read_pending_tasks("tester")[0]["feature_id"]
    spec_ids = _setup_to_step8(s, feature_id)
    t8 = s.claim_task(
        [t for t in s.read_pending_tasks("tester") if t["step_number"] == 8][0]["id"]
    )
    r = s.submit_test_results(
        t8["id"],
        [
            {"test_spec_id": spec_ids[0], "passed": False, "notes": "500 error"},
            {"test_spec_id": spec_ids[1], "passed": True},
        ],
    )
    assert r["all_passed"] is False
    assert r["result"] == "retrying"
    assert r["retry_count"] == 1
    # New step-8 task created
    new_step8 = [t for t in s.read_pending_tasks("tester") if t["step_number"] == 8]
    assert len(new_step8) == 1
    assert new_step8[0]["retry_count"] == 1


def test_test_loop_blocks_at_retry_limit(step5_ready):
    s, _ = step5_ready
    feature_id = s.read_pending_tasks("tester")[0]["feature_id"]
    spec_ids = _setup_to_step8(s, feature_id)

    for attempt in range(3):
        tasks = [t for t in s.read_pending_tasks("tester") if t["step_number"] == 8]
        assert len(tasks) == 1, f"Expected 1 step-8 task at attempt {attempt}"
        t8 = s.claim_task(tasks[0]["id"])
        r = s.submit_test_results(
            t8["id"],
            [
                {"test_spec_id": spec_ids[0], "passed": False},
                {"test_spec_id": spec_ids[1], "passed": False},
            ],
        )

    assert r["result"] == "blocked"
    assert r["retry_count"] == 3
    # No more pending step-8 tasks
    remaining = [t for t in s.read_pending_tasks("tester") if t["step_number"] == 8]
    assert len(remaining) == 0


# ---------------------------------------------------------------------------
# reject_task
# ---------------------------------------------------------------------------


def test_reject_worker_task_creates_retry(step5_ready):
    s, _ = step5_ready
    t5 = s.claim_task(s.read_pending_tasks("tester")[0]["id"])
    r = s.reject_task(t5["id"], "Specs need more detail")
    assert r["result"] == "retrying"
    assert r["retry_count"] == 1
    new_tasks = [
        t
        for t in s.read_pending_tasks("tester")
        if t["step_number"] == 5 and t["rejection_notes"] is not None
    ]
    assert len(new_tasks) == 1
    assert new_tasks[0]["rejection_notes"] == "Specs need more detail"


def test_reject_reviewer_task_recreates_worker(step5_ready):
    s, _ = step5_ready
    t5 = s.claim_task(s.read_pending_tasks("tester")[0]["id"])
    s.submit_test_specs(t5["id"], [{"description": "D", "expected_result": "E"}])
    t6 = s.claim_task(s.read_pending_tasks("test_reviewer")[0]["id"])
    r = s.reject_task(t6["id"], "Missing edge case")
    assert r["result"] == "retrying"
    # Recreates step-5 (preceding worker)
    new_step5 = [t for t in s.read_pending_tasks("tester") if t["step_number"] == 5]
    assert len(new_step5) >= 1


def test_reject_blocks_at_retry_limit(step5_ready):
    s, _ = step5_ready
    # Pin to the first feature so retries accumulate on one task chain
    feature_id = s.read_pending_tasks("tester")[0]["feature_id"]
    for _ in range(3):
        tasks = [
            t
            for t in s.read_pending_tasks("tester")
            if t["step_number"] == 5 and t["feature_id"] == feature_id
        ]
        t = s.claim_task(tasks[0]["id"])
        r = s.reject_task(t["id"], "Still wrong")
    assert r["result"] == "blocked"


# ---------------------------------------------------------------------------
# Documenter + decisions + cycle restart
# ---------------------------------------------------------------------------


def _run_to_step9(s):
    """Run a project all the way through steps 3-8 (passing) and return (project_id, spec_ids)."""
    pid = s.list_projects()[0]["id"]
    t3 = s.claim_task(s.read_pending_tasks("product_manager")[0]["id"])
    s.submit_features(
        t3["id"],
        [
            {
                "title": "F",
                "description": "D",
                "definitions_of_done": [{"criterion": "c", "verifiable": 1}],
            }
        ],
    )
    t4 = s.claim_task(s.read_pending_tasks("pm_reviewer")[0]["id"])
    s.approve_task(t4["id"])
    feature_id = s.read_pending_tasks("tester")[0]["feature_id"]
    spec_ids = _setup_to_step8(s, feature_id)
    t8 = s.claim_task(
        [t for t in s.read_pending_tasks("tester") if t["step_number"] == 8][0]["id"]
    )
    s.submit_test_results(
        t8["id"], [{"test_spec_id": sid, "passed": True} for sid in spec_ids]
    )
    return pid, spec_ids


def test_submit_retro_auto_spawns_step10(seeded):
    s, _ = seeded
    pid, _ = _run_to_step9(s)
    t9 = s.claim_task(s.read_pending_tasks("documenter")[0]["id"])
    r = s.submit_retro(
        t9["id"],
        "Went well",
        [
            {"description": "Add caching", "recommendation_type": "improve"},
        ],
    )
    assert r["retro_report"]["id"] is not None
    pm_tasks = s.read_pending_tasks("product_manager")
    assert any(t["step_number"] == 10 for t in pm_tasks)


def test_submit_decisions_creates_backlog_for_new_feature(seeded):
    s, _ = seeded
    pid, _ = _run_to_step9(s)
    t9 = s.claim_task(s.read_pending_tasks("documenter")[0]["id"])
    r9 = s.submit_retro(
        t9["id"],
        "Summary",
        [
            {"description": "Build dashboard", "recommendation_type": "new_feature"},
        ],
    )
    rec_id = r9["created_recommendations"][0]["recommendation_id"]
    t10 = s.claim_task(s.read_pending_tasks("product_manager")[0]["id"])
    r10 = s.submit_decisions(
        t10["id"],
        [
            {
                "recommendation_id": rec_id,
                "decision": "Add dashboard",
                "decision_type": "new_feature",
                "backlog_title": "Dashboard Feature",
                "backlog_description": "A dashboard for ops",
            }
        ],
    )
    assert len(r10["created_backlog_items"]) == 1
    backlog = s.read_backlog(pid)
    assert len(backlog) == 1
    assert backlog[0]["title"] == "Dashboard Feature"


def test_cycle_restart_on_step13_approval(seeded):
    s, _ = seeded
    pid, _ = _run_to_step9(s)
    # Steps 9 → 10
    t9 = s.claim_task(s.read_pending_tasks("documenter")[0]["id"])
    r9 = s.submit_retro(
        t9["id"], "Done", [{"description": "Nothing", "recommendation_type": "close"}]
    )
    rec_id = r9["created_recommendations"][0]["recommendation_id"]
    # Step 10
    t10 = s.claim_task(s.read_pending_tasks("product_manager")[0]["id"])
    r10 = s.submit_decisions(
        t10["id"],
        [{"recommendation_id": rec_id, "decision": "close", "decision_type": "defer"}],
    )
    dec_id = r10["created_decisions"][0]["decision_id"]
    # Step 11
    t11 = s.claim_task(s.read_pending_tasks("pm_reviewer")[0]["id"])
    s.approve_task(t11["id"])
    # Step 12
    t12 = s.claim_task(s.read_pending_tasks("product_manager")[0]["id"])
    s.submit_decision_artefact(t12["id"], dec_id, "note", "All good", "No issues found")
    r12 = s.complete_decisions_task(t12["id"])
    # Step 13
    t13 = s.claim_task(s.read_pending_tasks("pm_reviewer")[0]["id"])
    r13 = s.approve_task(t13["id"], "Cycle complete")
    # Should have spawned a new step-3 task
    new_step3 = s.read_pending_tasks("product_manager")
    assert len(new_step3) == 1
    assert new_step3[0]["step_number"] == 3


# ---------------------------------------------------------------------------
# decision_artefacts
# ---------------------------------------------------------------------------


def test_submit_decision_artefact_valid_types(seeded):
    s, _ = seeded
    pid, _ = _run_to_step9(s)
    t9 = s.claim_task(s.read_pending_tasks("documenter")[0]["id"])
    r9 = s.submit_retro(
        t9["id"], "S", [{"description": "D", "recommendation_type": "improve"}]
    )
    rec_id = r9["created_recommendations"][0]["recommendation_id"]
    t10 = s.claim_task(s.read_pending_tasks("product_manager")[0]["id"])
    r10 = s.submit_decisions(
        t10["id"],
        [{"recommendation_id": rec_id, "decision": "do", "decision_type": "implement"}],
    )
    dec_id = r10["created_decisions"][0]["decision_id"]
    t11 = s.claim_task(s.read_pending_tasks("pm_reviewer")[0]["id"])
    s.approve_task(t11["id"])
    t12 = s.claim_task(s.read_pending_tasks("product_manager")[0]["id"])
    for atype in ["pattern", "gotcha", "note", "constraint", "other"]:
        r = s.submit_decision_artefact(
            t12["id"], dec_id, atype, f"Title {atype}", "Content"
        )
        assert r["decision_artefact"]["artefact_type"] == atype


def test_submit_decision_artefact_invalid_type_raises(seeded):
    s, _ = seeded
    pid, _ = _run_to_step9(s)
    t9 = s.claim_task(s.read_pending_tasks("documenter")[0]["id"])
    r9 = s.submit_retro(
        t9["id"], "S", [{"description": "D", "recommendation_type": "improve"}]
    )
    rec_id = r9["created_recommendations"][0]["recommendation_id"]
    t10 = s.claim_task(s.read_pending_tasks("product_manager")[0]["id"])
    r10 = s.submit_decisions(
        t10["id"],
        [{"recommendation_id": rec_id, "decision": "do", "decision_type": "implement"}],
    )
    dec_id = r10["created_decisions"][0]["decision_id"]
    t11 = s.claim_task(s.read_pending_tasks("pm_reviewer")[0]["id"])
    s.approve_task(t11["id"])
    t12 = s.claim_task(s.read_pending_tasks("product_manager")[0]["id"])
    with pytest.raises(ValueError, match="artefact_type"):
        s.submit_decision_artefact(t12["id"], dec_id, "invalid_type", "T", "C")


# ---------------------------------------------------------------------------
# No delete tools exist
# ---------------------------------------------------------------------------


def test_no_delete_tools_on_module(server):
    import inspect

    tool_funcs = [
        name
        for name in dir(server)
        if name.startswith("delete") or name.startswith("remove")
    ]
    assert tool_funcs == [], f"Delete tools found: {tool_funcs}"


# ---------------------------------------------------------------------------
# ingest_brief
# ---------------------------------------------------------------------------

SAMPLE_BRIEF = {
    "metadata": {"schema_version": "1.0", "created_at": "2026-06-06T00:00:00Z"},
    "project_identity": {
        "name": "BookingFlow",
        "organisation": "Acme Accounting",
        "industry": "Professional services",
        "problem": "Staff track bookings in a spreadsheet causing double-bookings.",
        "success_definition": "Zero double-bookings; 50% less admin time.",
        "out_of_scope": "No mobile app, no payment processing.",
    },
    "goals": {
        "outcomes": ["Clients can book online", "Staff get automatic reminders"],
        "metrics": [
            {
                "metric": "Admin time",
                "current_state": "10h/week",
                "target": "2h/week",
                "how_measured": "Time tracking",
            },
        ],
    },
    "users": {
        "roles": [
            {
                "role": "Admin staff",
                "description": "5 office staff",
                "primary_workflow": "Log in → view bookings",
            },
            {
                "role": "Client",
                "description": "External clients",
                "primary_workflow": "Visit site → book",
            },
        ],
        "stakeholders": [
            {
                "name": "Jane Smith",
                "title": "Operations Manager",
                "authority": "Day-to-day decisions",
            },
        ],
        "decision_maker": {"name": "Jane Smith", "contact": "jane@acme.com"},
        "acceptance_testers": "Operations Manager and one admin staff",
    },
    "features": [
        {
            "name": "Appointment booking",
            "description": "Client self-service booking",
            "priority": "Must",
            "phase": "1",
        },
        {
            "name": "Email notifications",
            "description": "Automatic reminders",
            "priority": "Must",
            "phase": "1",
        },
        {
            "name": "SMS reminders",
            "description": "Optional SMS",
            "priority": "Could",
            "phase": "2",
        },
    ],
    "workflows": [
        {
            "actor": "Client",
            "trigger": "Wants to book",
            "steps": "1. Visit site 2. Pick time 3. Submit",
            "outcome": "Booking confirmed",
        },
    ],
    "non_functional": {
        "performance": {"required": True, "notes": "Under 2s page load"},
        "security": {"required": True, "notes": "OWASP Top 10"},
        "accessibility": {"required": False, "notes": ""},
        "availability": {"required": False, "notes": ""},
        "data_privacy": {"required": True, "notes": "Australian Privacy Act"},
        "compliance": {"required": False, "notes": ""},
        "other": {"required": False, "notes": ""},
    },
    "integrations": {
        "systems": [
            {
                "system": "Xero",
                "purpose": "Create invoices",
                "direction": "outbound",
                "auth_method": "OAuth 2.0",
                "phase_1_required": "yes",
            },
            {
                "system": "Google Calendar",
                "purpose": "Sync bookings",
                "direction": "bidirectional",
                "auth_method": "OAuth 2.0",
                "phase_1_required": "no",
            },
        ],
        "existing_system": "Legacy Access DB with 3 years of records",
    },
    "platforms": {"targets": ["web"], "hosting": "azure"},
    "design": {
        "source": "team",
        "references": "calendly.com",
        "brand": "Brand guide to be emailed",
        "maintenance": "IT contractor",
    },
    "timeline": {
        "deadline": {"date": "2026-09-01", "type": "hard", "reason": "Busy season"},
        "milestones": [
            {"description": "Internal prototype", "target_date": "2026-07-15"}
        ],
        "release_phases": [
            {
                "phase": "1",
                "description": "Core booking flow",
                "target_date": "2026-09-01",
            },
            {
                "phase": "2",
                "description": "SMS + calendar sync",
                "target_date": "2026-12-01",
            },
        ],
    },
    "risks": [
        {
            "description": "Key contact part-time",
            "likelihood": "M",
            "impact": "H",
            "mitigation": "Agree escalation path",
        },
    ],
}


import json as _json


def test_ingest_brief_creates_project(server):
    r = server.ingest_brief(_json.dumps(SAMPLE_BRIEF))
    assert r["project"]["name"] == "BookingFlow"
    assert r["project"]["organisation"] == "Acme Accounting"
    assert r["project"]["deadline_date"] == "2026-09-01"
    assert r["project"]["deadline_type"] == "hard"
    assert r["project"]["hosting"] == "azure"


def test_ingest_brief_spawns_step3(server):
    server.ingest_brief(_json.dumps(SAMPLE_BRIEF))
    tasks = server.read_pending_tasks("product_manager")
    assert len(tasks) == 1
    assert tasks[0]["step_number"] == 3


def test_ingest_brief_populates_outcomes(server):
    r = server.ingest_brief(_json.dumps(SAMPLE_BRIEF))
    pid = r["project"]["id"]
    assert r["ingested"]["outcomes"] == 2


def test_ingest_brief_populates_metrics(server):
    r = server.ingest_brief(_json.dumps(SAMPLE_BRIEF))
    assert r["ingested"]["success_metrics"] == 1


def test_ingest_brief_populates_user_roles(server):
    r = server.ingest_brief(_json.dumps(SAMPLE_BRIEF))
    assert r["ingested"]["user_roles"] == 2


def test_ingest_brief_populates_workflows(server):
    r = server.ingest_brief(_json.dumps(SAMPLE_BRIEF))
    assert r["ingested"]["workflows"] == 1


def test_ingest_brief_populates_nfr_only_enabled(server):
    r = server.ingest_brief(_json.dumps(SAMPLE_BRIEF))
    # performance, security, data_privacy are True; others are False → 3
    assert r["ingested"]["nfr"] == 3


def test_ingest_brief_populates_integrations(server):
    r = server.ingest_brief(_json.dumps(SAMPLE_BRIEF))
    assert r["ingested"]["integrations"] == 2


def test_ingest_brief_phase_1_required_flag(server):
    import sqlite3 as sl

    r = server.ingest_brief(_json.dumps(SAMPLE_BRIEF))
    pid = r["project"]["id"]
    db = os.environ["DB_PATH"]
    c = sl.connect(db)
    c.row_factory = sl.Row
    rows = c.execute(
        "SELECT system, phase_1_required FROM integrations WHERE project_id=?", (pid,)
    ).fetchall()
    c.close()
    by_name = {row["system"]: row["phase_1_required"] for row in rows}
    assert by_name["Xero"] == 1
    assert by_name["Google Calendar"] == 0


def test_ingest_brief_populates_risks(server):
    r = server.ingest_brief(_json.dumps(SAMPLE_BRIEF))
    assert r["ingested"]["risks"] == 1


def test_ingest_brief_populates_brief_features(server):
    r = server.ingest_brief(_json.dumps(SAMPLE_BRIEF))
    assert r["ingested"]["brief_features"] == 3


def test_ingest_brief_invalid_json_raises(server):
    with pytest.raises(ValueError, match="Invalid JSON"):
        server.ingest_brief("not json {{{")


def test_ingest_brief_context_in_step3(server):
    """read_task_context for step 3 includes brief data after ingest_brief."""
    server.ingest_brief(_json.dumps(SAMPLE_BRIEF))
    task = server.read_pending_tasks("product_manager")[0]
    server.claim_task(task["id"])
    ctx = server.read_task_context(task["id"])
    assert "brief" in ctx
    assert len(ctx["brief"]["user_roles"]) == 2
    assert len(ctx["brief"]["non_functional_requirements"]) == 3
    assert len(ctx["brief"]["integrations"]) == 2
    assert ctx["brief"]["brief_features"][0]["priority"] == "Must"


def test_ingest_brief_context_empty_for_start_project(server):
    """start_project (free-text path) still works; brief context returns empty collections."""
    server.start_project("Freetext Project", "Just a paragraph of notes.")
    task = server.read_pending_tasks("product_manager")[0]
    server.claim_task(task["id"])
    ctx = server.read_task_context(task["id"])
    assert "brief" in ctx
    assert ctx["brief"]["user_roles"] == []
    assert ctx["brief"]["integrations"] == []
