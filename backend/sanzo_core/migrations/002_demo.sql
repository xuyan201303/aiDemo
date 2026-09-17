-- Thin local Demo extension. The original W1 history and transactions are retained.
ALTER TABLE sanzo_core.items DROP CONSTRAINT items_input_kind_check;
ALTER TABLE sanzo_core.items ADD CONSTRAINT items_input_kind_check
 CHECK (input_kind IN ('SYNTHETIC_STRUCTURED', 'MANUAL_CONFIRMED'));

CREATE TABLE sanzo_core.documents (
 tenant_id text NOT NULL, document_id text NOT NULL, case_id text NOT NULL,
 original_filename text NOT NULL, media_type text NOT NULL,
 size_bytes bigint NOT NULL CHECK (size_bytes > 0),
 sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
 storage_key text NOT NULL,
 state text NOT NULL DEFAULT 'BLOCKED' CHECK (state = 'BLOCKED'),
 blocked_reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id, document_id),
 UNIQUE (tenant_id, case_id, document_id),
 FOREIGN KEY (tenant_id, case_id)
 REFERENCES sanzo_core.cases (tenant_id, case_id)
);
CREATE INDEX documents_case_lookup ON sanzo_core.documents (tenant_id, case_id);

CREATE TABLE sanzo_core.item_documents (
 tenant_id text NOT NULL, item_id text NOT NULL, case_id text NOT NULL,
 document_id text NOT NULL,
 PRIMARY KEY (tenant_id, item_id),
 FOREIGN KEY (tenant_id, case_id, item_id)
 REFERENCES sanzo_core.items (tenant_id, case_id, item_id),
 FOREIGN KEY (tenant_id, case_id, document_id)
 REFERENCES sanzo_core.documents (tenant_id, case_id, document_id)
);

-- A decision taken once when a new item enters this Demo. Reading the old table
-- uses the saved item, never a fresh classification that overwrites history.
CREATE TABLE sanzo_core.item_classifications (
 tenant_id text NOT NULL, item_id text NOT NULL,
 decision_json jsonb NOT NULL,
 memory_key text, memory_version bigint,
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id, item_id),
 FOREIGN KEY (tenant_id, item_id)
 REFERENCES sanzo_core.items (tenant_id, item_id),
 FOREIGN KEY (tenant_id, memory_key, memory_version)
 REFERENCES sanzo_core.memory_versions (tenant_id, memory_key, version),
 CHECK ((memory_key IS NULL AND memory_version IS NULL) OR
        (memory_key IS NOT NULL AND memory_version >= 1))
);
CREATE TRIGGER immutable_demo_documents BEFORE UPDATE OR DELETE ON sanzo_core.documents
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
CREATE TRIGGER immutable_item_documents BEFORE UPDATE OR DELETE ON sanzo_core.item_documents
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
CREATE TRIGGER immutable_item_classifications BEFORE UPDATE OR DELETE ON sanzo_core.item_classifications
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
