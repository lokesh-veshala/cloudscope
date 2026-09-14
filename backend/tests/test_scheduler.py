import unittest
from unittest.mock import patch

import scheduler


class SchedulerTests(unittest.TestCase):
    def test_retry_backoff_is_bounded(self):
        self.assertEqual(scheduler.retry_delay_seconds(1), 30)
        self.assertEqual(scheduler.retry_delay_seconds(2), 60)
        self.assertEqual(scheduler.retry_delay_seconds(10), 300)

    @patch("scheduler.claim_job")
    @patch("scheduler.enqueue_due_jobs")
    def test_idle_cycle_does_not_call_aws(self, enqueue, claim):
        claim.return_value = None
        with patch("scheduler.collect_via_api") as collect:
            self.assertFalse(scheduler.run_once())
            collect.assert_not_called()
        enqueue.assert_called_once()

    @patch("scheduler.complete_job")
    @patch("scheduler.collect_via_api", return_value={"status": "SUCCEEDED"})
    @patch("scheduler.claim_job", return_value={"id": "job", "cloud_account_id": "account", "attempt_count": 1})
    @patch("scheduler.enqueue_due_jobs")
    def test_success_is_recorded(self, _enqueue, _claim, collect, complete):
        self.assertTrue(scheduler.run_once())
        collect.assert_called_once_with("account")
        complete.assert_called_once_with(
            {"id": "job", "cloud_account_id": "account", "attempt_count": 1},
            result={"status": "SUCCEEDED"})

    @patch("scheduler.complete_job")
    @patch("scheduler.collect_via_api", side_effect=RuntimeError("unavailable"))
    @patch("scheduler.claim_job", return_value={"id": "job", "cloud_account_id": "account", "attempt_count": 1})
    @patch("scheduler.enqueue_due_jobs")
    def test_failure_is_passed_to_retry_state(self, _enqueue, _claim, _collect, complete):
        self.assertTrue(scheduler.run_once())
        args, kwargs = complete.call_args
        self.assertEqual(args[0]["id"], "job")
        self.assertEqual(str(kwargs["error"]), "unavailable")


if __name__ == "__main__":
    unittest.main()
