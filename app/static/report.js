// Reporter page: capture a photo and a location, then submit both.

const ELIZABETH_ST = [-37.8136, 144.9631];
const MAX_PHOTO_PX = 1600;

const state = {
  photo: null,        // Blob (resized JPEG)
  capturedAt: null,   // ISO string
  latlng: null,       // L.LatLng
  accuracy: null,     // metres, GPS only
  source: null,       // "gps" | "manual"
};

const el = (id) => document.getElementById(id);

// --- Map and location -------------------------------------------------------

const map = L.map("map").setView(ELIZABETH_ST, 16);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

fetch("/api/catchment")
  .then((r) => r.json())
  .then((geojson) =>
    L.geoJSON(geojson, { style: { color: "#2563eb", weight: 2, fill: false, dashArray: "6 4" } }).addTo(map)
  );

const marker = L.marker(ELIZABETH_ST, { draggable: true });
const accuracyCircle = L.circle(ELIZABETH_ST, { radius: 0, weight: 1, fillOpacity: 0.1 });
let watchId = null;

function setLocation(latlng, source, accuracy = null) {
  state.latlng = latlng;
  state.source = source;
  state.accuracy = accuracy;
  marker.setLatLng(latlng).addTo(map);
  if (source === "gps") {
    accuracyCircle.setLatLng(latlng).setRadius(accuracy).addTo(map);
    el("location-status").textContent = `Using your GPS location (±${Math.round(accuracy)} m)`;
    el("use-gps").hidden = true;
  } else {
    accuracyCircle.remove();
    el("location-status").textContent = "Using the location you placed on the map";
    el("use-gps").hidden = !("geolocation" in navigator);
  }
  updateSubmit();
}

function startGps() {
  if (!("geolocation" in navigator)) {
    el("location-status").textContent = "Location isn't available. Tap the map to place your location.";
    return;
  }
  let firstFix = true;
  watchId = navigator.geolocation.watchPosition(
    (pos) => {
      const latlng = L.latLng(pos.coords.latitude, pos.coords.longitude);
      setLocation(latlng, "gps", pos.coords.accuracy);
      if (firstFix) {
        map.setView(latlng, 18);
        firstFix = false;
      }
    },
    () => {
      if (state.source !== "manual") {
        el("location-status").textContent = "Couldn't get your location. Tap the map to place it.";
      }
    },
    { enableHighAccuracy: true, maximumAge: 10000, timeout: 20000 }
  );
}

function switchToManual(latlng) {
  if (watchId !== null) {
    navigator.geolocation.clearWatch(watchId);
    watchId = null;
  }
  setLocation(latlng, "manual");
}

marker.on("dragend", () => switchToManual(marker.getLatLng()));
map.on("click", (e) => switchToManual(e.latlng));
el("use-gps").addEventListener("click", () => {
  el("location-status").textContent = "Finding your location…";
  startGps();
});

// --- Photo ------------------------------------------------------------------

async function resizePhoto(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_PHOTO_PX / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.8));
}

el("photo-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  state.capturedAt = new Date().toISOString();
  try {
    state.photo = await resizePhoto(file);
  } catch {
    state.photo = file; // Browser couldn't decode it; upload the original.
  }
  const preview = el("photo-preview");
  preview.src = URL.createObjectURL(state.photo);
  preview.hidden = false;
  updateSubmit();
});

// --- Submit -----------------------------------------------------------------

function updateSubmit() {
  el("submit").disabled = !(state.photo && state.latlng);
}

el("submit").addEventListener("click", async () => {
  const button = el("submit");
  button.disabled = true;
  button.textContent = "Sending…";
  el("error").hidden = true;

  const form = new FormData();
  form.append("photo", state.photo, "photo.jpg");
  form.append("lat", state.latlng.lat);
  form.append("lon", state.latlng.lng);
  form.append("location_source", state.source);
  if (state.accuracy !== null) form.append("accuracy_m", state.accuracy);
  form.append("captured_at", state.capturedAt);

  try {
    const resp = await fetch("/api/reports", { method: "POST", body: form });
    if (!resp.ok) throw new Error(`Server responded ${resp.status}`);
    el("form-view").hidden = true;
    el("thanks-view").hidden = false;
  } catch (err) {
    el("error").textContent = `Sorry, the report couldn't be sent (${err.message}). Please try again.`;
    el("error").hidden = false;
    button.disabled = false;
  } finally {
    button.textContent = "Submit report";
  }
});

el("another").addEventListener("click", () => window.location.reload());

startGps();
