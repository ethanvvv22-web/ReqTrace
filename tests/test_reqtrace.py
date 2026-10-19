import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from reqtrace import maintain, supplier
from reqtrace.llm import LLM, quote_in
from reqtrace.model import Compliance, Requirement, SupplierAnswer
from reqtrace.select import extract_features, features_by_keyword, select_requirements
from reqtrace.store import ROOT, Store

SEED = json.loads((ROOT / "data" / "requirements.json").read_text(encoding="utf-8"))
LIB = {r["id"]: Requirement.model_validate(r) for r in SEED}


class FakeLLM(LLM):
    """Returns a canned reply; lets us test guardrails without network."""
    def __init__(self, reply):
        super().__init__(use_cache=False)
        self.key, self.reply = "fake", reply

    def json(self, system, user, retries=3):
        return self.reply


def offline_llm():
    llm = LLM(use_cache=False)
    llm.key = ""
    return llm


class StoreTests(unittest.TestCase):
    def test_new_version_keeps_history_and_audits(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / "t.db")
            s.load_seed()
            s.new_version("R021", {"source_ref": "Reg. (EU) 2023/1230, Annex III §1.5.1"},
                          "tester", "outdated ref")
            hist = s.history("R021")
            self.assertEqual([h.version for h in hist], [1, 2])
            self.assertIn("2006/42/EC", hist[0].source_ref)
            self.assertEqual(s.get("R021").version, 2)
            self.assertTrue(any(a.action == "new_version" for a in s.audit("req:R021")))
            s.db.close()

    def test_retired_hidden_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / "t.db")
            s.load_seed()
            self.assertNotIn("R015", {r.id for r in s.current()})
            self.assertIn("R015", {r.id for r in s.current(include_retired=True)})
            s.db.close()


class SelectionTests(unittest.TestCase):
    def test_keyword_features_carry_evidence(self):
        hits = features_by_keyword("Hydraulic power unit with a 400 V motor")
        self.assertEqual({h.feature for h in hits}, {"hydraulic", "electrical"})
        self.assertTrue(all(h.evidence for h in hits))

    def test_selection_is_traceable(self):
        sel = select_requirements({"hydraulic"}, list(LIB.values()))
        by_id = {s.req.id: s for s in sel}
        self.assertEqual(by_id["R060"].because, ["hydraulic"])
        self.assertEqual(by_id["R001"].because, [])          # always applies
        self.assertNotIn("R080", by_id)                       # robot not present

    def test_ai_feature_with_invented_quote_is_dropped(self):
        llm = FakeLLM({"features": [
            {"feature": "noise", "quote": "the rig is very loud", "reason": "x"},
            {"feature": "operator_access", "quote": "technicians swap the gearbox", "reason": "y"},
            {"feature": "teleportation", "quote": "rig", "reason": "z"}]})
        hits, notes = extract_features("Between runs, technicians swap the gearbox.", "ai", llm)
        self.assertEqual({h.feature for h in hits}, {"operator_access"})
        self.assertEqual(len(notes), 2)

    def test_falls_back_to_rules_without_key(self):
        hits, notes = extract_features("hydraulic bench", "hybrid", offline_llm())
        self.assertEqual({h.feature for h in hits}, {"hydraulic"})
        self.assertTrue(any("unavailable" in n for n in notes))


class SupplierTests(unittest.TestCase):
    def a(self, rid, text, claimed="Comply", mode="rules", llm=None):
        return supplier.assess(LIB[rid], SupplierAnswer(req_id=rid, answer=text, claimed=claimed),
                               mode, llm)

    def test_numeric_limit(self):
        self.assertEqual(self.a("R076", "approx. 82 dB(A)").status, Compliance.NON_COMPLIANT)
        self.assertEqual(self.a("R076", "78 dB(A) measured").status, Compliance.COMPLIANT)
        self.assertEqual(self.a("R014", "Warranty 24 months").status, Compliance.COMPLIANT)

    def test_markers_and_review(self):
        r = self.a("R024", "IP44 standard; IP54 on request.")
        self.assertEqual(r.status, Compliance.PARTIAL)
        self.assertTrue(r.needs_review)
        self.assertTrue(any("ticked" in f for f in r.flags))
        self.assertEqual(self.a("R050", "To be discussed").status, Compliance.UNCLEAR)

    def test_bare_yes_needs_review(self):
        r = self.a("R002", "Yes.")
        self.assertEqual(r.status, Compliance.COMPLIANT)
        self.assertTrue(r.needs_review)

    def test_ai_quote_guardrail(self):
        llm = FakeLLM({"status": "compliant", "quote": "fully interlocked doors",
                       "reason": "", "confidence": 0.9})
        r = self.a("R032", "Door has a safety switch.", mode="hybrid", llm=llm)
        self.assertTrue(r.needs_review)
        self.assertTrue(any("not found verbatim" in f for f in r.flags))
        self.assertEqual(r.layer, "rule")                     # AI result discarded

    def test_rule_ai_disagreement_flagged(self):
        llm = FakeLLM({"status": "non_compliant", "quote": "restarts automatically",
                       "reason": "", "confidence": 0.9})
        r = self.a("R026", "The drive restarts automatically when power returns.",
                   mode="hybrid", llm=llm)
        self.assertEqual(r.status, Compliance.NON_COMPLIANT)
        self.assertEqual(r.layer, "rule+ai")

    def test_quote_in_normalises_whitespace_and_case(self):
        self.assertTrue(quote_in("Restarts  AUTOMATICALLY.", "it restarts automatically when"))
        self.assertFalse(quote_in("", "anything"))


class MaintenanceTests(unittest.TestCase):
    def test_finds_conflict_outdated_and_vague(self):
        found = maintain.check_library([r for r in LIB.values() if r.status.value != "retired"],
                                       today=date(2026, 10, 18))
        kinds = {(f.kind, tuple(f.req_ids)) for f in found}
        self.assertIn(("conflict", ("R076", "R077")), kinds)
        self.assertIn(("outdated_ref", ("R021",)), kinds)
        self.assertIn(("vague", ("R045",)), kinds)
        out = next(f for f in found if f.kind == "outdated_ref" and f.req_ids == ["R021"])
        self.assertEqual(out.proposal["source_ref"], "Reg. (EU) 2023/1230, Annex III §1.5.1")


class AppSmokeTests(unittest.TestCase):
    def test_every_page_renders(self):
        try:
            from streamlit.testing.v1 import AppTest
        except ImportError:
            self.skipTest("streamlit not installed")
        for page in ["Library", "Maintenance", "Case requirements", "Supplier response",
                     "Audit log"]:
            at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
            at.run()
            at.sidebar.radio[1].set_value(page).run()
            self.assertFalse(at.exception, f"{page}: {at.exception}")


if __name__ == "__main__":
    unittest.main()
