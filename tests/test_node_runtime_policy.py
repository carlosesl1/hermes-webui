"""Keep the fork's Docker and CI Node runtimes on the reviewed security release."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NodeRuntimePolicyTests(unittest.TestCase):
    def test_pinned_security_release(self):
        self.assertEqual((ROOT / ".node-version").read_text().strip(), "22.23.4")

    def test_docker_copies_official_node_and_npm_without_replacing_python(self):
        docker = (ROOT / "Dockerfile").read_text()
        version = (ROOT / ".node-version").read_text().strip()
        self.assertRegex(docker, rf"(?m)^FROM node:{re.escape(version)}-bookworm-slim AS node-runtime$")
        self.assertIn("FROM python:3.12-slim", docker)
        self.assertIn("COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node", docker)
        self.assertIn("COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm", docker)
        self.assertIn("../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm", docker)
        self.assertIn("../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx", docker)
        self.assertIn("RUN node /apptoo/scripts/check_node_runtime.cjs", docker)

    def test_ci_lint_and_test_jobs_use_pinned_node(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text()
        for section in (workflow.split("  lint:\n", 1)[1].split("  test:\n", 1)[0],
                        workflow.split("  test:\n", 1)[1]):
            self.assertIn("uses: actions/setup-node@v4", section)
            self.assertIn("node-version-file: '.node-version'", section)
            self.assertIn("run: node scripts/check_node_runtime.cjs", section)
        self.assertNotRegex(workflow, r"node-version:\s*['\"]?20")

    def test_package_declares_patched_supported_branches(self):
        package = json.loads((ROOT / "package.json").read_text())
        self.assertEqual(package["engines"]["node"],
                         ">=22.23.4 <23 || >=24.22.1 <25 || >=26.12.1 <27")

    def test_docker_ci_smokes_node_as_runtime_user(self):
        workflow = (ROOT / ".github/workflows/docker-smoke.yml").read_text()
        self.assertIn("--user 1024:1024", workflow)
        self.assertIn("--entrypoint node hermes-webui:local", workflow)
        self.assertIn("/apptoo/scripts/check_node_runtime.cjs", workflow)
        self.assertEqual(workflow.count("- '.node-version'"), 2)
        self.assertEqual(workflow.count("- 'scripts/check_node_runtime.cjs'"), 2)


if __name__ == "__main__":
    unittest.main()
