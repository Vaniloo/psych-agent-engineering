CREATE TABLE agent_run_metrics (
    actual_cost_usd DOUBLE NOT NULL,
    estimated_cost_usd DOUBLE NOT NULL,
    fallback_count integer NOT NULL,
    mcp_duration_ms DOUBLE NOT NULL,
    tokens_per_second DOUBLE NOT NULL,
    total_duration_ms DOUBLE NOT NULL,
    ttft_ms DOUBLE,
    completion_tokens bigint NOT NULL,
    created_at datetime(6) NOT NULL,
    id bigint NOT NULL AUTO_INCREMENT,
    prompt_tokens bigint NOT NULL,
    session_id bigint NOT NULL,
    started_at datetime(6) NOT NULL,
    total_tokens bigint NOT NULL,
    cost_mode varchar(24) NOT NULL,
    persistence_status varchar(24) NOT NULL,
    status varchar(24) NOT NULL,
    request_id varchar(64) NOT NULL,
    username varchar(64) NOT NULL,
    model varchar(80) NOT NULL,
    capability_calls_json LONGTEXT,
    model_calls_json LONGTEXT,
    node_durations_json LONGTEXT,
    pipeline_json LONGTEXT,
    retrieval_json LONGTEXT,
    PRIMARY KEY (id)
);

CREATE TABLE agent_turn_receipts (
    created_at datetime(6) NOT NULL,
    id bigint NOT NULL AUTO_INCREMENT,
    session_id bigint NOT NULL,
    user_id bigint NOT NULL,
    fingerprint varchar(64) NOT NULL,
    request_id varchar(128) NOT NULL,
    assistant_reply MEDIUMTEXT NOT NULL,
    memory_status varchar(255) NOT NULL,
    user_message TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE conversation_memories (
    id bigint NOT NULL AUTO_INCREMENT,
    updated_at datetime(6) NOT NULL,
    user_id bigint NOT NULL,
    long_term_memory TEXT,
    summary TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE conversation_messages (
    created_at datetime(6) NOT NULL,
    id bigint NOT NULL AUTO_INCREMENT,
    session_id bigint,
    user_id bigint NOT NULL,
    role varchar(32) NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE conversation_sessions (
    created_at datetime(6) NOT NULL,
    id bigint NOT NULL AUTO_INCREMENT,
    updated_at datetime(6) NOT NULL,
    user_id bigint NOT NULL,
    title varchar(255) NOT NULL,
    summary TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE knowledge_document (
    created_at datetime(6) NOT NULL,
    category varchar(255) NOT NULL,
    content TEXT NOT NULL,
    id varchar(255) NOT NULL,
    metadata_json TEXT,
    source varchar(255) NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE rag_evaluation_metrics (
    avg_latency_ms DOUBLE NOT NULL,
    cache_hit_rate DOUBLE NOT NULL,
    hit_rate_at5 DOUBLE NOT NULL,
    mrr DOUBLE NOT NULL,
    n_dcg_at5 DOUBLE NOT NULL,
    p95latency_ms DOUBLE NOT NULL,
    precision_at5 DOUBLE NOT NULL,
    recall_at1 DOUBLE NOT NULL,
    recall_at3 DOUBLE NOT NULL,
    recall_at5 DOUBLE NOT NULL,
    sample_count integer NOT NULL,
    created_at datetime(6) NOT NULL,
    evaluated_at datetime(6) NOT NULL,
    id bigint NOT NULL AUTO_INCREMENT,
    dataset_version varchar(80) NOT NULL,
    cases_json LONGTEXT,
    PRIMARY KEY (id)
);

CREATE TABLE risk_alert_events (
    id bigint NOT NULL AUTO_INCREMENT,
    created_at datetime(6) NOT NULL,
    username varchar(255) NOT NULL,
    risk varchar(255) NOT NULL,
    status varchar(255) NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE user_profiles (
    id bigint NOT NULL AUTO_INCREMENT,
    updated_at datetime(6) NOT NULL,
    user_id bigint NOT NULL,
    concerns TEXT,
    coping_strategies TEXT,
    preferences TEXT,
    profile_summary TEXT,
    risk_signals TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE users (
    id bigint NOT NULL AUTO_INCREMENT,
    password varchar(255) NOT NULL,
    role varchar(255) NOT NULL,
    username varchar(255) NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX idx_agent_metric_user_started on agent_run_metrics (username, started_at);

CREATE INDEX idx_agent_metric_session on agent_run_metrics (session_id);

ALTER TABLE agent_run_metrics ADD constraint uk_agent_metric_request UNIQUE (request_id);

ALTER TABLE agent_turn_receipts ADD UNIQUE (user_id, request_id);

ALTER TABLE conversation_memories ADD UNIQUE (user_id);

CREATE INDEX idx_message_user_session_role_created on conversation_messages (user_id, session_id, role, created_at);

CREATE INDEX idx_session_user_updated on conversation_sessions (user_id, updated_at);

CREATE INDEX idx_risk_alert_user_created on risk_alert_events (username, created_at);

ALTER TABLE user_profiles ADD UNIQUE (user_id);

ALTER TABLE users ADD UNIQUE (username);

ALTER TABLE agent_turn_receipts ADD FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE conversation_memories ADD FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE conversation_messages ADD FOREIGN KEY (session_id) REFERENCES conversation_sessions (id);

ALTER TABLE conversation_messages ADD FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE conversation_sessions ADD FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE user_profiles ADD FOREIGN KEY (user_id) REFERENCES users (id);

