const form = document.getElementById("login-form");
const alertEl = document.getElementById("login-alert");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = form.querySelector("button");
  btn.disabled = true;
  try {
    const res = await fetch("api/login", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: document.getElementById("pw").value }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) { location.reload(); return; }
    alertEl.innerHTML = "";
    const d = document.createElement("div");
    d.className = "alert alert-error";
    d.textContent = data.error || "Couldn't sign in.";
    alertEl.append(d);
  } finally {
    btn.disabled = false;
  }
});
