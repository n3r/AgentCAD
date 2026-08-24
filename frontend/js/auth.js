// Hosted-mode identity: the sign-in / enrolment view and the identity chip.
//
// Inert in local mode. `session()` answers 404 there — the route pack refuses
// when there is no security config — and `boot()` treats that exactly as it
// treats "authenticated": show the workbench. So a local user never meets any
// of this, which is the same "local mode is unchanged" property the server
// side has.
//
// No bundler, no new vendor: one module, plain DOM, the same `request()`
// funnel every other panel uses.

import { api, ApiError } from "./api.js";

const ENROL_PREFIX = "/api/auth/enrol/";

/** The enrolment token when the browser landed on an enrol URL, else null.
 *  The admin CLI prints `<origin>/api/auth/enrol/<token>`; a human pastes it,
 *  the server serves index.html for an HTML request, and this reads the token
 *  back out of the path. */
export function enrolToken() {
  const path = location.pathname;
  if (!path.startsWith(ENROL_PREFIX)) return null;
  const token = decodeURIComponent(path.slice(ENROL_PREFIX.length)).trim();
  return token || null;
}

/** `{principal, kind, role, mode}`, or null when nobody is signed in.
 *  Local mode answers 404 and reports `{mode: "local"}` so one call decides
 *  the whole boot path.
 *
 *  On a signed-in hosted session this ALSO carries the tenancy fields —
 *  `org`, `workspace`, `orgs`, `roles` (`{project: role}` in the resolved
 *  workspace) and `scope` — because `GET /api/auth/session` does not have
 *  them (`routes_auth.py`'s `_identity` is byte-for-byte 005a's four keys)
 *  while the `whoami` TOOL does (`tools_cloud.py`'s `_extend_whoami`): it is
 *  the cheapest call that already exists for exactly this, one round trip
 *  behind the session's own, and it is a no-op subset of `base` on an
 *  instance with no orgs — so `boot()`'s one `auth.session()` call stays the
 *  single source every panel (the switcher, the role affordances) reads. */
export async function session() {
  let base;
  try {
    base = await api.session();
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return { mode: "local", principal: null };
    }
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
  if (!base || !base.principal) return base;
  try {
    const who = await api.callTool("whoami", {});
    if (who && !who.error) return { ...base, ...who };
  } catch {
    /* offline, or the tool call itself failed: the session-only identity is
       still a valid answer — the switcher and the role gate simply see no
       orgs, same as an untenanted instance. */
  }
  return base;
}

/** The stripping convention every principal-showing surface uses — lock
 *  chips (`main.js`), the presence roster (`presence.js`), review threads
 *  (`comments.js`), proposals (`proposals.js`) and version/release history
 *  (`versions.js`, `releases.js`). `user:nikita` (or the device-suffixed
 *  `user:nikita/browser:7f3a1b2c` a session's principal carries) reads as
 *  `nikita`; `agent:mcp:claude` reads as `claude (agent)` — the LAST
 *  colon-separated segment, because that is the name a human actually
 *  recognizes, not the transport (`mcp`/`chat`) in front of it. Anything else
 *  — a local-mode `browser:7f3a1b2c` id, a presence nickname, `"someone"` —
 *  is returned unchanged: this is a display convention, not a parser, and a
 *  shape it does not recognize is not this function's to guess at. The full
 *  string stays available as a tooltip everywhere this is used, the same
 *  `renderChip` precedent below. */
export function displayPrincipal(principal) {
  if (typeof principal !== "string" || !principal) return principal;
  if (principal.startsWith("user:")) return principal.slice(5).split("/")[0];
  if (principal.startsWith("agent:")) {
    const rest = principal.slice(6);
    const name = rest.split(":").pop();
    return `${name} (agent)`;
  }
  return principal;
}

export const login = (handle, password) => api.login(handle, password);
export const enrol = (token, password) => api.enrol(token, password);

export async function logout() {
  try {
    await api.logout();
  } catch (err) {
    // 401 means the session was already gone. Signing out of a dead session
    // is a success from where the user is standing.
    if (!(err instanceof ApiError && err.status === 401)) throw err;
  }
}

// ---------------------------------------------------------------- the view

