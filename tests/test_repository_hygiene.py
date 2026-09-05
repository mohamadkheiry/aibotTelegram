from __future__ import annotations

import fnmatch
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILENAMES = (
    ".env",
    "runtime.env",
    "bot.db",
    "bot.db-wal",
    "bot.db-shm",
    "bot.db-journal",
    "bot.sqlite",
    "bot.sqlite-wal",
    "bot.sqlite-shm",
    "bot.sqlite-journal",
    "bot.sqlite3",
    "bot.sqlite3-wal",
    "bot.sqlite3-shm",
    "bot.sqlite3-journal",
)
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "Telegram bot token",
        re.compile(
            rb"(?<![0-9])[0-9]{8,12}:[A-Za-z0-9_-]{30,50}(?![A-Za-z0-9_-])"
        ),
    ),
    ("GitHub token", re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("AWS access key", re.compile(rb"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])")),
    (
        "private key",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    ("Stripe live key", re.compile(rb"sk_live_[A-Za-z0-9]{16,}")),
)


def repository_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    names = {
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    }
    return [ROOT / name for name in sorted(names)]


def dockerignore_matches(path: str) -> bool:
    """Evaluate the simple root/basename rules used by this repository."""

    ignored = False
    name = Path(path).name
    for raw_rule in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        rule = raw_rule.strip()
        if not rule or rule.startswith("#"):
            continue
        negated = rule.startswith("!")
        pattern = rule[1:] if negated else rule
        if fnmatch.fnmatchcase(path, pattern) or (
            "/" not in pattern and fnmatch.fnmatchcase(name, pattern)
        ):
            ignored = not negated
    return ignored


def is_runtime_path(relative: Path) -> bool:
    """Return whether a repository path is runtime state rather than source."""

    lowered_parts = tuple(part.casefold() for part in relative.parts)
    lowered_name = relative.name.casefold()
    is_env_file = (
        lowered_name == ".env"
        or lowered_name.endswith(".env")
        or lowered_name.startswith(".env.")
    ) and lowered_name != ".env.example"
    is_sqlite_file = re.fullmatch(
        r".+\.(?:db|sqlite|sqlite3)(?:-.+)?", lowered_name
    ) is not None
    return bool(
        is_env_file
        or is_sqlite_file
        or relative.suffix.casefold() in {".log", ".pyc"}
        or any(part in {"data", "logs", "__pycache__"} for part in lowered_parts)
    )


class RepositoryHygieneTests(unittest.TestCase):
    def test_runtime_env_and_sqlite_names_are_ignored_by_git_and_docker(self) -> None:
        for filename in RUNTIME_FILENAMES:
            self.assertTrue(is_runtime_path(Path(filename)))
            with self.subTest(filename=filename, ignore_file=".gitignore"):
                result = subprocess.run(
                    ["git", "check-ignore", "--no-index", "--quiet", "--", filename],
                    cwd=ROOT,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, f"Git does not ignore {filename}")
            with self.subTest(filename=filename, ignore_file=".dockerignore"):
                self.assertTrue(
                    dockerignore_matches(filename),
                    f"Docker build context does not ignore {filename}",
                )

        git_example = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", "--", ".env.example"],
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(git_example.returncode, 1, ".env.example must remain committable")
        self.assertFalse(is_runtime_path(Path(".env.example")))
        self.assertFalse(
            dockerignore_matches(".env.example"),
            ".env.example must remain available to the Docker build context",
        )

    def test_repository_contains_no_runtime_state_or_plaintext_secrets(self) -> None:
        violations: list[str] = []
        for path in repository_files():
            relative = path.relative_to(ROOT)
            if is_runtime_path(relative):
                violations.append(f"runtime artifact is tracked: {relative}")
                continue
            if not path.is_file():
                continue
            payload = path.read_bytes()
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(payload):
                    violations.append(f"{label} found in {relative}")
        self.assertEqual(violations, [], "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
