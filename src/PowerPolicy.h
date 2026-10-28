#pragma once

#include <cstdint>

namespace powerPolicy {
constexpr char capabilityHeader[] = "X-FederFunk-Power-Control";
constexpr char stopHeader[] = "X-FederFunk-Stop-Millivolts";
constexpr uint16_t defaultStopMillivolts = 3300;
constexpr uint16_t resumeMarginMillivolts = 150;
constexpr uint32_t lowBatteryRecheckSeconds = 1800;
constexpr uint32_t retainedVersion = 0x46505001;  // FPP, version 1.

inline bool validStop(uint32_t millivolts) {
  return millivolts >= 3200 && millivolts <= 3600 && millivolts % 50 == 0;
}

inline uint16_t parseStop(const char* text) {
  if (!text || !*text) return 0;
  uint32_t value = 0;
  for (const char* p = text; *p; ++p) {
    if (*p < '0' || *p > '9') return 0;
    const uint32_t digit = static_cast<uint32_t>(*p - '0');
    if (value > (3600 - digit) / 10) return 0;
    value = value * 10 + digit;
  }
  return validStop(value) ? static_cast<uint16_t>(value) : 0;
}

inline bool shouldPersistStop(uint16_t current, uint16_t proposed) {
  return validStop(proposed) && current != proposed;
}

inline bool validMeasurement(uint16_t millivolts) {
  return millivolts >= 2500 && millivolts <= 4500;
}

// Two independent trimmed ADC readings are required both to enter and leave
// the pause. Invalid measurements fail closed for a confirmed battery. This
// reduces load; it is not a physical battery disconnect or cell protector.
inline bool pauseForVoltage(bool wasPaused, uint16_t first, uint16_t second,
                            uint16_t stop) {
  if (!validStop(stop)) stop = defaultStopMillivolts;
  if (!validMeasurement(first) || !validMeasurement(second)) return true;
  if (wasPaused) {
    const uint16_t resume = stop + resumeMarginMillivolts;
    return !(first >= resume && second >= resume);
  }
  return first <= stop && second <= stop;
}

enum class Result : uint8_t { Unknown, Updated, Unchanged, Failed, ScheduledHold, LowBattery };

inline const char* resultName(Result result) {
  switch (result) {
    case Result::Updated: return "updated";
    case Result::Unchanged: return "unchanged";
    case Result::Failed: return "failed";
    case Result::ScheduledHold: return "scheduled_hold";
    case Result::LowBattery: return "low_battery";
    default: return nullptr;
  }
}

struct Retained {
  uint32_t version;
  uint8_t consecutiveFailures;
  bool lowBatteryPaused;
  Result previousResult;
  uint32_t previousSleepSeconds;
};

inline void beginWake(Retained& state, bool timerWake) {
  if (!timerWake || state.version != retainedVersion ||
      state.consecutiveFailures > 3 ||
      static_cast<uint8_t>(state.previousResult) > static_cast<uint8_t>(Result::LowBattery) ||
      state.previousSleepSeconds > 86400) {
    state = {};
    state.version = retainedVersion;
  }
}

inline uint32_t finishWake(Retained& state, Result result,
                           uint32_t successfulSeconds, uint32_t fallbackSeconds,
                           uint32_t holdSeconds) {
  uint32_t seconds;
  if (holdSeconds > 0) {
    // A saved overnight hold remains the authority, even when the battery is
    // low. Neither failure retries nor the ADC recheck should shorten it.
    seconds = holdSeconds > 86400 ? 86400 : holdSeconds;
    state.consecutiveFailures = 0;
  } else if (result == Result::LowBattery) {
    seconds = lowBatteryRecheckSeconds;
    state.consecutiveFailures = 0;
  } else if (result == Result::Failed) {
    if (state.consecutiveFailures < 3) ++state.consecutiveFailures;
    seconds = state.consecutiveFailures == 1 ? fallbackSeconds :
              state.consecutiveFailures == 2 ? 3600 : 7200;
  } else {
    state.consecutiveFailures = 0;
    seconds = successfulSeconds ? successfulSeconds : fallbackSeconds;
  }
  state.previousResult = result;
  state.previousSleepSeconds = seconds;
  return seconds;
}
}  // namespace powerPolicy
