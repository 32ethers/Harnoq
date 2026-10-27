/* Translate only explicitly marked UI messages, never DOM or project content. */
const harnoqLocaleData = typeof document !== "undefined" && document.getElementById("harnoq-i18n");
const harnoqMessages = harnoqLocaleData ? JSON.parse(harnoqLocaleData.textContent) : {};
function uiText(message, ...values) {
  // Tagged templates keep interpolated values outside the translation lookup.
  const key = Array.isArray(message)
    ? message.reduce((text, part, index) => text + (index ? `{${index - 1}}` : "") + part, "")
    : message;
  const translated = Object.prototype.hasOwnProperty.call(harnoqMessages, key)
    ? harnoqMessages[key] : key;
  return values.length ? translated.replace(/\{(\d+)\}/g, (match, index) =>
    Number(index) < values.length ? String(values[Number(index)]) : match) : translated;
}

const harnoqLanguageSelect = typeof document !== "undefined" && document.getElementById("language-switch");
if (harnoqLanguageSelect) {
  harnoqLanguageSelect.addEventListener("change", () => {
    window.location.assign(harnoqLanguageSelect.value);
  });
}
