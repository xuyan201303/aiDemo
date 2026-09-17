"""Explicit allowance amendment, real PostgreSQL only; no HTTP/AI calls."""
import os
from pathlib import Path
import unittest
import psycopg
from psycopg.types.json import Jsonb
from sanzo_core.postgres import PostgresRepository

@unittest.skipUnless(os.environ.get('SANZO_TEST_DSN'), 'NOT_RUN: dedicated PostgreSQL required')
class ApprovedBudgetAmendmentTests(unittest.TestCase):
    def test_consumed_count_and_grant_preserved_and_normal_guard_restored(self):
        repo=PostgresRepository(os.environ['SANZO_TEST_DSN']);repo.migrate()
        sql=(Path(__file__).resolve().parents[1]/'backend/sanzo_core/migrations/006_authorized_demo_limit_100.sql').read_text()
        # Every fixture change rolls back; the production authorization ID is
        # used only inside this disposable test DB, never in the running Demo.
        with repo.transaction() as c:
            c.execute('SAVEPOINT test_fixture')
            try:
                c.execute("INSERT INTO sanzo_core.ai_authorizations(authorization_id,provider,model,authorized_sample_sha256,max_requests,requests_used) VALUES('sanzo-three-file-mock-sdk-v1','openai','gpt-5.6-terra',%s,30,23)",(Jsonb(['a'*64]),))
                before=c.execute("SELECT * FROM sanzo_core.ai_authorizations WHERE authorization_id='sanzo-three-file-mock-sdk-v1'").fetchone()
                c.execute(sql,prepare=False)
                after=c.execute("SELECT * FROM sanzo_core.ai_authorizations WHERE authorization_id='sanzo-three-file-mock-sdk-v1'").fetchone()
                self.assertEqual(after,{**before,'max_requests':100})
                c.execute(sql,prepare=False)
                self.assertEqual(c.execute("SELECT requests_used FROM sanzo_core.ai_authorizations WHERE authorization_id='sanzo-three-file-mock-sdk-v1'").fetchone()['requests_used'],23)
                for assignment in ['max_requests=101','requests_used=0',"model='other'"]:
                    with self.assertRaises(psycopg.errors.RaiseException):
                        with c.transaction():
                            c.execute("UPDATE sanzo_core.ai_authorizations SET "+assignment+" WHERE authorization_id='sanzo-three-file-mock-sdk-v1'")
                self.assertEqual(c.execute("SELECT tgenabled FROM pg_trigger WHERE tgname='immutable_ai_authorization'").fetchone()['tgenabled'],'O')
            finally:c.execute('ROLLBACK TO SAVEPOINT test_fixture')
