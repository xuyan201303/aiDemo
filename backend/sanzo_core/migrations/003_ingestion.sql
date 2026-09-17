-- Application-owned ingestion state; original documents and W1 history stay immutable.
ALTER TABLE sanzo_core.items DROP CONSTRAINT items_input_kind_check;
ALTER TABLE sanzo_core.items ADD CONSTRAINT items_input_kind_check
 CHECK (input_kind IN ('SYNTHETIC_STRUCTURED', 'MANUAL_CONFIRMED', 'SDK_OBSERVED'));

CREATE TABLE sanzo_core.ingestion_tasks (
 tenant_id text NOT NULL, task_id text NOT NULL, case_id text NOT NULL, document_id text NOT NULL,
 document_sha256 text NOT NULL CHECK (document_sha256 ~ '^[0-9a-f]{64}$'),
 actor_id text NOT NULL,
 state text NOT NULL CHECK (state IN
  ('QUEUED','READING','EXTRACTING','SAVING','AVAILABLE','REVIEW_REQUIRED','FAILED','BLOCKED','INTERRUPTED')),
 version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
 error_code text, result_json jsonb,
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 updated_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id, task_id),
 UNIQUE (tenant_id, case_id, task_id),
 FOREIGN KEY (tenant_id, case_id, document_id)
  REFERENCES sanzo_core.documents (tenant_id, case_id, document_id),
 CHECK ((state IN ('AVAILABLE','REVIEW_REQUIRED')) = (result_json IS NOT NULL))
);
CREATE INDEX ingestion_tasks_document_lookup
 ON sanzo_core.ingestion_tasks (tenant_id, document_id, created_at DESC, task_id);
CREATE UNIQUE INDEX ingestion_one_active_document
 ON sanzo_core.ingestion_tasks (tenant_id, document_id)
 WHERE state IN ('QUEUED','READING','EXTRACTING','SAVING');
CREATE UNIQUE INDEX ingestion_one_committed_document
 ON sanzo_core.ingestion_tasks (tenant_id, document_id)
 WHERE result_json IS NOT NULL;

CREATE TABLE sanzo_core.ingestion_commands (
 tenant_id text NOT NULL, idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
 request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
 task_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id,idempotency_key),
 FOREIGN KEY (tenant_id,task_id) REFERENCES sanzo_core.ingestion_tasks (tenant_id,task_id)
);
CREATE TABLE sanzo_core.ingestion_events (
 tenant_id text NOT NULL, task_id text NOT NULL, version bigint NOT NULL,
 state text NOT NULL CHECK (state IN
  ('QUEUED','READING','EXTRACTING','SAVING','AVAILABLE','REVIEW_REQUIRED','FAILED','BLOCKED','INTERRUPTED')),
 error_code text, metadata jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id,task_id,version),
 FOREIGN KEY (tenant_id,task_id) REFERENCES sanzo_core.ingestion_tasks (tenant_id,task_id)
);
CREATE TABLE sanzo_core.ingestion_sources (
 tenant_id text NOT NULL, task_id text NOT NULL, source_id text NOT NULL,
 raw_text text NOT NULL, source_ref jsonb NOT NULL,
 PRIMARY KEY (tenant_id,task_id,source_id),
 FOREIGN KEY (tenant_id,task_id) REFERENCES sanzo_core.ingestion_tasks (tenant_id,task_id)
);
CREATE FUNCTION sanzo_core.protect_ingestion_identity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.tenant_id,NEW.task_id,NEW.case_id,NEW.document_id,NEW.document_sha256,NEW.actor_id,NEW.created_at)
 IS DISTINCT FROM
    ROW(OLD.tenant_id,OLD.task_id,OLD.case_id,OLD.document_id,OLD.document_sha256,OLD.actor_id,OLD.created_at)
 OR OLD.state IN ('AVAILABLE','REVIEW_REQUIRED','FAILED','BLOCKED','INTERRUPTED')
 OR NEW.version <> OLD.version + 1
 THEN RAISE EXCEPTION 'Ingestion identity or completed attempt is immutable'; END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER immutable_ingestion_identity BEFORE UPDATE ON sanzo_core.ingestion_tasks
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_ingestion_identity();
CREATE TRIGGER immutable_ingestion_task_delete BEFORE DELETE ON sanzo_core.ingestion_tasks
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
CREATE TRIGGER immutable_ingestion_commands BEFORE UPDATE OR DELETE ON sanzo_core.ingestion_commands
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
CREATE TRIGGER immutable_ingestion_events BEFORE UPDATE OR DELETE ON sanzo_core.ingestion_events
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
CREATE TRIGGER immutable_ingestion_sources BEFORE UPDATE OR DELETE ON sanzo_core.ingestion_sources
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
