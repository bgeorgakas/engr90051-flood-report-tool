// Read-only map of submitted reports and their candidate inlets.

const map = L.map("map").setView([-37.8105, 144.9615], 15);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors | Pits: City of Melbourne (CC BY 4.0) | Catchment: Melbourne Water (CC BY 4.0)",
}).addTo(map);

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const inletsLayer = L.layerGroup();
const reportsLayer = L.layerGroup().addTo(map);
const candidatesLayer = L.layerGroup().addTo(map);
L.control.layers(null, { "Flood reports": reportsLayer, "All stormwater inlets": inletsLayer }).addTo(map);

fetch("/api/catchment")
  .then((r) => r.json())
  .then((geojson) => {
    const outline = L.geoJSON(geojson, { style: { color: "#2563eb", weight: 2, fill: false, dashArray: "6 4" } }).addTo(map);
    map.fitBounds(outline.getBounds());
  });

fetch("/api/inlets")
  .then((r) => r.json())
  .then((geojson) =>
    L.geoJSON(geojson, {
      pointToLayer: (feat, latlng) =>
        L.circleMarker(latlng, { radius: 2, color: "#64748b", weight: 1 })
          .bindTooltip(`Pit ${feat.properties.asset_number} · ${escapeHtml(feat.properties.type)}`),
    }).addTo(inletsLayer)
  );

function popupHtml(report) {
  const when = new Date(report.captured_at || report.received_at).toLocaleString("en-AU");
  const where =
    report.location_source === "gps"
      ? `GPS ±${Math.round(report.accuracy_m)} m`
      : "Pin placed manually";
  const rows = report.candidates.length
    ? report.candidates
        .map(
          (c, i) =>
            `<tr><td>${i + 1}</td><td>${c.asset_number}</td><td>${escapeHtml(c.type)}</td>` +
            `<td>${c.distance_m} m</td><td>${Math.round(c.score * 100)}%</td></tr>`
        )
        .join("")
    : `<tr><td colspan="5">No inlets within the search radius</td></tr>`;
  return `
    <div class="report-popup">
      <a href="/api/reports/${report.id}/photo" target="_blank" rel="noopener">
        <img src="/api/reports/${report.id}/photo" alt="Report ${report.id} photo">
      </a>
      <p><strong>Report #${report.id}</strong> · ${when}<br>${where}</p>
      <table>
        <thead><tr><th>#</th><th>Pit</th><th>Type</th><th>Dist.</th><th>Score</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function showCandidates(report) {
  candidatesLayer.clearLayers();
  const origin = [report.lat, report.lon];
  report.candidates.forEach((c, i) => {
    L.polyline([origin, [c.lat, c.lon]], { color: "#f97316", weight: 1, dashArray: "3 3" }).addTo(candidatesLayer);
    L.circleMarker([c.lat, c.lon], {
      radius: 6,
      color: i === 0 ? "#c2410c" : "#f97316",
      fillOpacity: 0.3 + 0.7 * c.score,
      weight: 2,
    })
      .bindTooltip(`#${i + 1} · Pit ${c.asset_number} · ${Math.round(c.score * 100)}%`)
      .addTo(candidatesLayer);
  });
}

fetch("/api/reports")
  .then((r) => r.json())
  .then((reports) => {
    reports.forEach((report) => {
      L.circleMarker([report.lat, report.lon], {
        radius: 8,
        color: "#1d4ed8",
        fillColor: report.candidates.length ? "#3b82f6" : "#dc2626",
        fillOpacity: 0.9,
        weight: 2,
      })
        .bindPopup(popupHtml(report), { maxWidth: 320 })
        .on("popupopen", () => showCandidates(report))
        .on("popupclose", () => candidatesLayer.clearLayers())
        .addTo(reportsLayer);
    });
  });
