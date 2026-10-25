// Netlify's build step replaces this value with CUTROOM_API_BASE_URL.
// An empty value preserves the existing same-origin local server behavior.
window.CUTROOM_API_BASE_URL = "";
window.cutroomApiUrl = function (path) {
  const value = String(path || "");
  if (!value.startsWith("/api/")) return value;
  const base = String(window.CUTROOM_API_BASE_URL || "").replace(/\/+$/, "");
  return base + value;
};

// Keep API media URLs and links working even when a view builds its markup
// dynamically. Fetch requests are credentialed by auth.js.
(() => {
  const base = String(window.CUTROOM_API_BASE_URL || "").replace(/\/+$/, "");
  if (!base) return;
  const rewrite = (value) =>
    typeof value === "string"
      ? value
          .replace(/(["'(=])\/api\//g, (_match, prefix) => prefix + base + "/api/")
          .replace(/<(video|audio|img|source)\b([^>]*?)>/gi, (tag, _name, attrs) =>
            attrs.includes(base + "/api/") && !/\bcrossorigin\s*=/i.test(attrs)
              ? tag.replace(/>$/, ' crossorigin="use-credentials">')
              : tag)
      : value;
  const innerHTML = Object.getOwnPropertyDescriptor(Element.prototype, "innerHTML");
  Object.defineProperty(Element.prototype, "innerHTML", {
    configurable: innerHTML.configurable,
    enumerable: innerHTML.enumerable,
    get() { return innerHTML.get.call(this); },
    set(value) { innerHTML.set.call(this, rewrite(value)); },
  });
  const setAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, value) {
    const attr = String(name).toLowerCase();
    if (["src", "href", "poster"].includes(attr) && String(value).startsWith("/api/")) {
      if (attr === "src" && (this instanceof HTMLMediaElement || this instanceof HTMLImageElement)) {
        this.crossOrigin = "use-credentials";
      }
      value = window.cutroomApiUrl(value);
    }
    return setAttribute.call(this, name, value);
  };
  for (const constructor of [HTMLMediaElement, HTMLImageElement, HTMLSourceElement, HTMLAnchorElement]) {
    const descriptor = Object.getOwnPropertyDescriptor(constructor.prototype, "src") ||
      Object.getOwnPropertyDescriptor(constructor.prototype, "href");
    const property = Object.getOwnPropertyDescriptor(constructor.prototype, "src") ? "src" : "href";
    if (!descriptor?.configurable || !descriptor.set || !descriptor.get) continue;
    Object.defineProperty(constructor.prototype, property, {
      configurable: descriptor.configurable,
      enumerable: descriptor.enumerable,
      get() { return descriptor.get.call(this); },
      set(value) {
        if (property === "src" && String(value).startsWith("/api/") &&
            (this instanceof HTMLMediaElement || this instanceof HTMLImageElement)) {
          this.crossOrigin = "use-credentials";
        }
        descriptor.set.call(this, window.cutroomApiUrl(value));
      },
    });
  }
  const hydratedBackgrounds = new WeakSet();
  async function hydrateApiBackgrounds(root) {
    const elements = [];
    if (root instanceof Element && root.matches('[style*="/api/"]')) elements.push(root);
    if (root.querySelectorAll) elements.push(...root.querySelectorAll('[style*="/api/"]'));
    for (const element of elements) {
      if (hydratedBackgrounds.has(element)) continue;
      const match = element.style.backgroundImage.match(/url\(["']?((?:https?:\/\/[^"')]+)?\/api\/[^"')]+)["']?\)/);
      if (!match) continue;
      hydratedBackgrounds.add(element);
      try {
        const response = await fetch(window.cutroomApiUrl(match[1]), { credentials: "include" });
        if (!response.ok) continue;
        const image = URL.createObjectURL(await response.blob());
        element.style.backgroundImage = element.style.backgroundImage.replace(match[1], image);
      } catch (error) {
        console.warn("Could not load a protected media thumbnail.", error);
      }
    }
  }
  const backgroundObserver = new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "attributes") hydrateApiBackgrounds(record.target);
      else record.addedNodes.forEach((node) => hydrateApiBackgrounds(node));
    }
  });
  backgroundObserver.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["style"],
  });
  hydrateApiBackgrounds(document);
  document.addEventListener("click", (event) => {
    const button = event.target.closest("#connectYoutube, #connectYoutubeEmpty, [data-download], [data-download-clip], [data-download-captions]");
    if (!button) return;
    let path = "";
    if (button.id === "connectYoutube" || button.id === "connectYoutubeEmpty") path = "/api/youtube/connect";
    else if (button.hasAttribute("data-download")) path = `/api/clips/${encodeURIComponent(button.dataset.download)}/download`;
    else if (button.hasAttribute("data-download-clip")) path = `/api/clips/${encodeURIComponent(button.dataset.downloadClip)}/download`;
    else if (button.hasAttribute("data-download-captions")) path = `/api/clips/${encodeURIComponent(button.dataset.downloadCaptions)}/captions`;
    if (path) {
      event.preventDefault();
      event.stopImmediatePropagation();
      window.location.assign(window.cutroomApiUrl(path));
    }
  }, true);
})();
