"""Host safety-policy checks plus ordering of the real firmware integration."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PowerPolicyTests(unittest.TestCase):
    def test_voltage_policy_persistence_gate_hysteresis_and_retry_state(self):
        compiler = shutil.which("clang++") or shutil.which("g++")
        if not compiler:
            self.skipTest("A C++ compiler is required for the firmware host test")
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "power-policy"
            subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-Isrc", "tests/power_policy_test.cpp",
                "-o", str(executable),
            ], cwd=ROOT, check=True, capture_output=True, text=True)
            subprocess.run([str(executable)], check=True, capture_output=True, text=True)

    def test_guards_run_before_filesystem_and_panel_but_after_cache_recovery(self):
        source = (ROOT / "src/main.cpp").read_text()
        setup = source.split("void setup() {", 1)[1]
        self.assertLess(setup.index("pauseForLowBattery()"), setup.index("LittleFS.begin"))
        self.assertLess(setup.index("pauseForLowBattery()"), setup.index("fetchAndDisplay()"))
        self.assertIn("if (!timerWake) delay(1500)", setup)
        self.assertIn("powerPolicy::beginWake(powerState, timerWake)", setup)
        display = source.split("bool displayPreparedFrame(", 1)[1].split("void updateBatteryFromCache()", 1)[0]
        self.assertLess(display.index("stopRadio()"), display.index("pauseForLowBattery()"))
        self.assertLess(display.index("pauseForLowBattery()"), display.index("LittleFS.rename"))
        self.assertLess(display.index("pauseForLowBattery()"), display.index("panel.display(frame)"))
        self.assertLess(display.index("panel.display(frame)"), display.rindex("refresh::remember"))
        fetch = source.split("bool fetchAndDisplay()", 1)[1].split("}  // namespace", 1)[0]
        retry = fetch.split("Re-downloading unconditionally", 1)[1]
        self.assertLess(retry.index("downloadImage("), retry.index("stopRadio()"))
        self.assertLess(retry.index("stopRadio()"), retry.index("prepareSourceFrame"))
        self.assertIn("status == 200 || (status == 304 && sentETag)", source)
        self.assertIn("refresh::validETag(previousETag)", source)
        self.assertNotIn("ChargeState::Charging", source.split("bool pauseForLowBattery()", 1)[1].split("void setStatusLed", 1)[0])


if __name__ == "__main__":
    unittest.main()
