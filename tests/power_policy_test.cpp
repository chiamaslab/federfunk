#include "PowerPolicy.h"
#include <cassert>
#include <cstring>
#include <initializer_list>
#include <limits>

using namespace powerPolicy;

int main() {
  for (uint16_t value = 0; value < 5000; ++value) {
    const bool expected = value >= 3200 && value <= 3600 && value % 50 == 0;
    assert(validStop(value) == expected);
  }
  assert(parseStop("3200") == 3200);
  assert(parseStop("3300") == 3300);
  assert(parseStop("3600") == 3600);
  for (const char* bad : {"", "3199", "3201", "3650", "3300.0", "3.3",
                         "-3300", "+3300", " 3300", "3300 ", "3300\r\n",
                         "4294967296", "184467440737095516163300"}) {
    assert(parseStop(bad) == 0);
  }
  assert(parseStop(nullptr) == 0);
  // The exact same validated policy must not cause another flash write.
  assert(!shouldPersistStop(3300, 3300));
  assert(!shouldPersistStop(3300, 0));
  assert(!shouldPersistStop(3300, 3251));
  assert(shouldPersistStop(3300, 3250));
  assert(shouldPersistStop(0, 3300));

  assert(pauseForVoltage(false, 3300, 3300, 3300));
  assert(pauseForVoltage(false, 3290, 3280, 3300));
  assert(!pauseForVoltage(false, 3300, 3301, 3300));
  assert(!pauseForVoltage(false, 3301, 3300, 3300));
  assert(!pauseForVoltage(false, 4000, 3990, 3300));
  assert(pauseForVoltage(true, 3449, 3450, 3300));
  assert(pauseForVoltage(true, 3450, 3449, 3300));
  assert(!pauseForVoltage(true, 3450, 3450, 3300));
  assert(!pauseForVoltage(true, 3600, 3590, 3300));
  for (uint16_t invalid : {uint16_t(0), uint16_t(2499), uint16_t(4501), uint16_t(65535)}) {
    assert(pauseForVoltage(false, invalid, invalid, 3300));
    assert(pauseForVoltage(false, invalid, 4000, 3300));
    assert(pauseForVoltage(false, 4000, invalid, 3300));
    assert(pauseForVoltage(true, invalid, 4000, 3300));
  }
  // Bad persisted cutoffs cannot accidentally disable the default guard.
  assert(pauseForVoltage(false, 3290, 3280, 0));
  assert(pauseForVoltage(true, 3440, 3440, 65535));
  // A raised response policy must pause before adopting the new image.
  assert(!pauseForVoltage(false, 3450, 3450, 3300));
  assert(pauseForVoltage(false, 3450, 3450, 3500));

  Retained state{};
  beginWake(state, false);
  assert(state.version == retainedVersion);
  assert(state.previousResult == Result::Unknown);
  assert(state.previousSleepSeconds == 0);
  assert(finishWake(state, Result::Failed, 3600, 1800, 0) == 1800);
  beginWake(state, true);
  assert(state.previousResult == Result::Failed);
  assert(state.previousSleepSeconds == 1800);
  assert(finishWake(state, Result::Failed, 3600, 1800, 0) == 3600);
  assert(finishWake(state, Result::Failed, 3600, 1800, 0) == 7200);
  for (unsigned attempt = 0; attempt < 1000; ++attempt) {
    assert(finishWake(state, Result::Failed, 3600, 1800, 0) == 7200);
    assert(state.consecutiveFailures == 3);
  }
  assert(finishWake(state, Result::Unchanged, 3600, 1800, 0) == 3600);
  assert(state.consecutiveFailures == 0);
  assert(finishWake(state, Result::Failed, 0, 1800, 0) == 1800);
  assert(finishWake(state, Result::Updated, 0, 1800, 0) == 1800);
  assert(state.consecutiveFailures == 0);
  assert(finishWake(state, Result::Failed, 0, 1800, 0) == 1800);
  assert(finishWake(state, Result::ScheduledHold, 3600, 1800, 17) == 17);
  assert(state.consecutiveFailures == 0);
  // Night holds have priority even if the same response raised the cutoff.
  assert(finishWake(state, Result::LowBattery, 3600, 1800, 42000) == 42000);
  assert(state.previousSleepSeconds == 42000);
  assert(finishWake(state, Result::LowBattery, 3600, 1800, 0) == 1800);
  assert(finishWake(state, Result::ScheduledHold, 0, 1800,
                    std::numeric_limits<uint32_t>::max()) == 86400);
  assert(static_cast<uint64_t>(state.previousSleepSeconds) * 1000000ULL == 86400000000ULL);

  state.lowBatteryPaused = true;
  beginWake(state, true);
  assert(state.lowBatteryPaused);
  beginWake(state, false);
  assert(!state.lowBatteryPaused);
  assert(state.previousResult == Result::Unknown);
  state.consecutiveFailures = 255;
  beginWake(state, true);
  assert(state.consecutiveFailures == 0);
  state.previousSleepSeconds = 86401;
  beginWake(state, true);
  assert(state.previousSleepSeconds == 0);
  state.previousResult = static_cast<Result>(255);
  beginWake(state, true);
  assert(state.previousResult == Result::Unknown);
  assert(resultName(Result::Unknown) == nullptr);
  assert(std::strcmp(resultName(Result::Updated), "updated") == 0);
  assert(std::strcmp(resultName(Result::Unchanged), "unchanged") == 0);
  assert(std::strcmp(resultName(Result::Failed), "failed") == 0);
  assert(std::strcmp(resultName(Result::ScheduledHold), "scheduled_hold") == 0);
  assert(std::strcmp(resultName(Result::LowBattery), "low_battery") == 0);
}
