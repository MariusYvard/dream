-- Dream / PGT 2.0 SQLite schema
-- All vector storage lives in LanceDB. SQLite holds metadata, vitality, ledger, config.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('fact','decision','code_snippet','person','process','error')),
    content TEXT NOT NULL,
    embedding_ref TEXT NOT NULL,
    validity_from TEXT NOT NULL,
    validity_to TEXT,
    confidence REAL NOT NULL DEFAULT 0.85,
    vitality REAL NOT NULL DEFAULT 0.9,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed TEXT,
    source_session TEXT,
    project TEXT,
    consensus_score REAL,
    scenario TEXT NOT NULL DEFAULT 'base' CHECK (scenario IN ('base','counterfactual')),
    access_policy TEXT NOT NULL DEFAULT 'read_write' CHECK (access_policy IN ('read_write','read_only')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','pending_hitl','archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_vitality ON nodes(vitality);
CREATE INDEX IF NOT EXISTS idx_nodes_validity ON nodes(validity_from, validity_to);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    to_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('implements','depends_on','contradicts','supersedes','alternative_of','derived_from')),
    weight REAL NOT NULL DEFAULT 0.8,
    temporal_from TEXT NOT NULL,
    temporal_to TEXT,
    evidence TEXT,
    scenario TEXT NOT NULL DEFAULT 'base',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(relation_type);

CREATE TABLE IF NOT EXISTS ledger (
    leaf_id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload_sha256 TEXT NOT NULL,
    parent_leaf INTEGER,
    signature TEXT NOT NULL,
    public_key_fp TEXT NOT NULL,
    operation TEXT NOT NULL,
    node_id TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    merkle_root TEXT NOT NULL,
    last_leaf INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cycle_state (
    cycle_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    phase TEXT NOT NULL,
    metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS hitl_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    debate_trail_path TEXT NOT NULL,
    score_final REAL NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT
);
