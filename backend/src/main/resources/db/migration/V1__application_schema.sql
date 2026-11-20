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
    created_goal_id bigint,
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

CREATE TABLE goal_check_ins (
    feedback_added bit NOT NULL,
    undone bit NOT NULL,
    applied_goal_version bigint,
    created_at datetime(6) NOT NULL,
    goal_id bigint NOT NULL,
    id bigint NOT NULL AUTO_INCREMENT,
    session_id bigint,
    user_id bigint NOT NULL,
    outcome varchar(20) NOT NULL,
    previous_status varchar(20) NOT NULL,
    source varchar(20) NOT NULL,
    action_id varchar(80) NOT NULL,
    source_request_id varchar(128),
    action_title varchar(300) NOT NULL,
    note varchar(500) NOT NULL,
    coaching_json TEXT,
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

CREATE TABLE psychological_reports (
    confidence DOUBLE NOT NULL,
    created_at datetime(6) NOT NULL,
    id bigint NOT NULL AUTO_INCREMENT,
    user_id bigint,
    emotion varchar(255) NOT NULL,
    message varchar(255) NOT NULL,
    risk varchar(255) NOT NULL,
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
    attempts integer NOT NULL,
    confidence DOUBLE NOT NULL,
    created_at datetime(6) NOT NULL,
    id bigint NOT NULL AUTO_INCREMENT,
    report_id bigint,
    updated_at datetime(6) NOT NULL,
    emotion varchar(255) NOT NULL,
    risk varchar(255) NOT NULL,
    status varchar(255) NOT NULL,
    username varchar(255) NOT NULL,
    last_error TEXT,
    message TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE role_cards (
    experimental bit,
    preset bit NOT NULL,
    created_at datetime(6) NOT NULL,
    id bigint NOT NULL AUTO_INCREMENT,
    owner_id bigint,
    description TEXT,
    name varchar(255) NOT NULL,
    response_style varchar(255) NOT NULL,
    source_card_json MEDIUMTEXT,
    studio_config_json MEDIUMTEXT,
    tone varchar(255) NOT NULL,
    avatar_png MEDIUMBLOB,
    custom_instructions TEXT,
    forbidden_expressions TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE support_goals (
    id bigint NOT NULL AUTO_INCREMENT,
    updated_at datetime(6) NOT NULL,
    user_id bigint NOT NULL,
    version bigint,
    status varchar(20) NOT NULL,
    title varchar(200) NOT NULL,
    completion_criteria varchar(1000),
    description varchar(1000) NOT NULL,
    actions_json MEDIUMTEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE user_model_api (
    user_id bigint NOT NULL,
    model varchar(160) NOT NULL,
    base_url varchar(500) NOT NULL,
    encrypted_key varchar(8192) NOT NULL,
    PRIMARY KEY (user_id)
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
    support_goals TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE users (
    goal_auto_sync boolean DEFAULT true NOT NULL,
    active_role_card_id bigint,
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

CREATE INDEX idx_checkin_user_created on goal_check_ins (user_id, created_at);

ALTER TABLE goal_check_ins ADD UNIQUE (user_id, source_request_id, action_id);

CREATE INDEX idx_reports_user_created_at on psychological_reports (user_id, created_at);

CREATE INDEX idx_reports_risk_created_at on psychological_reports (risk, created_at);

CREATE INDEX idx_risk_alert_status_created on risk_alert_events (status, created_at);

ALTER TABLE user_profiles ADD UNIQUE (user_id);

ALTER TABLE users ADD UNIQUE (username);

ALTER TABLE agent_turn_receipts ADD FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE conversation_memories ADD FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE conversation_messages ADD FOREIGN KEY (session_id) REFERENCES conversation_sessions (id);

ALTER TABLE conversation_messages ADD FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE conversation_sessions ADD FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE goal_check_ins ADD FOREIGN KEY (goal_id) REFERENCES support_goals (id);

ALTER TABLE goal_check_ins ADD FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE psychological_reports ADD FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE role_cards ADD FOREIGN KEY (owner_id) REFERENCES users (id);

ALTER TABLE support_goals ADD FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE user_profiles ADD FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE users ADD FOREIGN KEY (active_role_card_id) REFERENCES role_cards (id);
