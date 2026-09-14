"""Source-level regression contracts, not browser or end-to-end tests."""
from pathlib import Path
import unittest

PAGE = (Path(__file__).resolve().parents[2] / "app/page.tsx").read_text()
MANAGEMENT = (Path(__file__).resolve().parents[2] / "app/management.tsx").read_text()
ONBOARDING = (Path(__file__).resolve().parents[2] / "app/aws-onboarding.ts").read_text()
LIVE_DASHBOARD = (Path(__file__).resolve().parents[2] / "app/live-dashboard.tsx").read_text()
LIVE_TEAMS = (Path(__file__).resolve().parents[2] / "app/live-teams.tsx").read_text()
API = (Path(__file__).resolve().parents[1] / "api.py").read_text()

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
        self.assertIn("No automatic threshold event has been created yet", MANAGEMENT)

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

    def test_default_dashboard_uses_live_aggregates_without_zero_fallbacks(self):
        self.assertIn('<LiveDashboard onSample=', PAGE)
        self.assertIn('/overview?start_date=', LIVE_DASHBOARD)
        self.assertIn('Unavailable periods are not drawn as zero', LIVE_DASHBOARD)
        self.assertIn('declared first-month baseline plus complete observations after team creation', LIVE_DASHBOARD)
        self.assertNotIn('$4,137', LIVE_DASHBOARD)

    def test_live_overview_has_adjustable_five_minute_trend(self):
        self.assertIn('/cost-trend?window_minutes=', LIVE_DASHBOARD)
        self.assertIn('Cost per 5-minute bucket', LIVE_DASHBOARD)
        for minutes in ('value={60}', 'value={360}', 'value={1440}',
                        'value={4320}', 'value={10080}'):
            self.assertIn(minutes, LIVE_DASHBOARD)
        for handler in ('onPointerMove', 'onPointerLeave', 'onClick', 'onKeyDown'):
            self.assertIn(handler, LIVE_DASHBOARD)
        self.assertIn('line gaps mean unavailable', LIVE_DASHBOARD)

    def test_live_teams_support_exact_and_or_filters_and_drilldown(self):
        self.assertIn('label:"Teams & limits"', LIVE_DASHBOARD)
        for marker in ('"AND"|"OR"', 'Preview matched resources', '/teams/preview',
                       '/resources?start_date=', 'case-sensitively', 'BLOCKED'):
            self.assertIn(marker, LIVE_TEAMS)
        for marker in ('Existing MTD baseline (USD)', 'baseline_amount_usd',
                       'Declared baseline', 'FAIL CLOSED', 'allow_partial_alerts',
                       'Send alerts from partial observed cost'):
            self.assertIn(marker, LIVE_TEAMS)
        self.assertNotIn('status: "Normal"', LIVE_TEAMS)

    def test_live_team_deletion_cost_history_and_sns_test_are_wired(self):
        for marker in ('method:"DELETE"', 'window.confirm', 'View history',
                       'duration_seconds', 'effective_hourly_usd',
                       'Not complete lifetime or invoice history',
                       'TeamConsumptionChart', 'unavailable is not zero',
                       'onPointerEnter', 'onFocus', 'onKeyDown'):
            self.assertIn(marker, LIVE_TEAMS)
        self.assertIn('Test SNS publish', MANAGEMENT)
        self.assertIn('/notifications/test', MANAGEMENT)
        self.assertIn('Publishes one test event', MANAGEMENT)
        self.assertIn('/api/v1/alerts', MANAGEMENT)
        self.assertIn('Update cadence', MANAGEMENT)

    def test_team_trend_uses_observed_cost_composite_key(self):
        self.assertIn("count(m.resource_id)", API)
        self.assertNotIn("count(m.id)", API)
