"""Exercise the real Git ignore rules without creating historical data."""

from pathlib import Path
import subprocess
import tempfile
import unittest


class HistoricalFileExclusionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        source = Path(__file__).resolve().parents[1] / ".gitignore"
        (self.root / ".gitignore").write_bytes(source.read_bytes())
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True,
                       capture_output=True)

    def ignored(self, paths):
        result = subprocess.run(
            ["git", "-c", "core.excludesFile=/dev/null", "check-ignore", "--no-index", "--stdin"],
            cwd=self.root, input="\n".join(paths) + "\n", text=True,
            capture_output=True, check=False,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return set(result.stdout.splitlines())

    def test_database_replay_backup_lock_and_journal_variants_are_ignored(self):
        paths = []
        for directory in ("", "local/history/"):
            for extension in ("db", "sqlite", "sqlite3"):
                for suffix in ("", ".replay", ".bak", ".backup", ".replay.bak",
                               ".replay.backup", ".2026-11-10.backup"):
                    name = f"{directory}outbox.{extension}{suffix}"
                    # Dated backup names are protected by their final suffix;
                    # SQLite journals and owner locks append to the full name.
                    paths.extend(name + sidecar for sidecar in
                                 ("", ".lock", "-journal", "-wal", "-shm"))
        self.assertEqual(self.ignored(paths), set(paths))

    def test_other_documented_generated_files_are_ignored(self):
        paths = ["prepared.json", "seat_state.json", "seat_state.tmp",
                 "retained-outbox.sqlite3", "new-replay.sqlite3"]
        self.assertEqual(self.ignored(paths), set(paths))

    def test_legitimate_source_documentation_and_fixtures_remain_visible(self):
        paths = ["history_outbox.py", "tests/test_gitignore.py",
                 "cloudflare/migrations/0001_history.sql", "docs/HISTORY_RELIABILITY.md",
                 "schema.db.sql", "adapter.sqlite.py", "outbox.sqlite3.replay.py",
                 "docs/outbox.db.backup.md", "tests/fixtures/prepared.json",
                 "cloudflare/.dev.vars.example"]
        self.assertEqual(self.ignored(paths), set())


if __name__ == "__main__":
    unittest.main()
