"""Stable candidate identity affects provisional comparison, never confirmation."""
from copy import deepcopy
import unittest
from sanzo_demo.comparison import comparison_projection
from test_demo_comparison_projection import quote

class CanonicalProjectionTests(unittest.TestCase):
    def candidate(self, identity, company, fee, **kwargs):
        row=quote(identity,company,**kwargs);row["ai_matched_fee_id"]=fee;return row
    def test_confirmed_and_ai_identity_pair_without_promoting_ai(self):
        a=quote("a","A",fee_id="known");b=self.candidate("b","B","known",name="Canonical",transport_mode=None)
        before=deepcopy([a,b]); rows=comparison_projection([a,b])["rows"]
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]["confirmation_status"],"● AI仮判定")
        self.assertIsNone(b["current_fee_id"]);self.assertEqual([a,b],before)
    def test_same_ai_identity_pairs_but_distinct_ids_same_name_do_not(self):
        a=self.candidate("a","A","one");b=self.candidate("b","B","one")
        self.assertEqual(len(comparison_projection([a,b])["rows"]),1)
        b["ai_matched_fee_id"]="two"
        self.assertEqual(len(comparison_projection([a,b])["rows"]),2)
    def test_identical_candidate_does_not_override_known_condition_conflict(self):
        a=self.candidate("a","A","one",equipment="20FT");b=self.candidate("b","B","one",equipment="40FT")
        self.assertEqual(len(comparison_projection([a,b])["rows"]),2)
    def test_two_different_confirmed_ids_not_merged_by_ai_name(self):
        a=quote("a","A",fee_id="one");b=self.candidate("b","B","two",name=a["current_fee_name"])
        self.assertEqual(len(comparison_projection([a,b])["rows"]),2)
