"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const booleans = ["show_names", "show_scientific", "show_counts", "show_title", "show_qr", "show_all"];
  const numberFormat = new Intl.NumberFormat();
  const defaultSchedule = {
    enabled: true, timezone: "America/Denver", latitude: 40.7372, longitude: -111.8581,
    start: { mode: "sunrise", offset_minutes: 0, time: "07:00" },
    end: { mode: "sunset", offset_minutes: 60, time: "20:00" },
  };
  const defaults = {
    show_names: false, show_scientific: false, show_counts: false,
    show_title: true, show_qr: true, show_all: true, scale: "log", max_birds: 8,
    style: "collage", classic_media: "photos", orientation: "portrait", dashboard_url: "", variants: {}, variant_mode: "daily",
    mirror_mode: "original", mirror_overrides: {},
    refresh_interval_minutes: 30,
    stop_voltage: 3.3, update_mode: "every_check", meaningful_change_percent: 10, meaningful_max_minutes: 180,
  };
  let currentState = null;
  let draft = { ...defaults, variants: {}, mirror_overrides: {}, schedule: scheduleSettings() };
  let csrfToken = "";
  let dirty = false;
  let saving = false;
  let refreshing = false;
  let ready = false;
  let imageMode = "art";
  let displayedMode = null;
  let imageKey = "";
  let imageRequest = 0;
  let presentationTimer = null;
  let presentationBusy = false;
  let presentationKey = "";
  let presentationRequest = 0;
  let pendingPresentation = null;

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function count(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : 0;
  }

  function dateValue(value) {
    if (value === null || value === undefined || value === "") return null;
    const numeric = typeof value === "number" ? (value < 1e12 ? value * 1000 : value) : value;
    const date = new Date(numeric);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function timeLabel(value) {
    if (typeof value === "string" && /^\d{2}:\d{2}(:\d{2})?$/.test(value)) return value.slice(0, 5);
    const date = dateValue(value);
    return date ? date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : null;
  }

  function scheduleSettings(settings = {}) {
    return {
      ...defaultSchedule, ...settings,
      start: { ...defaultSchedule.start, ...settings.start },
      end: { ...defaultSchedule.end, ...settings.end },
    };
  }

  function scheduledTime(value, timezone, includeDay = false) {
    const date = dateValue(value);
    if (!date) return null;
    const options = { timeZone: timezone || "America/Denver", hour: "numeric", minute: "2-digit" };
    if (includeDay) options.weekday = "short";
    try { return date.toLocaleString([], options); }
    catch { return date.toISOString(); }
  }

  function renderSchedule(state) {
    const schedule = state.schedule;
    const settings = state.settings?.schedule;
    const supportsInterval = state.device?.refresh_control_supported === true;
    const reportedInterval = Number(state.device?.refresh_interval_seconds) / 60;
    const intervalMinutes = supportsInterval && Number.isFinite(reportedInterval) && reportedInterval > 0
      ? Math.round(reportedInterval) : defaults.refresh_interval_minutes;
    const intervalLabel = `Checks every ${intervalMinutes} minutes`;
    const savedInterval = state.settings?.refresh_interval_minutes ?? defaults.refresh_interval_minutes;
    byId("refresh-interval-help").textContent = !supportsInterval ? "Requires the next firmware version."
      : savedInterval !== intervalMinutes ? `Pending next wake · Currently ${intervalMinutes} minutes.`
      : "Applies at the next scheduled wake.";
    const supportsPower = state.device?.power_control_supported === true;
    const reportedStop = state.device?.stop_voltage;
    const savedStop = state.settings?.stop_voltage ?? defaults.stop_voltage;
    byId("power-control-status").textContent = !supportsPower ? "Requires the next firmware version."
      : typeof reportedStop !== "number" || Math.abs(reportedStop - savedStop) > .001
      ? "Pending next scheduled wake." : `Active · ${reportedStop.toFixed(2)} V`;
    byId("frame-schedule-status").hidden = !schedule;
    byId("sun-times").hidden = !schedule;
    if (!schedule) return;
    const timezone = settings?.timezone || "America/Denver";
    const sunrise = scheduledTime(schedule.sunrise, timezone);
    const sunset = scheduledTime(schedule.sunset, timezone);
    byId("sun-times").textContent = sunrise || sunset
      ? `Today · Sunrise ${sunrise || "unavailable"} · Sunset ${sunset || "unavailable"}`
      : "Sun times unavailable. Using saved clock times.";
    byId("sun-times").title = `For the saved location, in ${timezone}`;
    const until = scheduledTime(schedule.next_change, timezone, true);
    const resume = scheduledTime(schedule.resume_at || schedule.next_change, timezone, true);
    byId("frame-schedule-status").classList.toggle("resting", schedule.active === false);
    if (settings?.enabled === false) {
      byId("frame-schedule-label").textContent = "Updates enabled all day";
      byId("frame-schedule-detail").textContent = intervalLabel;
    } else if (schedule.active === false) {
      byId("frame-schedule-label").textContent = resume ? `Resting until ${resume}` : "The frame is resting";
      byId("frame-schedule-detail").textContent = "The display keeps its last image.";
    } else {
      byId("frame-schedule-label").textContent = until ? `Updates until ${until}` : "Updates enabled";
      byId("frame-schedule-detail").textContent = intervalLabel;
    }
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }

  function renderDevice(state) {
    const device = state.device || {};
    const percentage = device.battery_percent;
    const voltage = device.battery_voltage;
    const validPercent = Number.isInteger(percentage) && percentage >= 0 && percentage <= 100;
    const validVoltage = typeof voltage === "number" && Number.isFinite(voltage) && voltage >= 2.5 && voltage <= 4.5;
    const reported = dateValue(device.reported_at);
    const seen = dateValue(device.last_seen);
    const resting = device.resting === true;
    const stale = device.stale === true;
    const zone = state.settings?.schedule?.timezone || "America/Denver";
    const stamp = (value) => {
      try { return value.toLocaleString([], { timeZone: zone, month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); }
      catch { return value.toLocaleString(); }
    };
    byId("device-percent").textContent = validPercent ? `${percentage}%` : "—";
    byId("device-charging").textContent = device.charging === "charging" ? "Charging"
      : device.charging === "not_charging" ? "Not charging" : "Charging status unknown";
    byId("device-voltage").hidden = !validVoltage;
    byId("device-voltage").textContent = validVoltage ? `${voltage.toFixed(2)} V` : "";
    byId("device-status").textContent = !reported ? "No reading yet" : resting ? "Overnight rest" : stale ? "Last seen earlier" : "Latest reading";
    byId("device-heading").closest("section").classList.toggle("stale", stale && !resting);
    byId("device-reported").textContent = reported ? `Updated ${stamp(reported)}`
      : "No battery reading yet.";
    byId("device-seen").hidden = !seen || (reported && seen.getTime() === reported.getTime());
    byId("device-seen").textContent = seen ? `Last connected ${stamp(seen)}` : "";
    const pictureConfirmed = dateValue(device.last_displayed_reported_at);
    byId("device-picture-status").hidden = device.power_control_supported !== true;
    byId("device-picture-status").textContent = device.last_displayed_etag && pictureConfirmed
      ? `${device.last_displayed_matches_current ? "Current picture" : "Earlier picture"} confirmed ${stamp(pictureConfirmed)}`
      : "Picture confirmation pending";
  }

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(url, { cache: "no-store", credentials: "same-origin", signal: controller.signal, ...options });
      let payload;
      try { payload = await response.json(); }
      catch { throw new Error("The picture service returned an unreadable response."); }
      if (!response.ok) throw new Error(payload.error || `The picture service returned ${response.status}.`);
      return payload;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("The picture service took too long to respond. Try again.");
      throw error;
    } finally { window.clearTimeout(timeout); }
  }

  function showServiceMessage(message) {
    byId("service-message").hidden = !message;
    byId("service-message-text").textContent = message || "";
  }

  function saveStatus(message, error = false) {
    byId("save-status").textContent = message;
    byId("save-status").classList.toggle("error", error);
  }

  function updateFormHelp() {
    for (const radio of document.querySelectorAll('input[name="presentation"]')) radio.checked = radio.value === draft.style;
    byId("presentation-options").classList.toggle("landscape", draft.orientation === "landscape");
    const classic = draft.style === "classic";
    byId("classic-media-field").hidden = !classic;
    byId("classic_media").disabled = !classic;
    byId("scale").disabled = classic;
    byId("scale").closest(".field").hidden = classic;
    const photos = classic && draft.classic_media !== "drawings";
    byId("variant-section").hidden = photos || !byId("variant-fields").children.length;
    for (const select of byId("variant-fields").querySelectorAll("select")) select.disabled = photos;
    byId("qr-settings").hidden = !draft.show_qr;
    byId("dashboard_url").required = draft.show_qr;
    byId("max-birds-value").value = draft.max_birds;
    byId("bird-limit-field").hidden = draft.show_all;
    byId("max_birds").disabled = draft.show_all;
    const explanation = {
      log: "Logarithmic scaling keeps less frequent species visible.",
      linear: "Bird size is proportional to detection count.",
      equal: "All species have the same size.",
    };
    byId("scale-help").textContent = explanation[draft.scale] || explanation.log;
    const customInterval = byId("refresh_interval").value === "custom";
    byId("refresh-interval-fields").classList.toggle("is-custom", customInterval);
    byId("refresh-interval-custom-field").hidden = !customInterval;
    byId("refresh_interval_minutes").disabled = !customInterval;
    byId("refresh_interval_minutes").required = customInterval;
    const meaningful = draft.update_mode === "meaningful";
    byId("meaningful-controls").hidden = !meaningful;
    byId("meaningful-controls").disabled = !meaningful;
    byId("meaningful-help").hidden = !meaningful;
    const maxMinutes = draft.meaningful_max_minutes;
    const duration = Number.isInteger(maxMinutes) && maxMinutes >= 30 && maxMinutes <= 720
      ? maxMinutes % 60 === 0 ? `${maxMinutes / 60} ${maxMinutes === 60 ? "hour" : "hours"}` : `${maxMinutes} minutes`
      : "the selected interval";
    byId("meaningful-help").textContent = `New species, size changes, or after ${duration}. Shown at the next check.`;
    byId("schedule-controls").disabled = !draft.schedule.enabled;
    for (const boundary of ["start", "end"]) {
      const fixed = draft.schedule[boundary].mode === "fixed";
      byId(`schedule_${boundary}_offset_field`).hidden = fixed;
      byId(`schedule_${boundary}_offset`).disabled = fixed;
      byId(`schedule_${boundary}_time_field`).hidden = !fixed;
      byId(`schedule_${boundary}_time`).disabled = !fixed;
    }
    const defaultLocation = Math.abs(draft.schedule.latitude - defaultSchedule.latitude) < .0001 && Math.abs(draft.schedule.longitude - defaultSchedule.longitude) < .0001;
    byId("schedule-location-note").textContent = defaultLocation
      ? "Salt Lake City · ZIP 84105"
      : "Sun times use these coordinates.";
  }

  function presentationSettings() {
    const settings = {};
    for (const key of [...booleans, "classic_media", "scale", "max_birds", "orientation", "variant_mode", "mirror_mode", "dashboard_url"])
      settings[key] = draft[key];
    settings.variants = { ...draft.variants };
    settings.mirror_overrides = { ...draft.mirror_overrides };
    return settings;
  }

  function queuePresentationPreviews(force = false) {
    if (!currentState?.stats || !csrfToken) return;
    const settings = presentationSettings();
    const key = JSON.stringify([currentState.revision, currentState.stats, settings]);
    if (key === presentationKey && !force) return;
    presentationKey = key;
    pendingPresentation = { key, settings, request: ++presentationRequest };
    window.clearTimeout(presentationTimer);
    byId("presentation-options").setAttribute("aria-busy", "true");
    byId("presentation-options").classList.add("is-loading");
    byId("presentation-options").classList.remove("is-stale");
    byId("presentation-status").textContent = "Updating previews…";
    byId("presentation-retry").hidden = true;
    presentationTimer = window.setTimeout(() => {
      presentationTimer = null;
      loadPresentationPreviews();
    }, 350);
  }

  function renderPreviewCredits(previews) {
    const list = byId("presentation-credit-list");
    list.replaceChildren();
    const seen = new Set();
    for (const preview of previews) for (const credit of preview.credits || []) {
      const key = JSON.stringify([credit.id, credit.scientific_name, credit.source_url, credit.title]);
      if (seen.has(key)) continue;
      seen.add(key);
      const row = node("li");
      row.append(node("strong", "", credit.common_name || credit.scientific_name || credit.title));
      row.append(node("span", "credit-note", credit.attribution || [credit.creator || credit.artist, credit.rights || credit.license].filter(Boolean).join(" · ")));
      if (credit.changes) row.append(node("span", "credit-note", credit.changes));
      for (const [label, value] of [["Source", credit.source_url], ["License", credit.rights_url || credit.license_url]]) {
        const href = safeUrl(value);
        if (!href) continue;
        const link = node("a", "", label);
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        row.append(link);
      }
      list.append(row);
    }
    byId("presentation-credits").hidden = !list.children.length;
  }

  async function loadPresentationPreviews() {
    if (presentationBusy || !pendingPresentation) return;
    const job = pendingPresentation;
    pendingPresentation = null;
    presentationBusy = true;
    try {
      const data = await request("/api/presentation-previews", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify(job.settings),
      });
      if (job.request !== presentationRequest) return;
      const previews = Array.isArray(data.previews) ? data.previews : [];
      let unavailable = false;
      for (const style of ["collage", "classic", "field_guide"]) {
        const preview = previews.find(item => item.style === style);
        const image = byId(`presentation-${style}`);
        const placeholder = image.nextElementSibling;
        const valid = typeof preview?.image_url === "string" && preview.image_url.startsWith("data:image/png;base64,");
        if (valid) image.src = preview.image_url;
        image.hidden = !valid;
        placeholder.hidden = valid;
        placeholder.textContent = "Preview unavailable";
        image.closest(".presentation-card").title = preview?.error || "";
        unavailable ||= !valid || Boolean(preview?.error);
      }
      renderPreviewCredits(previews);
      byId("presentation-options").classList.remove("is-stale");
      byId("presentation-status").textContent = unavailable ? "Some previews are unavailable." : "";
      byId("presentation-retry").hidden = !unavailable;
    } catch (error) {
      if (job.request !== presentationRequest) return;
      const previous = ["collage", "classic", "field_guide"].some(style => !byId(`presentation-${style}`).hidden);
      byId("presentation-options").classList.toggle("is-stale", previous);
      byId("presentation-status").textContent = previous ? "Couldn’t update previews. Showing earlier versions." : "Couldn’t load previews.";
      byId("presentation-retry").hidden = false;
      for (const placeholder of document.querySelectorAll(".presentation-placeholder")) placeholder.textContent = "Preview unavailable";
    } finally {
      presentationBusy = false;
      if (job.request === presentationRequest) {
        byId("presentation-options").setAttribute("aria-busy", "false");
        byId("presentation-options").classList.remove("is-loading");
      }
      if (pendingPresentation && presentationTimer === null) loadPresentationPreviews();
    }
  }

  function readForm() {
    for (const name of booleans) draft[name] = byId(name).checked;
    draft.style = byId("style").value;
    draft.classic_media = byId("classic_media").value;
    draft.scale = byId("scale").value;
    draft.variant_mode = byId("variant_mode").value;
    draft.mirror_mode = byId("mirror_mode").value;
    draft.orientation = byId("orientation").value;
    draft.max_birds = Number(byId("max_birds").value);
    draft.dashboard_url = byId("dashboard_url").value.trim();
    draft.refresh_interval_minutes = byId("refresh_interval").value === "custom"
      ? byId("refresh_interval_minutes").valueAsNumber : Number(byId("refresh_interval").value);
    if (byId("refresh_interval").value !== "custom") byId("refresh_interval_minutes").value = draft.refresh_interval_minutes;
    draft.stop_voltage = byId("stop_voltage").valueAsNumber;
    draft.update_mode = byId("update_mode").value;
    draft.meaningful_change_percent = byId("meaningful_change_percent").valueAsNumber;
    draft.meaningful_max_minutes = byId("meaningful_max_minutes").valueAsNumber;
    if (draft.update_mode !== "meaningful") {
      for (const [field, minimum, maximum] of [["meaningful_change_percent", 5, 50], ["meaningful_max_minutes", 30, 720]]) {
        if (!Number.isInteger(draft[field]) || draft[field] < minimum || draft[field] > maximum) {
          draft[field] = currentState?.settings?.[field] ?? defaults[field];
          byId(field).value = draft[field];
        }
      }
    }
    draft.schedule.enabled = byId("schedule_enabled").checked;
    draft.schedule.timezone = byId("schedule_timezone").value.trim();
    draft.schedule.latitude = Number(byId("schedule_latitude").value);
    draft.schedule.longitude = Number(byId("schedule_longitude").value);
    for (const boundary of ["start", "end"]) {
      draft.schedule[boundary] = {
        mode: byId(`schedule_${boundary}_mode`).value,
        offset_minutes: Number(byId(`schedule_${boundary}_offset`).value),
        time: byId(`schedule_${boundary}_time`).value,
      };
    }
    for (const select of byId("variant-fields").querySelectorAll("select[data-species]")) {
      if (select.value) draft.variants[select.dataset.species] = select.value;
      else delete draft.variants[select.dataset.species];
    }
    for (const select of byId("variant-fields").querySelectorAll("select[data-mirror-species]")) {
      if (select.value) draft.mirror_overrides[select.dataset.mirrorSpecies] = select.value;
      else delete draft.mirror_overrides[select.dataset.mirrorSpecies];
    }
    updateFormHelp();
  }

  function fillForm(settings) {
    draft = { ...defaults, ...settings, variants: { ...(settings?.variants || {}) }, mirror_overrides: { ...(settings?.mirror_overrides || {}) }, schedule: scheduleSettings(settings?.schedule) };
    for (const name of booleans) byId(name).checked = Boolean(draft[name]);
    for (const name of ["style", "classic_media", "scale", "orientation", "max_birds", "dashboard_url", "variant_mode", "mirror_mode", "stop_voltage", "update_mode", "meaningful_change_percent", "meaningful_max_minutes"]) byId(name).value = draft[name];
    byId("refresh_interval").value = [30, 60, 120].includes(draft.refresh_interval_minutes)
      ? String(draft.refresh_interval_minutes) : "custom";
    byId("refresh_interval_minutes").value = draft.refresh_interval_minutes;
    byId("schedule_enabled").checked = Boolean(draft.schedule.enabled);
    for (const field of ["timezone", "latitude", "longitude"]) byId(`schedule_${field}`).value = draft.schedule[field];
    for (const boundary of ["start", "end"]) {
      byId(`schedule_${boundary}_mode`).value = draft.schedule[boundary].mode;
      byId(`schedule_${boundary}_offset`).value = draft.schedule[boundary].offset_minutes;
      byId(`schedule_${boundary}_time`).value = draft.schedule[boundary].time;
    }
    updateFormHelp();
  }

  function catalogue(state) {
    return Array.isArray(state.artworks) ? state.artworks : (state.artworks?.assets || []);
  }

  function artworkId(artwork) { return String(artwork.id || artwork.artwork_id || ""); }
  function artworkSpecies(artwork) { return artwork.scientific_name || artwork.species || ""; }
  function artworkTitle(artwork) {
    return artwork.title || artwork.label || [artwork.common_name || artwork.scientific_name, artwork.creator, artwork.date].filter(Boolean).join(" · ") || artworkId(artwork);
  }

  function displaySettings(state) { return state.display_settings || state.settings || {}; }
  function displayStyle(state) { return displaySettings(state).style || "collage"; }

  function renderVariants(state) {
    const container = byId("variant-fields");
    container.replaceChildren();
    const assets = catalogue(state);
    for (const species of state.stats?.species || []) {
      const choices = assets.filter((artwork) => artworkSpecies(artwork) === species.scientific_name && artworkId(artwork));
      if (!choices.length) continue;
      const field = node("div", "field");
      const id = `artwork-variant-${container.children.length}`;
      const label = node("label", "", species.common_name || species.scientific_name);
      label.htmlFor = id;
      const select = node("select");
      select.id = id;
      select.dataset.species = species.scientific_name;
      const automatic = node("option", "", "Automatic selection");
      automatic.value = "";
      select.append(automatic);
      for (const artwork of choices) {
        const option = node("option", "", artworkTitle(artwork));
        option.value = artworkId(artwork);
        select.append(option);
      }
      select.value = draft.variants[species.scientific_name] || "";
      select.disabled = draft.style === "classic" && draft.classic_media !== "drawings";
      field.append(label, select);
      const direction = node("select");
      direction.dataset.mirrorSpecies = species.scientific_name;
      direction.id = `${id}-direction`;
      const directionLabel = node("label", "", "Image direction");
      directionLabel.htmlFor = direction.id;
      for (const [value, title] of [["", "Use default above"], ["original", "Original"], ["mirrored", "Mirrored left / right"], ["daily", "Random each day"]]) {
        const option = node("option", "", title); option.value = value; direction.append(option);
      }
      direction.value = draft.mirror_overrides[species.scientific_name] || "";
      direction.disabled = select.disabled;
      field.append(directionLabel, direction);
      container.append(field);
    }
    byId("variant-section").hidden = (draft.style === "classic" && draft.classic_media !== "drawings") || !container.children.length;
  }

  function hourlyValues(species) {
    const total = new Array(24).fill(0);
    let present = false;
    for (const bird of species) {
      const hours = bird.hourly_counts;
      if (!hours || typeof hours !== "object" || !Object.keys(hours).length) continue;
      present = true;
      if (Array.isArray(hours)) {
        hours.forEach((value, index) => {
          const hour = typeof value === "object" && value !== null ? Number(value.hour) : index;
          const detections = typeof value === "object" && value !== null ? value.count : value;
          if (Number.isInteger(hour) && hour >= 0 && hour < 24) total[hour] += count(detections);
        });
      } else {
        for (const [key, value] of Object.entries(hours)) {
          const hour = Number(key);
          if (Number.isInteger(hour) && hour >= 0 && hour < 24) total[hour] += count(value);
        }
      }
    }
    return present ? total : null;
  }

  function renderHourly(species) {
    const values = hourlyValues(species);
    byId("hourly-section").hidden = !values;
    if (!values) return;
    const chart = byId("hourly-chart");
    chart.replaceChildren();
    const maximum = Math.max(...values, 1);
    const accessible = [];
    values.forEach((value, hour) => {
      const track = node("div", "hour-track");
      track.title = `${String(hour).padStart(2, "0")}:00 · ${numberFormat.format(value)} detections`;
      const bar = node("span", "hour-bar");
      bar.style.height = `${100 * value / maximum}%`;
      track.append(bar);
      chart.append(track);
      if (value) accessible.push(`${hour}:00, ${value}`);
    });
    chart.setAttribute("aria-label", `Hourly detections: ${accessible.length ? accessible.join("; ") : "none"}.`);
  }

  function renderSpecies(species, state) {
    const container = byId("species-list");
    const expanded = new Set(Array.from(container.querySelectorAll("details[open]"), (item) => item.dataset.species));
    container.replaceChildren();
    byId("species-empty").hidden = species.length > 0;
    byId("species-details").hidden = species.length === 0;
    const largest = Math.max(...species.map((bird) => count(bird.count)), 1);
    for (const bird of species) {
      const row = node("details", "species-row");
      row.dataset.species = bird.scientific_name;
      row.open = expanded.has(bird.scientific_name);
      const summary = node("summary");
      const identity = node("span", "species-identity");
      identity.append(node("span", "species-name", bird.common_name || bird.scientific_name));
      if (bird.scientific_name) identity.append(node("span", "species-latin", bird.scientific_name));
      const detections = node("span", "species-count", numberFormat.format(count(bird.count)));
      detections.setAttribute("aria-label", `${count(bird.count)} detections`);
      summary.append(identity, detections);
      const meta = node("div", "species-meta");
      const latest = timeLabel(bird.latest_heard);
      if (latest) meta.append(node("span", "", `Last heard ${latest}`));
      if (bird.max_confidence !== null && bird.max_confidence !== undefined) {
        const confidence = Number(bird.max_confidence);
        if (Number.isFinite(confidence)) meta.append(node("span", "", `Best confidence ${Math.round(confidence <= 1 ? confidence * 100 : confidence)}%`));
      }
      if (displayStyle(state) !== "classic") {
        if (bird.artwork_available === false) meta.append(node("span", "missing-art", "Illustration unavailable"));
        else if (bird.illustrated === false) meta.append(node("span", "", "Not shown in this layout"));
      } else if (displaySettings(state).classic_media === "drawings" && bird.displayed === true && bird.artwork_available === false) {
        meta.append(node("span", "missing-art", "Illustration unavailable"));
      }
      const bar = node("div", "species-frequency");
      bar.setAttribute("aria-hidden", "true");
      const fill = node("span");
      fill.style.width = `${count(bird.count) / largest * 100}%`;
      bar.append(fill);
      row.append(summary, meta, bar);
      const explore = node("button", "text-button explore-bird", "Bird details ↗");
      explore.type = "button";
      explore.addEventListener("click", () => window.dispatchEvent(new CustomEvent("federfunk:bird", {
        detail: { scientific_name: bird.scientific_name, date: state.stats?.date },
      })));
      row.append(explore);
      container.append(row);
    }
  }

  function renderCredits(state) {
    const container = byId("artwork-credits");
    container.replaceChildren();
    const used = new Set(((state.display_stats || state.stats)?.species || []).map((bird) => bird.artwork_id).filter(Boolean));
    const classic = displayStyle(state) === "classic";
    const photos = classic && displaySettings(state).classic_media !== "drawings";
    const credits = classic ? (Array.isArray(state.photo_credits) ? state.photo_credits : [])
      : catalogue(state).filter((item) => used.has(artworkId(item)));
    byId("sources-heading").textContent = "Image credits";
    byId("sources-heading").append(node("span", "muted", "Sources & licenses"));
    byId("credits-description").textContent = photos
      ? "Credits for the photograph in this print."
      : "Credits for the illustrations in this print.";
    const printRights = state.print_rights;
    if (printRights?.rights) {
      byId("credits-description").append(document.createTextNode(" This composed print is shared under "));
      const licenseUrl = safeUrl(printRights.rights_url);
      const license = node(licenseUrl ? "a" : "span", "", printRights.rights);
      if (licenseUrl) { license.href = licenseUrl; license.target = "_blank"; license.rel = "noopener noreferrer"; }
      byId("credits-description").append(license, document.createTextNode(". " + (printRights.changes || "")));
    }
    for (const artwork of credits) {
      const item = node("li");
      const source = safeUrl(artwork.source_url || artwork.source || artwork.url);
      const name = artwork.common_name || artwork.scientific_name || (photos ? "Bird photograph" : "Bird illustration");
      const title = node(source ? "a" : "span", "credit-title", `${name} · ${artworkTitle(artwork)}`);
      if (source) { title.href = source; title.target = "_blank"; title.rel = "noopener noreferrer"; }
      const details = [artwork.artist || artwork.creator, artwork.year || artwork.date, artwork.license || artwork.rights,
        photos ? artwork.source_provider : null].filter(Boolean).join(" · ");
      item.append(title);
      if (details) item.append(node("span", "credit-note", details));
      if (typeof artwork.attribution === "string" && artwork.attribution) item.append(node("span", "credit-note", artwork.attribution));
      if (typeof artwork.changes === "string" && artwork.changes) item.append(node("span", "credit-note", `Image changes: ${artwork.changes}`));
      const links = node("div", "credit-links");
      const included = new Set();
      for (const [label, value] of [["Source record", source], ["Archive", artwork.archive_url],
        ["Prepared drawing", artwork.upstream_url],
        ["Photographer", photos ? artwork.author_url : null],
        [photos ? "Original photo" : "Original scan", artwork.original_image_url || artwork.image_url], ["Rights & license", artwork.rights_url || artwork.license_url]]) {
        const href = safeUrl(value);
        if (!href || included.has(href)) continue;
        included.add(href);
        const link = node("a", "", label);
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.setAttribute("aria-label", `${label} for ${name}`);
        links.append(link);
      }
      if (links.children.length) item.append(links);
      container.append(item);
    }
    byId("sources-section").hidden = !container.children.length;
  }

  function renderImage(state) {
    const revision = state.revision ?? state.last_success ?? "current";
    const key = `${imageMode}-${revision}`;
    byId("art-mode").setAttribute("aria-pressed", String(imageMode === "art"));
    byId("paper-mode").setAttribute("aria-pressed", String(imageMode === "paper"));
    const classic = displayStyle(state) === "classic";
    const photos = classic && displaySettings(state).classic_media !== "drawings";
    byId("art-mode").textContent = photos ? "Photo" : "Artwork";
    byId("picture").alt = classic ? `A featured bird ${photos ? "photograph" : "drawing"} and the day’s detection summary`
      : displayStyle(state) === "field_guide" ? "Today’s birds arranged as a field-guide plate"
      : "Today’s birds, arranged as a natural history illustration";
    const source = `${imageMode === "paper" ? "/preview.png" : "/art.png"}?v=${encodeURIComponent(revision)}`;
    byId("download-image").href = source;
    byId("download-image").download = imageMode === "paper" ? "heard-today-epaper.png" : "heard-today.png";
    byId("picture-paper").classList.toggle("landscape", (state.display_settings || state.settings)?.orientation === "landscape");
    byId("preview-note").textContent = imageMode === "paper"
      ? "Six-color preview. Colors on the physical display may differ."
      : "";
    if (key === imageKey) return;
    imageKey = key;
    const requestId = ++imageRequest;
    const image = new Image();
    image.onload = () => {
      if (requestId !== imageRequest) return;
      displayedMode = imageMode;
      byId("picture").src = source;
      byId("picture").hidden = false;
      byId("picture-placeholder").hidden = true;
      window.dispatchEvent(new CustomEvent("federfunk:image", { detail: state }));
    };
    image.onerror = () => {
      if (requestId !== imageRequest) return;
      imageKey = "";
      if (!byId("picture").hidden) {
        imageMode = displayedMode;
        byId("art-mode").setAttribute("aria-pressed", String(imageMode === "art"));
        byId("paper-mode").setAttribute("aria-pressed", String(imageMode === "paper"));
        byId("download-image").href = byId("picture").src;
        byId("download-image").download = imageMode === "paper" ? "heard-today-epaper.png" : "heard-today.png";
        byId("preview-note").textContent = "Couldn’t load the latest image. Showing the previous version.";
        return;
      }
      byId("picture-placeholder").classList.add("error");
      byId("picture-placeholder-text").textContent = "Artwork unavailable. Try refreshing the page.";
    };
    image.src = source;
  }

  function renderState(state, replaceDraft = false) {
    currentState = state;
    const stats = state.stats || {};
    const species = Array.isArray(stats.species) ? stats.species : [];
    byId("demo-badge").hidden = !state.demo;
    byId("species-total").textContent = stats.species_count === undefined ? "—" : numberFormat.format(count(stats.species_count));
    byId("detection-total").textContent = stats.total_detections === undefined ? "—" : numberFormat.format(count(stats.total_detections));
    const threshold = Number(stats.confidence_threshold);
    byId("confidence-note").hidden = stats.confidence_threshold === undefined || !Number.isFinite(threshold);
    byId("confidence-note").textContent = `Confidence threshold: ${Math.round(threshold * 100)}%.`;
    const day = dateValue(/^\d{4}-\d{2}-\d{2}$/.test(stats.date || "") ? `${stats.date}T12:00:00` : stats.date);
    byId("collection-date").textContent = day ? day.toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" }) : "Today";
    const updated = dateValue(state.last_success);
    byId("collection-date").title = updated ? `Updated ${updated.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` : "";
    if (replaceDraft || !dirty) fillForm(state.settings || {});
    renderSpecies(species, state);
    if (!state.stats) byId("species-empty").textContent = "Waiting for detections.";
    else byId("species-empty").textContent = "No detections yet.";
    renderHourly(species);
    renderVariants(state);
    renderCredits(state);
    renderImage(state);
    renderSchedule(state);
    renderDevice(state);
    queuePresentationPreviews();
    window.dispatchEvent(new CustomEvent("federfunk:state", { detail: state }));
    showServiceMessage(state.error ? `The collection could not be updated: ${state.error}. Showing the last available picture and detections.` : "");
  }

  async function refresh() {
    if (refreshing || saving) return;
    refreshing = true;
    try {
      if (!ready) {
        const [state, configuration] = await Promise.all([request("/api/state"), request("/api/settings")]);
        csrfToken = configuration.csrf_token || "";
        if (!csrfToken) throw new Error("The service did not provide a settings token. Reload the page to try again.");
        renderState({ ...state, settings: state.settings || configuration.settings }, true);
        ready = true;
        byId("settings-fields").disabled = false;
        saveStatus("");
      } else {
        renderState(await request("/api/state"));
      }
    } catch (error) {
      showServiceMessage(currentState ? `The service is unavailable. Showing the last loaded collection. ${error.message}` : `Could not load your collection. ${error.message}`);
      if (!ready) {
        saveStatus("Settings are unavailable until the service reconnects.", true);
        byId("collection-date").textContent = "Collection unavailable";
        byId("picture-placeholder").classList.add("error");
        byId("picture-placeholder-text").textContent = "Your picture will appear when the service reconnects.";
      }
    } finally { refreshing = false; }
  }

  byId("settings-form").addEventListener("input", (event) => {
    if (event.target.name === "presentation") byId("style").value = event.target.value;
    readForm();
    queuePresentationPreviews();
    dirty = true;
    byId("apply-button").disabled = !ready || saving;
    saveStatus("Unsaved changes");
  });
  byId("stop_voltage").addEventListener("invalid", () => { byId("power-controls").open = true; });

  byId("settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!ready || saving || !dirty) return;
    readForm();
    if (!Number.isInteger(draft.refresh_interval_minutes) || draft.refresh_interval_minutes < 5 || draft.refresh_interval_minutes > 720) {
      saveStatus("Enter a whole number from 5 to 720 minutes.", true);
      byId("refresh_interval_minutes").focus();
      return;
    }
    if (!Number.isFinite(draft.stop_voltage) || draft.stop_voltage < 3.2 || draft.stop_voltage > 3.6 || Math.abs(draft.stop_voltage * 20 - Math.round(draft.stop_voltage * 20)) > .0001) {
      saveStatus("Choose 3.20–3.60 V in steps of 0.05 V.", true);
      byId("power-controls").open = true;
      byId("stop_voltage").focus();
      return;
    }
    if (draft.update_mode === "meaningful") {
      for (const [field, minimum, maximum, message] of [
        ["meaningful_change_percent", 5, 50, "Enter a size change from 5 to 50%."],
        ["meaningful_max_minutes", 30, 720, "Enter a whole number from 30 to 720 minutes."],
      ]) {
        if (!Number.isInteger(draft[field]) || draft[field] < minimum || draft[field] > maximum) {
          saveStatus(message, true);
          byId(field).focus();
          return;
        }
      }
    }
    if (draft.show_qr && !safeUrl(draft.dashboard_url)) {
      saveStatus("Enter a complete http:// or https:// dashboard address for the QR link.", true);
      byId("dashboard_url").focus();
      return;
    }
    if (draft.schedule.enabled) {
      try { new Intl.DateTimeFormat("en-US", { timeZone: draft.schedule.timezone }).format(); }
      catch {
        saveStatus("Enter a valid IANA time zone, such as America/Denver.", true);
        byId("schedule_timezone").closest("details").open = true;
        byId("schedule_timezone").focus();
        return;
      }
    }
    const intervalChanged = draft.refresh_interval_minutes !== (currentState?.settings?.refresh_interval_minutes ?? defaults.refresh_interval_minutes);
    const stopVoltageChanged = draft.stop_voltage !== (currentState?.settings?.stop_voltage ?? defaults.stop_voltage);
    saving = true;
    byId("settings-fields").disabled = true;
    byId("apply-button").disabled = true;
    saveStatus("Applying…");
    try {
      const state = await request("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify(draft),
      });
      dirty = false;
      renderState(state, true);
      saveStatus(state.preview_only ? "Preview updated"
        : (intervalChanged && state.device?.refresh_control_supported !== true) || (stopVoltageChanged && state.device?.power_control_supported !== true)
        ? "Saved · Ready for the next firmware version."
        : state.schedule?.active === false
        ? "Saved · Display updates when the schedule resumes."
        : "Saved · Display updates at its next scheduled wake.");
    } catch (error) {
      saveStatus(`Could not apply changes: ${error.message}`, true);
    } finally {
      saving = false;
      byId("settings-fields").disabled = false;
      byId("apply-button").disabled = !dirty;
    }
  });

  for (const [id, mode] of [["art-mode", "art"], ["paper-mode", "paper"]]) {
    byId(id).addEventListener("click", () => {
      imageMode = mode;
      if (currentState) renderImage(currentState);
    });
  }
  byId("retry-button").addEventListener("click", refresh);
  byId("presentation-retry").addEventListener("click", () => queuePresentationPreviews(true));
  let libraryReport = null;
  let libraryLoading = false;
  function renderLibrary() {
    if (!libraryReport) return;
    const query = byId("library-search").value.toLowerCase().trim();
    const container = byId("library-list");
    container.replaceChildren();
    for (const row of libraryReport.rows) {
      if (query && !`${row.common_name} ${row.scientific_name}`.toLowerCase().includes(query)) continue;
      const item = node("div", "library-row");
      if (row.artwork_ids.length) {
        const artworkLink = node("a");
        const thumbnail = node("img", "library-bird");
        thumbnail.src = `/artwork/${encodeURIComponent(row.artwork_ids[0])}.png`;
        thumbnail.alt = row.common_name;
        thumbnail.loading = "lazy";
        artworkLink.href = thumbnail.src;
        artworkLink.target = "_blank";
        artworkLink.rel = "noopener";
        artworkLink.title = `Open the ${row.common_name} illustration`;
        artworkLink.append(thumbnail);
        item.append(artworkLink);
      } else item.append(node("span", "library-bird missing-art", "—"));
      const description = node("div");
      description.append(node("strong", "", row.common_name), node("div", "muted small", row.scientific_name));
      const status = !row.covered ? "Artwork needed" : row.artwork_reviewed ? "Reviewed illustration" : "Illustration awaiting review";
      description.append(node("div", "small", status + (row.max_confidence < .8 ? " · Low-confidence identification" : "")));
      description.append(node("div", "small muted", `${numberFormat.format(row.count)} recorded detections`));
      item.append(description);
      container.append(item);
    }
  }
  byId("library-search").addEventListener("input", renderLibrary);
  byId("library-details").addEventListener("toggle", async () => {
    if (!byId("library-details").open || libraryReport || libraryLoading) return;
    libraryLoading = true;
    byId("library-summary").textContent = "Loading species…";
    try {
      libraryReport = await request("/api/library");
      byId("library-summary").textContent = `${libraryReport.covered_species} of ${libraryReport.species_count} species illustrated · ${Number(libraryReport.detection_coverage_percent).toFixed(1)}% of detections covered`;
      renderLibrary();
    } catch (error) {
      byId("library-summary").textContent = `${error.message}. Close and reopen this section to retry.`;
    } finally { libraryLoading = false; }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  window.setInterval(() => { if (!document.hidden) refresh(); }, 30000);
  refresh();
})();
