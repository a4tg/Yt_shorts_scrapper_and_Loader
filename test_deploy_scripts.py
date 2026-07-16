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


class ContainerManifestTests(unittest.TestCase):
    def test_saas_routes_are_copied_into_production_image(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        for module in (
            "workspace_service.py",
            "workspace_routes.py",
            "content_routes.py",
            "file_validation.py",
            "messaging_routes.py",
        ):
            self.assertIn(module, dockerfile)

    def test_all_supported_source_cookie_paths_are_mounted(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("YOUTUBE_COOKIES: /cookies/www.youtube.com_cookies.txt", compose)
        self.assertIn("VK_COOKIES: /cookies/vk.com_cookies.txt", compose)
        self.assertIn("RUTUBE_COOKIES: /cookies/rutube.ru_cookies.txt", compose)
        self.assertIn("./cookies:/cookies", compose)

    def test_workspace_depth_flags_are_passed_to_containers(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        for name in (
            "WORKSPACE_DEPTH_SHELL",
            "CHAT_ANYWHERE",
            "ASSET_VIEWER",
            "ASSET_REVIEWS",
            "PROJECT_GRAPH",
            "DECISION_INTELLIGENCE",
        ):
            self.assertIn(f"YT_LOADER_FEATURE_{name}", compose)


if __name__ == "__main__":
    unittest.main()
