-- TaskFlow DB — schema + pipeline seed data
-- Idempotent: safe to run multiple times (CREATE TABLE IF NOT EXISTS)
-- All foreign keys enforced at connection level via PRAGMA foreign_keys = ON

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Pipeline definition
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pipeline_steps (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    step_number         INTEGER UNIQUE NOT NULL,
    name                TEXT NOT NULL,
    agent_role          TEXT NOT NULL CHECK (agent_role IN (
                            'product_manager', 'pm_reviewer', 'tester',
                            'test_reviewer', 'builder', 'documenter')),
    output_record_type  TEXT NOT NULL,
    requires_approval   INTEGER NOT NULL DEFAULT 1 CHECK (requires_approval IN (0, 1)),
    -- JSON array of step numbers e.g. '[3]', or 'per_feature', or null (no spawn)
    on_approval_spawn   TEXT,
    on_rejection_action TEXT NOT NULL DEFAULT 'retry' CHECK (on_rejection_action IN ('retry', 'repair_step')),
    repair_step_number  INTEGER
);

-- Seed the 13 pipeline steps (INSERT OR IGNORE keeps it idempotent)
INSERT OR IGNORE INTO pipeline_steps
    (step_number, name, agent_role, output_record_type, requires_approval, on_approval_spawn)
VALUES
    (1,  'Ingest brief',             'product_manager', 'project',           1, '[2]'),
    (2,  'Review project',           'pm_reviewer',     'task_approval',     0, '[3]'),
    (3,  'Define features',          'product_manager', 'features',          1, '[4]'),
    (4,  'Review features',          'pm_reviewer',     'task_approval',     0, 'per_feature'),
    (5,  'Write test specs',         'tester',          'test_specs',        1, '[6]'),
    (6,  'Review test specs',        'test_reviewer',   'task_approval',     0, '[7]'),
    (7,  'Build',                    'builder',         'build_report',      1, '[8]'),
    (8,  'Run tests',                'tester',          'test_results',      0, '[9]'),
    (9,  'Retrospective',            'documenter',      'retro_report',      0,  NULL),
    (10, 'Decisions',                'product_manager', 'decisions',         1, '[11]'),
    (11, 'Review decisions',         'pm_reviewer',     'task_approval',     0, '[12]'),
    (12, 'Implement decisions',      'product_manager', 'decision_artefacts',1, '[13]'),
    (13, 'Final verification',       'pm_reviewer',     'task_approval',     0, '[3]');

-- Note: step 8 on_approval_spawn is NULL because submit_test_results auto-spawns step 9
-- on all-pass. Step 9 on_approval_spawn is NULL because submit_retro auto-spawns step 10.
-- Step 13 on_approval_spawn '[3]' triggers the next cycle.

-- ---------------------------------------------------------------------------
-- Projects
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS projects (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    brief_text              TEXT NOT NULL,          -- full brief content (raw JSON or free text)
    brief_path              TEXT,                   -- source file path (nullable if chat-initiated)
    -- Scalar fields parsed from the brief form JSON --
    organisation            TEXT,
    industry                TEXT,
    problem                 TEXT,
    success_definition      TEXT,
    out_of_scope            TEXT,
    decision_maker_name     TEXT,
    decision_maker_contact  TEXT,
    acceptance_testers      TEXT,
    hosting                 TEXT,
    design_source           TEXT,
    design_references       TEXT,
    brand                   TEXT,
    maintenance             TEXT,
    deadline_date           TEXT,
    deadline_type           TEXT CHECK (deadline_type IN ('hard', 'soft', NULL)),
    deadline_reason         TEXT,
    platforms               TEXT,   -- JSON array of platform strings
    -- ---- --
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'complete', 'archived')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- ---------------------------------------------------------------------------
-- Brief-derived lookup tables
-- (populated by ingest_brief; read by agents via read_task_context)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS project_outcomes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    outcome     TEXT NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS success_metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id),
    metric        TEXT NOT NULL,
    current_state TEXT,
    target        TEXT,
    how_measured  TEXT
);

CREATE TABLE IF NOT EXISTS user_roles (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES projects(id),
    role             TEXT NOT NULL,
    description      TEXT,
    primary_workflow TEXT
);

CREATE TABLE IF NOT EXISTS stakeholders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    name        TEXT NOT NULL,
    title       TEXT,
    authority   TEXT
);

