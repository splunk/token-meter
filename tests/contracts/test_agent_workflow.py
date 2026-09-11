import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class AgentWorkflowContractTests(unittest.TestCase):
    def test_communication_manager_is_a_first_class_outbound_message_gate(self):
        routing = json.loads(
            (ROOT / ".agents" / "workflow" / "routing.yaml").read_text(
                encoding="utf-8"
            )
        )
        handoff = json.loads(
            (ROOT / ".agents" / "workflow" / "handoff.schema.json").read_text(
                encoding="utf-8"
            )
        )
        lock = json.loads(
            (ROOT / "agent-toolchain.lock.yaml").read_text(encoding="utf-8")
        )

        communication = routing["task_classes"]["communication"]
        self.assertEqual(
            communication["route"],
            ["coordinator", "communication-manager", "authorized-channel-operator"],
        )
        self.assertEqual(
            communication["applies_to"],
            [
                "slack",
                "github",
                "release-communication",
                "contributor-communication",
                "user-facing-status",
            ],
        )
        self.assertEqual(
            communication["separation_of_duties"],
            {
                "communication-manager": "draft-and-quality-gate",
                "authorized-operator": "external-write-and-read-back",
            },
        )
        self.assertEqual(
            communication["principles"],
            [
                "evidence-before-claims",
                "channel-appropriate-detail",
                "concise-by-default",
                "privacy-and-sensitive-data-minimization",
                "explicit-delivery-state-language",
                "no-invented-diagnosis-deadline-or-commitment",
                "approval-scope-preservation",
                "deduplicate-before-send",
                "read-back-after-write",
            ],
        )

        for task_class in (
            "reporter-end-to-end",
            "bounded-code-change",
            "architectural-change",
            "github-operation",
        ):
            route = routing["task_classes"][task_class]["route"]
            self.assertIn("communication-manager", route)
            self.assertLess(
                route.index("communication-manager"), route.index("github-operator")
            )

        owner_roles = handoff["properties"]["owner_role"]["enum"]
        self.assertIn("communication-manager", owner_roles)

        project_skills = {item["name"]: item for item in lock["project_skills"]}
        self.assertEqual(
            project_skills["token-meter-communication"]["path"],
            ".agents/skills/token-meter-communication/SKILL.md",
        )
        roles = {item["name"]: item for item in lock["roles"]}
        self.assertEqual(
            roles["communication-manager"],
            {
                "name": "communication-manager",
                "skill": "token-meter-communication",
                "source": ".agents/roles/communication-manager.md",
                "write_mode": "read-only-message-author",
                "codex_adapter": ".codex/agents/token-meter-communication-manager.toml",
                "claude_adapter": ".claude/agents/token-meter-communication-manager.md",
            },
        )

        for path in (
            ".agents/skills/token-meter-communication/SKILL.md",
            ".agents/roles/communication-manager.md",
            ".codex/agents/token-meter-communication-manager.toml",
            ".claude/agents/token-meter-communication-manager.md",
            ".claude/skills/token-meter-communication/SKILL.md",
        ):
            self.assertTrue((ROOT / path).is_file(), path)

        agents = (ROOT / "specs" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("communication-manager", agents)
        self.assertIn("evidence before claims", agents.lower())

    def test_reporter_end_to_end_route_has_bounded_delivery_authority(self):
        routing = json.loads(
            (ROOT / ".agents" / "workflow" / "routing.yaml").read_text(
                encoding="utf-8"
            )
        )
        route = routing["task_classes"]["reporter-end-to-end"]

        self.assertEqual(
            route["route"],
            [
                "coordinator",
                "developer",
                "coordinator",
                "tester+reviewer",
                "coordinator",
                "communication-manager",
                "github-operator",
                "communication-manager",
                "github-operator",
                "coordinator",
            ],
        )
        self.assertEqual(
            route["delivery_order"],
            [
                "developer-implements-scoped-change",
                "coordinator-commits-scoped-change",
                "tester-and-reviewer-gate-exact-commit",
                "communication-manager-drafts-pull-request",
                "github-operator-pushes-branch-and-creates-pull-request",
                "communication-manager-drafts-linked-handoffs",
                "github-operator-posts-linked-issue-handoff",
                "coordinator-posts-slack-follow-up",
            ],
        )
        self.assertEqual(
            route["standing_approval_scope"],
            [
                "scoped-commit",
                "dedicated-branch-push",
                "linked-pull-request",
                "github-reporter-testing-handoff",
                "concise-slack-pr-follow-up",
            ],
        )
        self.assertEqual(route["slack_follow_up"], {
            "format": "one-short-paragraph",
            "required": [
                "thanks",
                "pull-request-link",
                "brief-try-request",
                "pratiks-agent-signature",
            ],
            "forbidden": [
                "code",
                "commands",
                "clone-or-install-steps",
                "procedural-next-steps",
                "test-matrix",
            ],
        })
        self.assertEqual(route["github_handoff"], {
            "location": "linked-issue",
            "content": "detailed-clone-install-restore-and-test-instructions",
        })
        self.assertEqual(
            route["separately_approved_actions"],
            ["merge", "release", "close", "label", "hosted-review-request"],
        )

        agents = (ROOT / "specs" / "AGENTS.md").read_text(encoding="utf-8")
        intake = (
            ROOT / ".agents" / "skills" / "token-meter-intake" / "SKILL.md"
        ).read_text(encoding="utf-8")
        github_ops = (
            ROOT / ".agents" / "skills" / "token-meter-github-ops" / "SKILL.md"
        ).read_text(encoding="utf-8")
        normalized_intake = " ".join(intake.split()).lower()
        self.assertIn(
            "except for the `reporter-end-to-end` route and the two narrow "
            "standing exceptions below",
            normalized_intake,
        )
        for source in (agents, intake, github_ops):
            self.assertIn("reporter-end-to-end", source)
            self.assertIn("procedural next steps", source.lower())
            self.assertIn("Pratik's agent", source)

    def test_low_risk_fast_path_is_explicit_bounded_and_agent_free(self):
        routing = json.loads(
            (ROOT / ".agents" / "workflow" / "routing.yaml").read_text(
                encoding="utf-8"
            )
        )
        review_policy = json.loads(
            (ROOT / ".agents" / "workflow" / "review-policy.yaml").read_text(
                encoding="utf-8"
            )
        )

        route = routing["task_classes"]["low-risk-fast-path"]
        self.assertEqual(route["route"], ["coordinator-or-developer", "coordinator"])
        self.assertEqual(route["review_gate"], "fast_path")
        self.assertFalse(route["independent_testing_required"])
        self.assertFalse(route["independent_review_required"])
        self.assertFalse(route["installed_runtime_required_by_default"])
        self.assertIn(
            "selected-allowed-change-kind", route["required_qualification"]
        )

        fast_path = review_policy["fast_path"]
        self.assertEqual(fast_path["minimum_testers"], 0)
        self.assertEqual(fast_path["minimum_project_reviewers"], 0)
        self.assertIn("allowed-change-kind-selected", fast_path["eligibility_all"])
        self.assertIn("no-high-risk-category", fast_path["eligibility_all"])
        self.assertIn("no-production-behavior-change", fast_path["eligibility_all"])
        self.assertIn("focused-checks", fast_path["required_evidence"])
        self.assertIn("scope-expansion", fast_path["escalate_on"])
        self.assertIn("unexpected-failure", fast_path["escalate_on"])

        high_risk = set(review_policy["high_risk"]["categories"])
        self.assertTrue(high_risk.issubset(set(fast_path["disqualifying_risks"])))

    def test_agent_prompts_explain_fast_path_and_fail_closed_escalation(self):
        agents = (ROOT / "specs" / "AGENTS.md").read_text(encoding="utf-8")
        development = (
            ROOT / ".agents" / "skills" / "token-meter-development" / "SKILL.md"
        ).read_text(encoding="utf-8")
        intake = (
            ROOT / ".agents" / "skills" / "token-meter-intake" / "SKILL.md"
        ).read_text(encoding="utf-8")
        review = (ROOT / "specs" / "agent-workflow" / "REVIEW.md").read_text(
            encoding="utf-8"
        )

        for source in (agents, development, intake, review):
            self.assertIn("low-risk fast path", source.lower())
        normalized_agents = " ".join(agents.split())
        self.assertIn("no independent tester or reviewer", normalized_agents)
        self.assertIn(
            "does not require installed-runtime verification", normalized_agents
        )
        self.assertIn("escalate", development.lower())

    def test_generated_root_agent_and_review_prompts_match_their_sources(self):
        agents_source = (ROOT / "specs" / "AGENTS.md").read_text(encoding="utf-8")
        expected_agents = (
            "<!-- Generated by scripts/agent_tools.py; do not edit directly. "
            "Source: specs/AGENTS.md. -->\n\n"
            + agents_source.replace(
                "[ARCHITECTURE.md](ARCHITECTURE.md)",
                "[ARCHITECTURE.md](specs/ARCHITECTURE.md)",
            )
        )
        self.assertEqual(
            (ROOT / "AGENTS.md").read_text(encoding="utf-8"), expected_agents
        )

        review_source = (
            ROOT / "specs" / "agent-workflow" / "REVIEW.md"
        ).read_text(encoding="utf-8")
        expected_review = (
            "<!-- Generated by scripts/agent_tools.py; do not edit directly. "
            "Source: specs/agent-workflow/REVIEW.md. -->\n\n"
            + review_source
        )
        self.assertEqual(
            (ROOT / "REVIEW.md").read_text(encoding="utf-8"), expected_review
        )


if __name__ == "__main__":
    unittest.main()
