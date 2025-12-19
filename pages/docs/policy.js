(() => {
  const STORAGE_KEY = "kotori_lang";

  function normalizeLang(raw) {
    if (!raw) return null;
    const value = raw.toLowerCase();
    if (value.startsWith("ja")) return "ja";
    if (value.startsWith("zh")) return "zh-TW";
    if (value.startsWith("th")) return "th";
    return "en";
  }

  const params = new URLSearchParams(window.location.search);
  const paramLang = normalizeLang(
    params.get("lang") || params.get("language") || params.get("locale")
  );
  const htmlLang = normalizeLang(document.documentElement.lang);
  const currentLang = paramLang || htmlLang;

  if (!currentLang) return;

  try {
    localStorage.setItem(STORAGE_KEY, currentLang);
  } catch (err) {
    // ローカルストレージが使えない場合は無視する
  }

  const backHome = document.querySelector(".back-home");
  if (backHome) {
    backHome.href = `../index.html?lang=${encodeURIComponent(currentLang)}`;
  }
})();