CREATE TABLE IF NOT EXISTS key_workflows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    actor       TEXT,
    trigger     TEXT,
    steps       TEXT,
    outcome     TEXT,
    order_index INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS non_functional_requirements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    nfr_type    TEXT NOT NULL CHECK (nfr_type IN (
                    'performance', 'security', 'accessibility',
                    'availability', 'data_privacy', 'compliance', 'other')),
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS integrations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id        INTEGER NOT NULL REFERENCES projects(id),
    system            TEXT NOT NULL,
    purpose           TEXT,
    direction         TEXT CHECK (direction IN ('inbound', 'outbound', 'bidirectional', NULL)),
    auth_method       TEXT,
    phase_1_required  INTEGER NOT NULL DEFAULT 1 CHECK (phase_1_required IN (0, 1))
);

CREATE TABLE IF NOT EXISTS project_risks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    description TEXT NOT NULL,
    likelihood  TEXT CHECK (likelihood IN ('H', 'M', 'L', NULL)),
    impact      TEXT CHECK (impact IN ('H', 'M', 'L', NULL)),
    mitigation  TEXT
);

CREATE TABLE IF NOT EXISTS release_phases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    phase_number TEXT,
    description  TEXT,
    target_date  TEXT
);

-- Brief-suggested features (from form JSON, before PM formally defines them at step 3)
CREATE TABLE IF NOT EXISTS brief_features (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    name        TEXT NOT NULL,
    description TEXT,
    priority    TEXT CHECK (priority IN ('Must', 'Should', 'Could', NULL)),
    phase       TEXT
);

-- ---------------------------------------------------------------------------
-- Features
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS features (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id              INTEGER NOT NULL REFERENCES projects(id),
    title                   TEXT NOT NULL,
    description             TEXT NOT NULL,
    source_requirement_text TEXT,
    order_index             INTEGER NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------------------------
-- Definitions of done
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS definitions_of_done (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id  INTEGER NOT NULL REFERENCES features(id),
    criterion   TEXT NOT NULL,
    verifiable  INTEGER NOT NULL DEFAULT 1 CHECK (verifiable IN (0, 1))
);

-- ---------------------------------------------------------------------------
-- Test specs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS test_specs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id      INTEGER NOT NULL REFERENCES features(id),
    description     TEXT NOT NULL,
    rationale       TEXT,
    strategy        TEXT,
    expected_result TEXT NOT NULL,
    order_index     INTEGER NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------------------------
-- Build reports
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS build_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id  INTEGER NOT NULL REFERENCES features(id),
    summary     TEXT NOT NULL,
    issues      TEXT,
    wins        TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- ---------------------------------------------------------------------------
-- Test results
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS test_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    test_spec_id    INTEGER NOT NULL REFERENCES test_specs(id),
    build_report_id INTEGER NOT NULL REFERENCES build_reports(id),
    passed          INTEGER NOT NULL CHECK (passed IN (0, 1)),
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- ---------------------------------------------------------------------------
-- Retro reports
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS retro_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id  INTEGER NOT NULL REFERENCES features(id),
    summary     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- ---------------------------------------------------------------------------
-- Recommendations
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS recommendations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    retro_report_id     INTEGER NOT NULL REFERENCES retro_reports(id),
    description         TEXT NOT NULL,
    recommendation_type TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Decisions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS decisions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER NOT NULL REFERENCES recommendations(id),
    decision          TEXT NOT NULL,
    rationale         TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- ---------------------------------------------------------------------------
-- Decision artefacts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS decision_artefacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id     INTEGER NOT NULL REFERENCES decisions(id),
    artefact_type   TEXT NOT NULL CHECK (artefact_type IN ('pattern', 'gotcha', 'note', 'constraint', 'other')),
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- ---------------------------------------------------------------------------
-- Feature backlog
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feature_backlog (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id              INTEGER NOT NULL REFERENCES projects(id),
    title                   TEXT NOT NULL,
    description             TEXT NOT NULL,
    source_decision_id      INTEGER REFERENCES decisions(id),
    source_recommendation_id INTEGER REFERENCES recommendations(id),
    priority                INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'promoted', 'deferred', 'rejected')),
    created_at              TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- ---------------------------------------------------------------------------
-- Tasks
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER REFERENCES projects(id),  -- nullable for step-1/2 pre-project tasks
    feature_id      INTEGER REFERENCES features(id),  -- nullable: pre-feature steps
    step_id         INTEGER NOT NULL REFERENCES pipeline_steps(id),
    agent_role      TEXT NOT NULL CHECK (agent_role IN (
                        'product_manager', 'pm_reviewer', 'tester',
                        'test_reviewer', 'builder', 'documenter')),
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                        'pending', 'in_progress', 'done', 'rejected', 'blocked')),
    rejection_notes TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    task_data       TEXT,  -- JSON blob for step-specific input (e.g. brief file path)
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    completed_at    TEXT
);
