from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RefreshIntervalTests(unittest.TestCase):
    def test_interval_validation_completion_retry_and_overnight_priority(self):
        compiler = shutil.which("clang++") or shutil.which("g++")
        if not compiler:
            self.skipTest("A C++ compiler is required for the firmware host test")
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "refresh-interval"
            subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-Isrc", "tests/refresh_interval_test.cpp",
                "-o", str(executable),
            ], cwd=ROOT, check=True, capture_output=True, text=True)
            subprocess.run([str(executable)], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
