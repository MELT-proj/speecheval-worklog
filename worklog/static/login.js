/* The sign-in page: theme, the post-login destination, and one error line. */
window.Worklog = window.Worklog || {};

(function (W) {
  const params = new URLSearchParams(location.search);
  const error = params.get("error");
  const next = document.getElementById("login-next");
  const box = document.getElementById("login-error");

  if (next) next.value = params.get("next") || "/";
  if (error && box) {
    if (error === "throttled") {
      const wait = params.get("wait") || "a few";
      box.textContent = `Too many failed attempts. Try again in ${wait} seconds.`;
    } else {
      box.textContent = "That password is not right.";
    }
    box.hidden = false;
  }

  W.theme.init();
})(window.Worklog);
