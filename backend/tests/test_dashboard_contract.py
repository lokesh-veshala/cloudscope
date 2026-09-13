"""Source-level regression contracts, not browser or end-to-end tests."""
from pathlib import Path
import unittest

PAGE = (Path(__file__).resolve().parents[2] / "app/page.tsx").read_text()
MANAGEMENT = (Path(__file__).resolve().parents[2] / "app/management.tsx").read_text()
ONBOARDING = (Path(__file__).resolve().parents[2] / "app/aws-onboarding.ts").read_text()

class DashboardContractTests(unittest.TestCase):
    def test_management_views_are_wired(self):
        for name, component in (("accounts", "Accounts"), ("alerts", "AlertHistory"), ("settings", "Settings")):
            self.assertIn(f'view==="{name}"&&<{component}/>', PAGE)

    def test_notification_and_workspace_actions(self):
        self.assertIn('Open alert history', PAGE)
        self.assertIn('Open cloud accounts', PAGE)
        self.assertIn('PopoverTrigger', PAGE)

    def test_chart_pointer_keyboard_and_touch_handlers(self):
        for marker in ("onPointerEnter", "onPointerLeave", "onFocus", "onBlur", "onClick", 'role="tooltip"'):
            self.assertIn(marker, PAGE)
        # SVG title nodes are omitted because the HTML parser can relocate their
        # text during SSR hydration. Interactive hit targets already expose the
        # same values through deterministic aria-label attributes.
        self.assertNotIn("<title>", PAGE)

    def test_date_selection_does_not_prorate_unknown_costs(self):
        self.assertIn('Data unavailable for this range', PAGE)
        self.assertIn('days>90', PAGE)
        for view in ("overview", "resources", "teams", "quality"):
            self.assertIn(f'hasData&&view==="{view}"', PAGE)

    def test_no_fake_refresh_or_sns_success(self):
        self.assertNotIn("Verified snapshot refreshed", PAGE)
        self.assertNotIn("SNS message verified", PAGE)
        self.assertIn("No live AWS request was made", PAGE)
        self.assertIn("No real alerts have been sent", MANAGEMENT)

    def test_live_account_actions_are_wired(self):
        for marker in ('"/api/v1/accounts"', '"test-connection"', '"collect"', "Register account locally"):
            self.assertIn(marker, MANAGEMENT)
        self.assertIn("Private keys stay on this VM", MANAGEMENT)

    def test_preferences_are_explicitly_device_local(self):
        self.assertIn("Device-local preferences only", MANAGEMENT)
        self.assertIn('localStorage.setItem("cloudscope-density"', MANAGEMENT)

    def test_account_onboarding_generates_restricted_cloudformation(self):
        self.assertIn("Download CloudFormation template", MANAGEMENT)
        for marker in ("AWS::RolesAnywhere::TrustAnchor", "AWS::RolesAnywhere::Profile", "AWS::IAM::Role", "AWS::SNS::Topic"):
            self.assertIn(marker, ONBOARDING)
        self.assertIn("aws:PrincipalTag/x509Subject/CN", ONBOARDING)
        self.assertIn("Resource: !Ref AlertTopic", ONBOARDING)
        for forbidden in ("PRIVATE KEY-----", "ec2:StopInstances", "ec2:TerminateInstances", "lambda:InvokeFunction"):
            self.assertNotIn(forbidden, ONBOARDING)
