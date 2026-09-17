"""Optional contract checks: uses jsonschema when installed, never installs packages."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        print('BLOCKED: jsonschema is not installed; unit tests still run without it.')
        return 2
    tests = [
        ('reference_correction.schema.json','correction_first.synthetic.json'),
        ('reference_decision.schema.json','decision_reuse.synthetic.json'),
        ('reference_decision.schema.json','decision_unknown.synthetic.json'),
    ]
    for schema_name, example_name in tests:
        schema=json.loads((ROOT/'contracts'/schema_name).read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(json.loads((ROOT/'contracts'/example_name).read_text()))
        print('PASS',schema_name,example_name)
    # Negative cases must not accidentally validate.
    schema=json.loads((ROOT/'contracts/reference_correction.schema.json').read_text())
    v=Draft202012Validator(schema)
    base={'item_id':'x','expected_item_version':1,'expected_memory_version':0,'idempotency_key':'x'}
    bad=[base,{**base,'new_fee_name':'x','target_fee_id':'y'},
         {**base,'unit_price':'1,000'},{**base,'unit_price':'20','replace_existing_mapping':True},
         {**base,'unit_price':'20','tenant_id':'attacker'}]
    for idx,data in enumerate(bad,1):
        if not list(v.iter_errors(data)):
            raise AssertionError(f'Negative case {idx} accepted')
        print('PASS rejected negative correction',idx)
    return 0

if __name__ == '__main__':
    sys.exit(main())
