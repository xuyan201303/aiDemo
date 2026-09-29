-- Human applicability corrections are an overlay. SDK/observation evidence stays immutable.
CREATE TABLE sanzo_core.condition_correction_events (
 tenant_id text NOT NULL, event_id text NOT NULL, case_id text NOT NULL,
 item_id text NOT NULL, actor_id text NOT NULL,
 field_name text NOT NULL CHECK (field_name IN ('equipment','billing_unit')),
 original_value text, before_effective_value text, human_confirmed_value text,
 original_memory_key text,
 item_version bigint NOT NULL CHECK (item_version >= 1),
 case_revision bigint NOT NULL CHECK (case_revision >= 1),
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id,event_id),
 FOREIGN KEY (tenant_id,case_id,item_id)
 REFERENCES sanzo_core.items (tenant_id,case_id,item_id)
);
CREATE INDEX condition_correction_item_history
 ON sanzo_core.condition_correction_events (tenant_id,item_id,created_at,event_id);
CREATE INDEX condition_correction_memory_guard
 ON sanzo_core.condition_correction_events (tenant_id,original_memory_key)
 WHERE original_memory_key IS NOT NULL
   AND original_value IS DISTINCT FROM human_confirmed_value;
CREATE TRIGGER immutable_condition_correction_events
 BEFORE UPDATE OR DELETE ON sanzo_core.condition_correction_events
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();

CREATE TABLE sanzo_core.item_condition_overlays (
 tenant_id text NOT NULL, item_id text NOT NULL,
 field_name text NOT NULL CHECK (field_name IN ('equipment','billing_unit')),
 original_value text, human_confirmed_value text, event_id text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id,item_id,field_name),
 FOREIGN KEY (tenant_id,item_id) REFERENCES sanzo_core.items (tenant_id,item_id),
 FOREIGN KEY (tenant_id,event_id)
 REFERENCES sanzo_core.condition_correction_events (tenant_id,event_id)
);
