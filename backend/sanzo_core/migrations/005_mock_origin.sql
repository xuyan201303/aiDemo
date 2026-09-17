-- Explicit fixture-only learning scope. No old observation/key/history is rewritten.
ALTER TABLE sanzo_core.items DROP CONSTRAINT items_input_kind_check;
ALTER TABLE sanzo_core.items ADD CONSTRAINT items_input_kind_check
 CHECK (input_kind IN ('SYNTHETIC_STRUCTURED','MANUAL_CONFIRMED','SDK_OBSERVED','MOCK_SDK'));
ALTER TABLE sanzo_core.items ADD CONSTRAINT items_mock_source_scope_check CHECK (
 (input_kind = 'MOCK_SDK') = COALESCE(source_ref->>'origin' = 'MOCK_SDK', false)
 AND (input_kind = 'MOCK_SDK') = COALESCE(source_ref->>'extraction_origin' = 'MOCK_SDK', false)
 AND (input_kind <> 'MOCK_SDK' OR (
  COALESCE(length(btrim(source_ref->>'fixture_version')) BETWEEN 1 AND 200, false)
  AND COALESCE(source_ref->>'fixture_sha256' ~ '^[0-9a-f]{64}$', false)
  AND COALESCE(source_ref->>'fixture_integrity' = 'VERIFIED', false)
 ))
);

-- LEGACY is an honest absence of an origin field in historical rows, not REAL_SDK.
ALTER TABLE sanzo_core.correction_events ADD COLUMN origin text NOT NULL DEFAULT 'LEGACY';
ALTER TABLE sanzo_core.memory_versions ADD COLUMN origin text NOT NULL DEFAULT 'LEGACY';
ALTER TABLE sanzo_core.memory_events ADD COLUMN origin text NOT NULL DEFAULT 'LEGACY';
ALTER TABLE sanzo_core.memory_slots ADD COLUMN origin text NOT NULL DEFAULT 'LEGACY';

ALTER TABLE sanzo_core.memory_versions ADD CONSTRAINT memory_versions_mock_scope_check CHECK (
 (origin = 'MOCK_SDK' AND memory_key ~ '^MOCK_SDK:v1:[0-9a-f]{64}$')
 OR (origin <> 'MOCK_SDK' AND memory_key !~ '^MOCK_SDK:')
);
ALTER TABLE sanzo_core.memory_events ADD CONSTRAINT memory_events_mock_scope_check CHECK (
 (origin = 'MOCK_SDK' AND memory_key ~ '^MOCK_SDK:v1:[0-9a-f]{64}$')
 OR (origin <> 'MOCK_SDK' AND memory_key !~ '^MOCK_SDK:')
);
ALTER TABLE sanzo_core.memory_slots ADD CONSTRAINT memory_slots_mock_scope_check CHECK (
 (origin = 'MOCK_SDK' AND memory_key ~ '^MOCK_SDK:v1:[0-9a-f]{64}$')
 OR (origin <> 'MOCK_SDK' AND memory_key !~ '^MOCK_SDK:')
);
