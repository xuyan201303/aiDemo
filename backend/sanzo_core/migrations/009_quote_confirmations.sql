-- Current-quotation attestations are separate from immutable reader facts and learning.
-- Extend only the already modelled line conditions; 008 remains unchanged.
ALTER TABLE sanzo_core.condition_correction_events
 DROP CONSTRAINT condition_correction_events_field_name_check;
ALTER TABLE sanzo_core.condition_correction_events
 ADD CONSTRAINT condition_correction_events_field_name_check
 CHECK (field_name IN ('equipment','billing_unit','charge_scope','extra_terms'));
ALTER TABLE sanzo_core.item_condition_overlays
 DROP CONSTRAINT item_condition_overlays_field_name_check;
ALTER TABLE sanzo_core.item_condition_overlays
 ADD CONSTRAINT item_condition_overlays_field_name_check
 CHECK (field_name IN ('equipment','billing_unit','charge_scope','extra_terms'));
ALTER TABLE sanzo_core.condition_correction_events ADD COLUMN evidence_note text
 CHECK (evidence_note IS NULL OR length(btrim(evidence_note)) BETWEEN 1 AND 2000);

CREATE TABLE sanzo_core.quote_confirmation_events (
 tenant_id text NOT NULL, event_id text NOT NULL, case_id text NOT NULL,
 item_id text NOT NULL, document_id text NOT NULL, actor_id text NOT NULL,
 confirmation_kind text NOT NULL CHECK (confirmation_kind IN ('CONDITION_VALUE','SOURCE_REVIEW')),
 field_name text,
 value_snapshot jsonb NOT NULL,
 anchor_sha256 text NOT NULL CHECK (anchor_sha256 ~ '^[0-9a-f]{64}$'),
 document_sha256 text NOT NULL CHECK (document_sha256 ~ '^[0-9a-f]{64}$'),
 evidence_note text NOT NULL CHECK (length(btrim(evidence_note)) BETWEEN 1 AND 2000),
 item_version bigint NOT NULL CHECK (item_version >= 1),
 case_revision bigint NOT NULL CHECK (case_revision >= 1),
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id,event_id),
 FOREIGN KEY (tenant_id,case_id,item_id)
 REFERENCES sanzo_core.items (tenant_id,case_id,item_id),
 FOREIGN KEY (tenant_id,document_id) REFERENCES sanzo_core.documents (tenant_id,document_id),
 CHECK ((confirmation_kind='CONDITION_VALUE' AND field_name IN
   ('equipment','billing_unit','charge_scope','extra_terms'))
   OR (confirmation_kind='SOURCE_REVIEW' AND field_name IS NULL))
);
CREATE INDEX quote_confirmation_item_history
 ON sanzo_core.quote_confirmation_events (tenant_id,item_id,item_version,event_id);
CREATE TRIGGER immutable_quote_confirmation_events
 BEFORE UPDATE OR DELETE ON sanzo_core.quote_confirmation_events
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();

CREATE TABLE sanzo_core.document_review_events (
 tenant_id text NOT NULL, event_id text NOT NULL, case_id text NOT NULL,
 document_id text NOT NULL, actor_id text NOT NULL,
 document_sha256 text NOT NULL CHECK (document_sha256 ~ '^[0-9a-f]{64}$'),
 review_sha256 text NOT NULL CHECK (review_sha256 ~ '^[0-9a-f]{64}$'),
 value_snapshot jsonb NOT NULL,
 confirmed_item_count bigint NOT NULL CHECK (confirmed_item_count > 0),
 evidence_note text NOT NULL CHECK (length(btrim(evidence_note)) BETWEEN 1 AND 2000),
 case_revision bigint NOT NULL CHECK (case_revision >= 1),
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id,event_id),
 FOREIGN KEY (tenant_id,case_id,document_id)
 REFERENCES sanzo_core.documents (tenant_id,case_id,document_id)
);
CREATE INDEX document_review_history
 ON sanzo_core.document_review_events (tenant_id,document_id,case_revision,event_id);
CREATE TRIGGER immutable_document_review_events
 BEFORE UPDATE OR DELETE ON sanzo_core.document_review_events
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();

-- Document operations have their own ledger; an item is never invented as an anchor.
CREATE TABLE sanzo_core.document_review_commands (
 tenant_id text NOT NULL, idempotency_key text NOT NULL
 CHECK (length(idempotency_key) BETWEEN 1 AND 200),
 actor_id text NOT NULL, case_id text NOT NULL, document_id text NOT NULL,
 request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
 response_json jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id,idempotency_key),
 FOREIGN KEY (tenant_id,case_id,document_id)
 REFERENCES sanzo_core.documents (tenant_id,case_id,document_id)
);
CREATE TRIGGER immutable_document_review_commands
 BEFORE UPDATE OR DELETE ON sanzo_core.document_review_commands
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