/** Render the sign-in (or enrolment) view into `container`.
 *  `onSignedIn` re-runs boot; the view removes itself. */
export function renderSignIn(container, onSignedIn) {
  const token = enrolToken();
  const enrolling = token !== null;
  container.innerHTML = "";
  container.classList.remove("hidden");

  const card = el("form", "auth-card");
  card.setAttribute("novalidate", "");
  card.append(
    el("h1", "auth-title", enrolling ? "Set your password" : "Sign in"),
  );

  const subtitle = el("p", "auth-sub", "");
  card.append(subtitle);

  const handleWrap = el("label", "auth-field", "");
  const handleInput = input("text", "handle", "handle");
  handleWrap.append(el("span", "auth-label", "Handle"), handleInput);

  const passwordWrap = el("label", "auth-field", "");
  const passwordInput = input("password", "password", "current-password");
  passwordWrap.append(el("span", "auth-label", "Password"), passwordInput);

  if (!enrolling) card.append(handleWrap);
  card.append(passwordWrap);

  const error = el("p", "auth-error", "");
  error.hidden = true;
  const submit = el("button", "tb-btn auth-submit",
                    enrolling ? "Set password" : "Sign in");
  submit.type = "submit";
  card.append(error, submit);
  container.append(card);

  if (enrolling) {
    passwordInput.autocomplete = "new-password";
    // Name the account the link is for, so a mis-sent invitation is visible
    // before somebody sets a password on somebody else's account.
    api.enrolInfo(token)
      .then((info) => { subtitle.textContent = `Enrolling ${info.handle}.`; })
      .catch(() => {
        subtitle.textContent = "";
        fail("This enrolment link is not valid. Ask for a new one.");
        submit.disabled = true;
      });
  }

  (enrolling ? passwordInput : handleInput).focus();

  card.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true;
    error.hidden = true;
    try {
      const identity = enrolling
        ? await enrol(token, passwordInput.value)
        : await login(handleInput.value.trim(), passwordInput.value);
      // Drop the token out of the URL and the history entry: an enrolment
      // link in a back button, a bookmark or a shared screenshot is a
      // credential lying around, even a spent one.
      if (enrolling) history.replaceState(null, "", "/");
      container.classList.add("hidden");
      container.innerHTML = "";
      onSignedIn(identity);
    } catch (err) {
      submit.disabled = false;
      passwordInput.value = "";
      fail(messageFor(err));
      passwordInput.focus();
    }
  });

  function fail(text) {
    error.textContent = text;
    error.hidden = false;
  }
}

/** The server's message, except where a raw one would be unhelpful. */
function messageFor(err) {
  if (!(err instanceof ApiError)) return "Something went wrong. Try again.";
  if (err.status === 429) {
    const wait = (err.error.details || {}).retry_after_s;
    return `Too many attempts. Try again in ${wait || "a few"} seconds.`;
  }
  if (err.status === 401) return "Sign-in failed. Check your handle and password.";
  if (err.status === 0) return "Server unreachable.";
  return err.error.message || "Sign-in failed.";
}

/** The identity chip in the toolbar, plus its sign-out button. */
export function renderChip(host, identity, onSignedOut) {
  host.innerHTML = "";
  if (!identity || !identity.principal) {
    host.classList.add("hidden");
    return;
  }
  host.classList.remove("hidden");
  // The principal carries the device suffix (`user:nikita/browser:7f3a1b2c`);
  // the chip shows the person, and the full identity is the tooltip — the
  // same `displayPrincipal` convention lock chips, claims and history
  // entries use.
  const name = displayPrincipal(identity.principal);
  const chip = el("span", "auth-chip", name);
  chip.title = `${identity.principal} · ${identity.role}`;
  const out = el("button", "tb-btn auth-signout", "Sign out");
  out.type = "button";
  out.addEventListener("click", async () => {
    out.disabled = true;
    await logout();
    onSignedOut();
  });
  host.append(chip, out);
}

// -------------------------------------------------------------- utilities

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function input(type, name, autocomplete) {
  const node = document.createElement("input");
  node.type = type;
  node.name = name;
  node.autocomplete = autocomplete;
  node.required = true;
  return node;
}
