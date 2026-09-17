-- Project-local, cumulative authorization budget. No credential or prompt storage.
CREATE TABLE sanzo_core.ai_authorizations (
 authorization_id text PRIMARY KEY CHECK (authorization_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$'),
 provider text NOT NULL CHECK (provider='openai'),
 model text NOT NULL,
 authorized_sample_sha256 jsonb NOT NULL CHECK (jsonb_typeof(authorized_sample_sha256)='array'),
 max_requests bigint NOT NULL CHECK (max_requests BETWEEN 0 AND 2147483647),
 requests_used bigint NOT NULL DEFAULT 0 CHECK (requests_used BETWEEN 0 AND max_requests),
 created_at timestamptz NOT NULL DEFAULT current_timestamp
);
CREATE TABLE sanzo_core.ai_request_attempts (
 authorization_id text NOT NULL REFERENCES sanzo_core.ai_authorizations(authorization_id),
 attempt_number bigint NOT NULL CHECK (attempt_number >= 1),
 attempt_id text NOT NULL UNIQUE CHECK (attempt_id ~ '^[0-9a-f]{32}$'),
 document_sha256 text NOT NULL CHECK (document_sha256 ~ '^[0-9a-f]{64}$'),
 purpose text NOT NULL CHECK (purpose IN ('quotation_extraction','fee_classification')),
 state text NOT NULL DEFAULT 'ALLOWANCE_CONSUMED' CHECK (state='ALLOWANCE_CONSUMED'),
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY (authorization_id,attempt_number)
);
COMMENT ON TABLE sanzo_core.ai_request_attempts IS
 'Committed before credential lookup/HTTP. An allowance may have no outbound call after crash or missing key; never refunded. Actual HTTP outcome remains in ingestion events.';

CREATE FUNCTION sanzo_core.protect_ai_authorization() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.authorization_id,NEW.provider,NEW.model,NEW.authorized_sample_sha256,
        NEW.max_requests,NEW.created_at)
 IS DISTINCT FROM
    ROW(OLD.authorization_id,OLD.provider,OLD.model,OLD.authorized_sample_sha256,
        OLD.max_requests,OLD.created_at)
 OR NEW.requests_used <> OLD.requests_used + 1
 THEN RAISE EXCEPTION 'AI authorization is immutable; allowance only advances by one'; END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER immutable_ai_authorization BEFORE UPDATE ON sanzo_core.ai_authorizations
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_ai_authorization();
CREATE TRIGGER immutable_ai_authorization_delete BEFORE DELETE ON sanzo_core.ai_authorizations
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
CREATE TRIGGER immutable_ai_request_attempts BEFORE UPDATE OR DELETE ON sanzo_core.ai_request_attempts
 FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
