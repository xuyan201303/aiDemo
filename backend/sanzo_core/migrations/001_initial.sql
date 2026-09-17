-- W1 independent business core; input evidence is explicitly synthetic.
CREATE TABLE sanzo_core.cases (
 tenant_id text NOT NULL, case_id text NOT NULL,
 name text NOT NULL CHECK (length(btrim(name)) > 0),
 case_revision bigint NOT NULL DEFAULT 1 CHECK (case_revision >= 1),
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 updated_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id, case_id)
);
CREATE TABLE sanzo_core.fees (
 tenant_id text NOT NULL, fee_id text NOT NULL, display_name text NOT NULL,
 normalized_name text NOT NULL,
 origin text NOT NULL CHECK (origin = 'EXPLICIT_ITEM_CONFIRMATION'),
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id, fee_id)
);
-- Names are not semantic IDs. The service locks/checks names before creation.
CREATE INDEX fees_name_lookup ON sanzo_core.fees (tenant_id, normalized_name);
CREATE TABLE sanzo_core.items (
 tenant_id text NOT NULL, item_id text NOT NULL, case_id text NOT NULL,
 observation jsonb NOT NULL, source_ref jsonb, ai_suggested_name text,
 raw_value text, quotation_date date, valid_until date,
 input_kind text NOT NULL CHECK (input_kind = 'SYNTHETIC_STRUCTURED'),
 currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
 version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
 current_fee_id text, current_price numeric NOT NULL, current_price_text text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 updated_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id, item_id), UNIQUE (tenant_id, case_id, item_id),
 FOREIGN KEY (tenant_id, case_id) REFERENCES sanzo_core.cases (tenant_id, case_id),
 FOREIGN KEY (tenant_id, current_fee_id) REFERENCES sanzo_core.fees (tenant_id, fee_id),
 CHECK (current_price_text ~ '^-?(0|[1-9][0-9]*)(\.[0-9]+)?$'),
 CHECK (current_price = current_price_text::numeric),
 CHECK (observation->>'tenant_id' = tenant_id),
 CHECK (observation->>'item_id' = item_id),
 CHECK (observation->>'case_id' = case_id),
 CHECK (observation->>'currency' = currency)
);
CREATE INDEX items_case_lookup ON sanzo_core.items (tenant_id, case_id);
CREATE TABLE sanzo_core.correction_events (
 tenant_id text NOT NULL, event_id text NOT NULL, case_id text NOT NULL,
 item_id text NOT NULL, actor_id text NOT NULL, item_version bigint NOT NULL,
 case_revision bigint NOT NULL, before_json jsonb NOT NULL,
 after_json jsonb NOT NULL, response_json jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id, event_id), UNIQUE (tenant_id, item_id, item_version),
 FOREIGN KEY (tenant_id, case_id, item_id)
 REFERENCES sanzo_core.items (tenant_id, case_id, item_id)
);
CREATE TABLE sanzo_core.memory_versions (
 tenant_id text NOT NULL, memory_key text NOT NULL,
 version bigint NOT NULL CHECK (version >= 1),
 status text NOT NULL CHECK (status IN ('ACTIVE','CONFLICTED','REVOKED')),
 fee_id text, target_fee_id text, actor_id text NOT NULL, item_id text NOT NULL,
 raw_label text NOT NULL, context_json jsonb NOT NULL, source_ref jsonb,
 reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id, memory_key, version),
 FOREIGN KEY (tenant_id, fee_id) REFERENCES sanzo_core.fees (tenant_id, fee_id),
 FOREIGN KEY (tenant_id, target_fee_id) REFERENCES sanzo_core.fees (tenant_id, fee_id),
 FOREIGN KEY (tenant_id, item_id) REFERENCES sanzo_core.items (tenant_id, item_id),
 CHECK ((status = 'ACTIVE' AND fee_id IS NOT NULL) OR
        (status <> 'ACTIVE' AND fee_id IS NULL))
);
CREATE TABLE sanzo_core.memory_events (
 tenant_id text NOT NULL, event_id text NOT NULL, memory_key text NOT NULL,
 memory_version bigint NOT NULL,
 status text NOT NULL CHECK (status IN ('ACTIVE','CONFLICTED','REVOKED')),
 target_fee_id text, actor_id text NOT NULL, source_event_id text, reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id, event_id), UNIQUE (tenant_id, memory_key, memory_version),
 FOREIGN KEY (tenant_id, memory_key, memory_version)
 REFERENCES sanzo_core.memory_versions (tenant_id, memory_key, version),
 FOREIGN KEY (tenant_id, source_event_id)
 REFERENCES sanzo_core.correction_events (tenant_id, event_id),
 FOREIGN KEY (tenant_id, target_fee_id) REFERENCES sanzo_core.fees (tenant_id, fee_id)
);
CREATE TABLE sanzo_core.memory_slots (
 tenant_id text NOT NULL, memory_key text NOT NULL,
 version bigint NOT NULL CHECK (version >= 1),
 status text NOT NULL CHECK (status IN ('ACTIVE','CONFLICTED','REVOKED')),
 fee_id text, evidence_event_id text NOT NULL,
 PRIMARY KEY (tenant_id, memory_key),
 FOREIGN KEY (tenant_id, memory_key, version)
 REFERENCES sanzo_core.memory_versions (tenant_id, memory_key, version),
 FOREIGN KEY (tenant_id, fee_id) REFERENCES sanzo_core.fees (tenant_id, fee_id),
 FOREIGN KEY (tenant_id, evidence_event_id)
 REFERENCES sanzo_core.memory_events (tenant_id, event_id),
 CHECK ((status = 'ACTIVE' AND fee_id IS NOT NULL) OR
        (status <> 'ACTIVE' AND fee_id IS NULL))
);
CREATE TABLE sanzo_core.commands (
 tenant_id text NOT NULL,
 idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
 operation text NOT NULL CHECK (operation IN ('CORRECT','REVOKE')),
 actor_id text NOT NULL, case_id text NOT NULL, item_id text NOT NULL,
 request_hash text NOT NULL CHECK (length(request_hash) = 64),
 response_json jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (tenant_id, idempotency_key),
 FOREIGN KEY (tenant_id, case_id, item_id)
 REFERENCES sanzo_core.items (tenant_id, case_id, item_id)
);
CREATE FUNCTION sanzo_core.protect_observation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.tenant_id,NEW.item_id,NEW.case_id,NEW.observation,NEW.source_ref,
        NEW.ai_suggested_name,NEW.raw_value,NEW.quotation_date,NEW.valid_until,
        NEW.input_kind,NEW.currency)
 IS DISTINCT FROM
    ROW(OLD.tenant_id,OLD.item_id,OLD.case_id,OLD.observation,OLD.source_ref,
        OLD.ai_suggested_name,OLD.raw_value,OLD.quotation_date,OLD.valid_until,
        OLD.input_kind,OLD.currency)
 THEN RAISE EXCEPTION 'Original observation is immutable'; END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER immutable_observation BEFORE UPDATE ON sanzo_core.items
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_observation();
CREATE FUNCTION sanzo_core.protect_history() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Business history is append-only';
END;
$$;
CREATE TRIGGER immutable_memory_versions BEFORE UPDATE OR DELETE ON sanzo_core.memory_versions
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
CREATE TRIGGER immutable_memory_events BEFORE UPDATE OR DELETE ON sanzo_core.memory_events
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
CREATE TRIGGER immutable_correction_events BEFORE UPDATE OR DELETE ON sanzo_core.correction_events
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
CREATE TRIGGER immutable_commands BEFORE UPDATE OR DELETE ON sanzo_core.commands
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
