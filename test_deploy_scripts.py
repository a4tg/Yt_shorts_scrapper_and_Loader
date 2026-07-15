import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class FirstSaasUpgradeScriptTests(unittest.TestCase):
    def test_upgrade_orders_backup_migration_and_application_switch(self) -> None:
        source = (ROOT / "deploy" / "first-saas-upgrade.sh").read_text(encoding="utf-8")
        backup = source.index('tar -czf "$backup_dir/legacy-data.tar.gz"')
        build = source.index("docker compose build")
        postgres = source.index("docker compose up -d postgres")
        migration = source.index("docker compose run --rm migrate")
        switch = source.index("docker compose up -d --no-deps yt-loader")
        health = source.index("curl -fsS http://127.0.0.1:8000/api/health")
        self.assertLess(backup, build)
        self.assertLess(build, postgres)
        self.assertLess(postgres, migration)
        self.assertLess(migration, switch)
        self.assertLess(switch, health)

    def test_upgrade_does_not_echo_database_password(self) -> None:
        source = (ROOT / "deploy" / "first-saas-upgrade.sh").read_text(encoding="utf-8")
        self.assertNotIn("echo $postgres_password", source)
        self.assertNotIn("set -x", source)
        self.assertIn("docker compose config --quiet", source)
        self.assertIn("audit-credits --allow-empty", source)


if __name__ == "__main__":
    unittest.main()
