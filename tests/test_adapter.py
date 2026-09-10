import os
import tempfile
import unittest
from vt_runtime.adapter import (
    ReviewSystemAdapter, ExternalUnavailable,
    RECONCILE_YES, RECONCILE_NO, RECONCILE_UNKNOWN,
)


class TestReviewSystemAdapter(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "review.db")

    def test_execute_creates_and_reconcile_finds(self):
        a = ReviewSystemAdapter(self.path)
        tid = a.execute({"operation": "create_task"}, "k1")
        self.assertTrue(tid.startswith("T-"))
        outcome, found = a.reconcile("k1")
        self.assertEqual(outcome, RECONCILE_YES)
        self.assertEqual(found, tid)

    def test_reconcile_no_when_absent(self):
        a = ReviewSystemAdapter(self.path)
        outcome, found = a.reconcile("missing")
        self.assertEqual(outcome, RECONCILE_NO)
        self.assertIsNone(found)

    def test_execute_is_idempotent_at_target(self):
        a = ReviewSystemAdapter(self.path)
        t1 = a.execute({"operation": "create_task"}, "k1")
        t2 = a.execute({"operation": "create_task"}, "k1")
        self.assertEqual(t1, t2)

    def test_down_execute_raises(self):
        a = ReviewSystemAdapter(self.path, down=True)
        with self.assertRaises(ExternalUnavailable):
            a.execute({"operation": "create_task"}, "k1")

    def test_down_reconcile_cannot_answer(self):
        # create a task while up, then reopen "down": reconcile must not lie
        ReviewSystemAdapter(self.path).execute({"operation": "create_task"}, "k1")
        a = ReviewSystemAdapter(self.path, down=True)
        outcome, found = a.reconcile("k1")
        self.assertEqual(outcome, RECONCILE_UNKNOWN)
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
