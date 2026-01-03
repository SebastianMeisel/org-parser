// static/viewer.js

function makeHeadingsCollapsible({ collapseOnStartup = false } = {}) {
  const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4, h5, h6"));

  headings.forEach((h) => {
    h.classList.add("collapsible-heading");

    h.addEventListener("click", () => {
      toggleSection(h);
    });
  });

  if (collapseOnStartup) {
    // Collapse from deepest to shallowest so we don't fight with parent sections.
    const levelOf = (h) => parseInt(h.tagName.substring(1), 10);
    headings
      .slice()
      .sort((a, b) => levelOf(b) - levelOf(a))
      .forEach((h) => collapseHeading(h));
  }
}

// Collapse a heading without toggling it twice
function collapseHeading(heading) {
  if (!heading.classList.contains("collapsed")) {
    heading.classList.add("collapsed");
  }

  const tag = heading.tagName.toUpperCase();
  if (!/^H[1-6]$/.test(tag)) return;

  const level = parseInt(tag.substring(1), 10);

  let el = heading.nextElementSibling;
  while (el) {
    const t = el.tagName.toUpperCase();
    if (/^H[1-6]$/.test(t)) {
      const nextLevel = parseInt(t.substring(1), 10);
      if (nextLevel <= level) break;
    }

    el.dataset._prevDisplay = el.style.display || "";
    el.style.display = "none";

    el = el.nextElementSibling;
  }
}

function toggleSection(heading) {
  const tag = heading.tagName.toUpperCase();
  if (!/^H[1-6]$/.test(tag)) return;

  const level = parseInt(tag.substring(1), 10);
  const collapsed = heading.classList.toggle("collapsed");

  let el = heading.nextElementSibling;
  while (el) {
    const t = el.tagName.toUpperCase();
    if (/^H[1-6]$/.test(t)) {
      const nextLevel = parseInt(t.substring(1), 10);
      if (nextLevel <= level) break; // stop at same or higher heading
    }

    if (collapsed) {
      el.dataset._prevDisplay = el.style.display || "";
      el.style.display = "none";
    } else {
      el.style.display = el.dataset._prevDisplay || "";
      delete el.dataset._prevDisplay;
    }

    el = el.nextElementSibling;
  }
}

function addCopyButtons() {
  const blocks = document.querySelectorAll("pre.block-src, pre.block.result");

  blocks.forEach((pre) => {
    // avoid duplicates if called multiple times
    if (pre.dataset.hasCopyButton === "true") return;
    pre.dataset.hasCopyButton = "true";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy-button";
    btn.textContent = "Copy";

    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();

      // 🔑 Only copy the <code> content, not the whole <pre>
      const code = pre.querySelector("code");
      const text = code ? code.innerText : pre.innerText;

      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = "Copied!";
        setTimeout(() => {
          btn.textContent = "Copy";
        }, 1200);
      } catch (err) {
        console.error("Clipboard error:", err);
        btn.textContent = "Error";
        setTimeout(() => {
          btn.textContent = "Copy";
        }, 1500);
      }
    });

    // Insert as first child of <pre>
    pre.insertBefore(btn, pre.firstChild);
  });
}

(function () {
  function applySidebarState(isCollapsed) {
    document.body.classList.toggle("sidebar-collapsed", isCollapsed);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("sidebar-toggle");
    if (!btn) return;

    // restore
    const stored = localStorage.getItem("sidebar-collapsed");
    const isCollapsed = stored === "1";
    applySidebarState(isCollapsed);

    // toggle
    btn.addEventListener("click", () => {
      const next = !document.body.classList.contains("sidebar-collapsed");
      applySidebarState(next);
      localStorage.setItem("sidebar-collapsed", next ? "1" : "0");
    });
  });
})();

document.addEventListener("click", (e) => {
  const btn = e.target.closest(".comment-toggle");
  if (!btn) return;

  const targetId = btn.getAttribute("data-target");
  if (!targetId) return;

  const el = document.getElementById(targetId);
  if (!el) return;

  const nowHidden = !el.hidden;
  el.hidden = nowHidden;

  btn.textContent = nowHidden ? "Show comment" : "Hide comment";
});

document.addEventListener("DOMContentLoaded", () => {
  makeHeadingsCollapsible({ collapseOnStartup: true });
  addCopyButtons();
});


