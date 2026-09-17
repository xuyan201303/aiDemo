-- Explicit user authorization on 2026-09-11: cumulative maximum 30 -> 100.
-- Same grant/model/samples and all consumed attempts are retained. This is a
-- one-time data migration, not an application endpoint or startup auto-increase.
-- PostgresRepository.migrate executes this and its migration ledger atomically.
LOCK TABLE sanzo_core.ai_authorizations IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE grant_row sanzo_core.ai_authorizations%ROWTYPE;
BEGIN
 SELECT * INTO grant_row FROM sanzo_core.ai_authorizations
 WHERE authorization_id='sanzo-three-file-mock-sdk-v1';
 IF FOUND THEN
  IF grant_row.provider <> 'openai' OR grant_row.model <> 'gpt-5.6-terra'
     OR grant_row.max_requests NOT IN (30,100) THEN
   RAISE EXCEPTION 'Approved Demo budget amendment does not match existing grant';
  END IF;
  IF grant_row.max_requests=30 THEN
   -- Exclusive lock and one enclosing transaction prevent any concurrent writer
   -- from observing this maintenance-only trigger suspension. Rollback restores
   -- both the old grant and trigger state if any subsequent operation fails.
   ALTER TABLE sanzo_core.ai_authorizations DISABLE TRIGGER immutable_ai_authorization;
   UPDATE sanzo_core.ai_authorizations SET max_requests=100
    WHERE authorization_id=grant_row.authorization_id;
   ALTER TABLE sanzo_core.ai_authorizations ENABLE TRIGGER immutable_ai_authorization;
  END IF;
 END IF;
END;
$$;
