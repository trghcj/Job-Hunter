// Google sign-in: Firebase gives a one-time ID token; the server checks it and starts
// its own session. The browser then signs out of Firebase (the server session is what counts).
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js";
import {
  getAuth, GoogleAuthProvider, signInWithPopup, signOut,
} from "https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js";

const button = document.getElementById("signin");
const status = document.getElementById("signin-status");
const cfg = button.dataset;
const auth = getAuth(initializeApp({
  apiKey: cfg.apiKey, authDomain: cfg.authDomain, projectId: cfg.projectId, appId: cfg.appId,
}));

function say(text) { status.textContent = text; }

button.addEventListener("click", async () => {
  button.disabled = true;
  say("Opening Google…");
  try {
    const result = await signInWithPopup(auth, new GoogleAuthProvider());
    const idToken = await result.user.getIdToken();
    say("Signing you in…");
    const response = await fetch("/auth/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ idToken, csrf: cfg.csrf }),
    });
    const data = await response.json().catch(() => ({}));
    await signOut(auth);
    if (data.next) { window.location.assign(data.next); return; }
    say(data.error || "Sign-in failed, please try again.");
  } catch (err) {
    const cancelled = err && (err.code === "auth/popup-closed-by-user"
      || err.code === "auth/cancelled-popup-request");
    say(cancelled ? "Sign-in was cancelled." : "Sign-in failed, please try again.");
  }
  button.disabled = false;
});
