-- Stable fee identity, audited names, and adopted item display snapshots.
ALTER TABLE sanzo_core.fees ADD COLUMN name_version bigint NOT NULL DEFAULT 1 CHECK(name_version>0);
ALTER TABLE sanzo_core.fees ADD COLUMN retired boolean NOT NULL DEFAULT false;
CREATE TABLE sanzo_core.fee_name_versions (
 tenant_id text NOT NULL, fee_id text NOT NULL, name_version bigint NOT NULL CHECK(name_version>0),
 display_name text NOT NULL, normalized_name text NOT NULL, event_id text NOT NULL,
 actor_id text, case_id text, reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY(tenant_id,fee_id,name_version), UNIQUE(tenant_id,event_id),
 FOREIGN KEY(tenant_id,fee_id) REFERENCES sanzo_core.fees(tenant_id,fee_id)
);
INSERT INTO sanzo_core.fee_name_versions(tenant_id,fee_id,name_version,display_name,normalized_name,event_id,reason)
 SELECT tenant_id,fee_id,1,display_name,normalized_name,'baseline:'||fee_id,'BASELINE_MIGRATION' FROM sanzo_core.fees;
ALTER TABLE sanzo_core.items ADD COLUMN current_fee_name_snapshot text;
ALTER TABLE sanzo_core.items ADD COLUMN current_fee_name_version bigint;
UPDATE sanzo_core.items i SET current_fee_name_snapshot=f.display_name,current_fee_name_version=f.name_version
 FROM sanzo_core.fees f WHERE f.tenant_id=i.tenant_id AND f.fee_id=i.current_fee_id;
ALTER TABLE sanzo_core.items ADD CONSTRAINT adopted_fee_name_fk FOREIGN KEY(tenant_id,current_fee_id,current_fee_name_version)
 REFERENCES sanzo_core.fee_name_versions(tenant_id,fee_id,name_version);
ALTER TABLE sanzo_core.items ADD CONSTRAINT adopted_fee_name_present CHECK(
 (current_fee_id IS NULL AND current_fee_name_snapshot IS NULL AND current_fee_name_version IS NULL) OR
 (current_fee_id IS NOT NULL AND current_fee_name_snapshot IS NOT NULL AND current_fee_name_version IS NOT NULL));
CREATE FUNCTION sanzo_core.initial_fee_name() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO sanzo_core.fee_name_versions(tenant_id,fee_id,name_version,display_name,normalized_name,event_id,reason)
 VALUES(NEW.tenant_id,NEW.fee_id,NEW.name_version,NEW.display_name,NEW.normalized_name,'initial:'||NEW.fee_id,'INITIAL_CONFIRMATION');
 RETURN NEW;
END; $$;
CREATE TRIGGER initial_fee_name AFTER INSERT ON sanzo_core.fees FOR EACH ROW EXECUTE FUNCTION sanzo_core.initial_fee_name();
CREATE FUNCTION sanzo_core.adopt_fee_name() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.current_fee_id IS NULL THEN
  NEW.current_fee_name_snapshot=NULL; NEW.current_fee_name_version=NULL;
 ELSIF TG_OP='INSERT' OR NEW.current_fee_id IS DISTINCT FROM OLD.current_fee_id OR NEW.current_fee_name_snapshot IS NULL THEN
  SELECT display_name,name_version INTO NEW.current_fee_name_snapshot,NEW.current_fee_name_version
   FROM sanzo_core.fees WHERE tenant_id=NEW.tenant_id AND fee_id=NEW.current_fee_id;
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER adopt_fee_name BEFORE INSERT OR UPDATE OF current_fee_id ON sanzo_core.items FOR EACH ROW EXECUTE FUNCTION sanzo_core.adopt_fee_name();
CREATE TABLE sanzo_core.fee_identity_repair_events (
 tenant_id text NOT NULL, repair_id text NOT NULL, request_hash text NOT NULL,
 original_fee_id text NOT NULL, branch_fee_id text NOT NULL, case_id text NOT NULL,
 actor_id text NOT NULL, evidence jsonb NOT NULL, result jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT current_timestamp,
 PRIMARY KEY(tenant_id,repair_id),
 FOREIGN KEY(tenant_id,original_fee_id) REFERENCES sanzo_core.fees(tenant_id,fee_id),
 FOREIGN KEY(tenant_id,branch_fee_id) REFERENCES sanzo_core.fees(tenant_id,fee_id)
);
CREATE TRIGGER immutable_fee_name_versions BEFORE UPDATE OR DELETE ON sanzo_core.fee_name_versions FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
CREATE TRIGGER immutable_fee_identity_repairs BEFORE UPDATE OR DELETE ON sanzo_core.fee_identity_repair_events FOR EACH ROW EXECUTE FUNCTION sanzo_core.protect_history();
