from tests.dashboard_layout_support import DashboardLayoutCase


class DashboardGoalTests(DashboardLayoutCase):
    def test_repeat_labels_cover_calendar_and_custom_periods(self):
        result = self.run_chart_gap_harness("""
load(['goalRepeatText']);
console.log(JSON.stringify({
 daily:goalRepeatText({period:'custom',start:'2026-11-13',end:'2026-11-13'}),
 fixed:goalRepeatText({period:'custom',start:'2026-11-13',end:'2026-12-12'}),
 weekly:goalRepeatText({period:'week'}),
 monthly:goalRepeatText({period:'month'})
}));
""")
        self.assertEqual(result, {"daily": "daily", "fixed": "every 30 days", "weekly": "weekly", "monthly": "monthly"})

    def test_hourly_chart_positions_use_timestamps_for_the_partial_hour(self):
        result = self.run_chart_gap_harness("""
load(['goalTrendPosition','goalTrendIndex']);
const samples=[{at:3600},{at:7200},{at:9000}];
console.log(JSON.stringify({middle:goalTrendPosition(samples,1),nearest:goalTrendIndex(samples,.8),last:goalTrendIndex(samples,1)}));
""")
        self.assertAlmostEqual(result["middle"], 2 / 3)
        self.assertEqual((result["nearest"], result["last"]), (1, 2))

    def test_learn_links_and_structured_checkin_have_no_journal_field(self):
        for metric in ("output_per_dollar", "context_load", "reasoning_ratio", "frontier_share", "cache_hit_ratio"):
            self.assertIn("id=learn-efficiency-" + metric, self.page)
        self.assertIn("data-goal-learn", self.page)
        self.assertIn("name=reflection-outcome", self.page)
        self.assertIn("name=reflection-next", self.page)
        self.assertIn("period_start:form.dataset.period", self.page)
        reflection = self.page.split("function goalReflectionCard(d){", 1)[1].split("function renderGoalDetail", 1)[0]
        self.assertNotIn("textarea", reflection)
