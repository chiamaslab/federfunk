"""Check that the actual installer payload boots independently of the checkout."""
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent


class InstallTests(unittest.TestCase):
    def test_release_payload_contains_runtime_dependencies_metadata_and_web_extensions(self):
        script = (ROOT / "install.sh").read_text().replace("\\\n", " ")
        command = next(line for line in script.splitlines() if line.startswith("install -m 644 "))
        sources = shlex.split(command)[3:-1]
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary)
            for filename in sources:
                shutil.copy2(ROOT / filename, release / filename)
            # These directories are included wholesale by the real installer;
            # symlink them in the test to avoid copying large public plates.
            self.assertRegex(script, r'cp -R artwork web "\$release_dir/"')
            for directory in ("artwork", "web"):
                (release / directory).symlink_to(ROOT / directory, target_is_directory=True)
            program = """
import json, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
import server, render, classic, collage, field_guide, color_profiles, artwork_packs, contour_artwork, subject_geometry
assert render.catalogue(pack_directory=pathlib.Path('absent-cache'))
assert artwork_packs.registry()
assert json.loads(subject_geometry.GEOMETRY_FILE.read_text())['assets']
for filename in ('index.html', 'app.js', 'style.css', 'enhancements.js', 'enhancements.css'):
    assert (pathlib.Path('web') / filename).is_file()
with tempfile.TemporaryDirectory() as state:
    feed = server.Feed('http://unused', pathlib.Path(state), demo=True, assets=[])
    assert feed.history.days() == []
    assert feed.battery_state(7)['readings'] == []
"""
            result = subprocess.run([sys.executable, "-I", "-c", program], cwd=release,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
        subprocess.run(["sh", "-n", str(ROOT / "install.sh")], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
