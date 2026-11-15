const $ = (id) => document.getElementById(id);
let selectedSlot = null;

function apiKey() { return $("apiKey").value.trim(); }
async function api(path, options = {}) {
  const headers = {"X-API-Key": apiKey(), ...(options.headers || {})};
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...options, headers});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status})`);
    error.status = response.status;
    error.retryAfter = response.headers.get("Retry-After");
    throw error;
  }
  return data;
}
function message(text, type="") {
  $("notice").textContent = text;
  $("notice").className = `notice ${type}`;
}
function dateLabel(value) {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString([], {dateStyle:"medium",timeStyle:"short"});
}
async function loadSlots() {
  $("slots").innerHTML = '<p class="muted">Loading slots…</p>';
  try {
    const data = await api("/slots");
    $("slots").replaceChildren();
    if (!data.slots.length) {
      $("slots").innerHTML = '<p class="muted">No appointment slots are available yet.</p>';
      return;
    }
    data.slots.forEach(slot => {
      const item = document.createElement("div"); item.className = "slot";
      const details = document.createElement("div");
      const provider = document.createElement("strong"); provider.textContent = slot.provider;
      const time = document.createElement("span"); time.textContent = `${dateLabel(slot.start)} – ${dateLabel(slot.end)}`;
      const status = document.createElement("span"); status.textContent = slot.available ? "Available" : "Booked";
      details.append(provider,time,status);
      const button = document.createElement("button"); button.className = "button"; button.textContent = slot.available ? "Select" : "Unavailable";
      button.disabled = !slot.available;
      button.addEventListener("click", () => {
        selectedSlot = slot.id; $("selectedSlot").value = `${slot.provider} · ${dateLabel(slot.start)} (${slot.id})`;
        message(""); $("confirmation").hidden = true;
      });
      item.append(details,button); $("slots").append(item);
    });
  } catch (e) { $("slots").innerHTML = `<p class="notice error">${escapeHtml(e.message)}</p>`; }
}
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
$("saveKey").addEventListener("click", loadSlots);
$("refresh").addEventListener("click", loadSlots);
$("bookingForm").addEventListener("submit", async e => {
  e.preventDefault();
  if (!selectedSlot) return message("Please select an available appointment slot.", "error");
  const body = {slot_id:selectedSlot,name:$("patientName").value,email:$("patientEmail").value};
  const key = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
  try {
    const booking = await api("/bookings", {method:"POST",headers:{"Idempotency-Key":key},body:JSON.stringify(body)});
    $("confirmation").hidden = false;
    $("confirmation").innerHTML = `<strong>Appointment confirmed</strong>Booking reference: ${escapeHtml(booking.id)}<br>Name: ${escapeHtml(booking.name)}<br>Email: ${escapeHtml(booking.email)}<br>Status: ${escapeHtml(booking.status)}`;
    message("Your appointment has been booked.", "success");
    selectedSlot = null; $("selectedSlot").value = "";
    await loadSlots();
  } catch (err) {
    message(`${err.message}${err.retryAfter ? ` Please retry in ${err.retryAfter} seconds.` : ""}`, "error");
    await loadSlots();
  }
});
$("historyForm").addEventListener("submit", async e => {
  e.preventDefault();
  const email = $("historyEmail").value.trim();
  const target = $("history"); target.innerHTML = '<p class="muted">Loading bookings…</p>';
  try {
    const data = await api(`/bookings?email=${encodeURIComponent(email)}&limit=10`);
    target.replaceChildren();
    if (!data.bookings.length) target.innerHTML = '<p class="muted">No bookings found.</p>';
    data.bookings.forEach(renderHistoryItem);
    if (data.next_cursor) {
      const more = document.createElement("button"); more.className="button secondary"; more.textContent="Load more";
      more.addEventListener("click", () => loadMore(email, data.next_cursor, more)); target.append(more);
    }
  } catch (err) { target.textContent = err.message; }
});
function renderHistoryItem(booking) {
  const row = document.createElement("div"); row.className = "history-item";
  const info = document.createElement("div");
  info.innerHTML = `<strong>${escapeHtml(booking.name)}</strong><br><span>Ref: ${escapeHtml(booking.id)} · Slot: ${escapeHtml(booking.slot_id)} · ${escapeHtml(booking.status)}</span>`;
  row.append(info);
  if (booking.status === "confirmed") {
    const cancel = document.createElement("button"); cancel.className="button secondary"; cancel.textContent="Cancel";
    cancel.addEventListener("click", async () => {
      try { await api(`/bookings/${encodeURIComponent(booking.id)}`, {method:"DELETE"}); message("Booking cancelled.", "success"); $("historyForm").requestSubmit(); await loadSlots(); }
      catch(err) { message(err.message,"error"); }
    });
    row.append(cancel);
  }
  $("history").append(row);
}
async function loadMore(email, cursor, button) {
  button.disabled = true;
  try {
    const data = await api(`/bookings?email=${encodeURIComponent(email)}&limit=10&cursor=${encodeURIComponent(cursor)}`);
    button.remove(); data.bookings.forEach(renderHistoryItem);
    if (data.next_cursor) {
      const more = document.createElement("button"); more.className="button secondary"; more.textContent="Load more";
      more.addEventListener("click", () => loadMore(email, data.next_cursor, more)); $("history").append(more);
    }
  } catch(err) { button.disabled=false; message(err.message,"error"); }
}
