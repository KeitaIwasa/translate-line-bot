(() => {
  const translations = {
    en: {
      heroTitle: "Run multilingual LINE groups stress-free with the KOTORI Pro plan.",
      heroLead:
        "The KOTORI Pro Plan is the paid plan for our LINE bot that auto-translates group messages with contextual awareness. Use it confidently for work or personal chats without worrying about free-tier limits.",
      price:
        "Price: <span class=\"price-amount\"><span class=\"price-currency\">JPY</span><span class=\"price-number\">380</span></span> / month (tax included) per group",
      audience:
        "Best for: business chats in multinational teams, international student or friend groups, and cross-cultural communities",
      ctaLabel: "Purchase Pro Plan",
      ctaTerms:
        "By purchasing via the button below, you agree to the following terms.<br />・<a href=\"./docs/terms-en.html\" target=\"_blank\" rel=\"noopener\">Terms of Service</a><br />・<a href=\"./docs/privacy-en.html\" target=\"_blank\" rel=\"noopener\">Privacy Policy</a>",
      missingNotice:
        "Checkout link is missing. Please open this page from the official invitation message to continue.",
      featuresTitle: "What you get with Pro",
      features: [
        "Instantly translate group messages into up to five registered languages",
        "Context-aware, natural translations using recent conversation history",
        "Higher rate limits than Free for virtually unlimited everyday use",
        "Access to the wide language coverage provided by the underlying LLM",
        "Works for global business teams and casual international groups alike",
      ],
      compareTitle: "Free vs. Pro",
      compareItem: "Item",
      compareFree: "Free Plan",
      comparePro: "Pro Plan",
      comparison: [
        {
          item: "Price",
          free: "Free",
          pro: "JPY 380 / month (tax included) per group",
        },
        {
          item: "Translation volume",
          free: "50 messages / month",
          pro: "8,000 messages / month",
        },
      ],
      pricingTitle: "Pricing & billing conditions",
      pricing: [
        "Plan name: KOTORI - AI Translation/การแปล Pro Plan",
        "Price: JPY 380 / month (tax included) per group",
        "Billing: paid online by one representative (auto-renew)",
        "Charge timing: pay one month at checkout; auto-renews monthly thereafter",
        "Cancellation: mention the bot in your group and send the cancel command anytime; Pro stays active until the paid period ends",
      ],
      flowTitle: "How to get started",
      flow: [
        "Invite the official KOTORI LINE bot to your target group.",
        "Try translation behavior on the Free plan.",
        "Click the “Purchase Pro Plan” button on this page to go to checkout and pay by credit card.",
        "After payment, the group’s Pro Plan activates within a few minutes and remains active for the billing period.",
        "To cancel, mention the bot in the group and send the cancel command; you can keep using Pro until the paid period ends.",
      ],
      notesTitle: "Notes and terms",
      notes: [
        "Pro is contracted per LINE group. Each group requires its own subscription.",
        "If the bot is removed from a group, that group’s Pro plan is auto-canceled; no refunds for the remaining period.",
        "No refunds when you cancel; Pro remains available until the end of the paid period.",
        "Service details, pricing, and supported languages may change without notice.",
        "Temporary outages may occur due to network issues or external services (LINE, LLM, Stripe).",
        "For translation quality and context, recent group messages are stored and a portion may be sent to external AI models (Gemini).",
      ],
      notesTerms:
        "See the full details below.<br />・<a href=\"./docs/terms-en.html\" target=\"_blank\" rel=\"noopener\">Terms of Service</a><br />・<a href=\"./docs/privacy-en.html\" target=\"_blank\" rel=\"noopener\">Privacy Policy</a>",
      consumerLink: "Consumer Information",
    },
    ja: {
      heroTitle: "多言語のLINEグループ運営を、AIボットのProプランでストレスゼロに。",
      heroLead:
        "「KOTORI Proプラン」は、LINEグループ内のメッセージを文脈を考慮して自動翻訳するLINEボットの有料プランです。無料プランの制限を気にせず、ビジネスでもプライベートでも安心してご利用いただけます。",
      price:
        "料金：月額 <span class=\"price-amount\"><span class=\"price-currency\">¥</span><span class=\"price-number\">380</span></span>(税込) / 1グループ",
      audience: "対象：多国籍チームのビジネスチャット、留学生・友人グループ、国際交流コミュニティ など",
      ctaLabel: "Proプランを購入",
      ctaTerms:
        "上のボタンよりご購入いただくことで、以下の規約に同意したものとみなされます。<br />・<a href=\"./docs/terms-ja.html\" target=\"_blank\" rel=\"noopener\">利用規約</a><br />・<a href=\"./docs/privacy-ja.html\" target=\"_blank\" rel=\"noopener\">プライバシーポリシー</a>",
      missingNotice: "決済リンクが無効です。招待メッセージから正しいリンクを開いてください。",
      featuresTitle: "Proプランでできること",
      features: [
        "グループ内のメッセージを、登録された言語（最大5言語）へ即時翻訳",
        "直近の会話履歴の文脈を考慮した、自然で読みやすい翻訳",
        "Freeプランよりも高いレート制限で、ほぼ使い放題の翻訳",
        "LLMが対応する幅広い言語を利用可能",
        "多国籍ビジネスチームから友人グループまで、場面を選ばず利用可能",
      ],
      compareTitle: "Freeプランとの違い",
      compareItem: "項目",
      compareFree: "Freeプラン",
      comparePro: "Proプラン",
      comparison: [
        { item: "料金", free: "無料", pro: "月額 ¥380（税込） / 1グループ" },
        { item: "翻訳量", free: "50メッセージ/月", pro: "8,000メッセージ/月" },
      ],
      pricingTitle: "料金・課金条件",
      pricing: [
        "プラン名：KOTORI - AI翻訳/การแปล Proプラン",
        "料金：月額 ¥380（税込） / 1グループ",
        "課金方式：代表者1人がオンライン決済（自動更新）",
        "請求タイミング：初回購入時に1か月分をお支払い、その後は毎月自動更新",
        "解約方法：グループでボットにメンションし、解約コマンドを送信するといつでも解約可能。支払済み期間の終了までは利用可能",
      ],
      flowTitle: "ご利用開始までの流れ",
      flow: [
        "KOTORI公式LINEを対象グループに招待します。",
        "Freeプランとして翻訳の挙動をお試しください。",
        "本ページの「Proプランを購入」ボタンから決済ページへ進み、クレジットカードなどでお支払いください。",
        "決済完了後、数分以内にグループの Pro プランが有効化され、月額期間中は Pro プランの機能をご利用いただけます。",
        "解約したい場合は、グループでボットにメンションし、解約コマンドを送信してください。支払済み期間の終了までは Pro プランを利用できます。",
      ],
      notesTitle: "注意事項・利用規約について",
      notes: [
        "Proプランは1つのLINEグループごとに契約されます。複数グループで利用する場合はグループごとに契約が必要です。",
        "グループからボットが退会させられた場合、その時点で Pro プランは自動的に解約され、期間途中でも返金は行いません。",
        "お客様による解約手続きの場合も、支払済み期間中の返金は行いません。支払済み期間の満了までは Pro プランを利用できます。",
        "サービスの仕様・料金・対応言語は、事前の予告なく変更される場合があります。",
        "通信環境や外部サービスの障害により、一時的に翻訳結果の提供ができない場合があります。",
        "翻訳品質向上と文脈理解のため、グループ内のメッセージを保存し、翻訳時に直近の会話履歴の一部を外部AIモデルに送信します。",
      ],
      notesTerms:
        "詳細な条件については、以下のページをご確認ください。<br />・<a href=\"./docs/terms-ja.html\" target=\"_blank\" rel=\"noopener\">利用規約</a><br />・<a href=\"./docs/privacy-ja.html\" target=\"_blank\" rel=\"noopener\">プライバシーポリシー</a>",
      consumerLink: "特定商取引法に基づく表記",
    },
    "zh-TW": {
      heroTitle: "用 KOTORI Pro 方案，無壓力經營多語系 LINE 群組。",
      heroLead:
        "「KOTORI Pro 方案」是 LINE 機器人的付費方案，可依據脈絡自動翻譯群組訊息。免擔心免費方案的限制，商務或私人聊天都能安心使用。",
      price:
        "價格：每組每月 <span class=\"price-amount\"><span class=\"price-currency\">JPY</span><span class=\"price-number\">380</span></span>（含稅）",
      audience: "適用：跨國商務團隊、留學生／朋友群組、國際交流社群等",
      ctaLabel: "購買 Pro 方案",
      ctaTerms:
        "點擊下方按鈕購買即表示同意以下條款。<br />・<a href=\"./docs/terms-zh-tw.html\" target=\"_blank\" rel=\"noopener\">服務條款</a><br />・<a href=\"./docs/privacy-zh-tw.html\" target=\"_blank\" rel=\"noopener\">隱私權政策</a>",
      missingNotice: "缺少結帳連結。請從官方邀請訊息開啟本頁以繼續。",
      featuresTitle: "Pro 方案提供的功能",
      features: [
        "將群組訊息即時翻譯成已註冊的最多 5 種語言",
        "考量近期對話脈絡，提供自然易讀的翻譯",
        "比 Free 方案更高的速率限制，日常幾乎可不限量使用",
        "可使用底層 LLM 支援的廣泛語言",
        "適用於跨國商務團隊，也適合國際朋友或社群",
      ],
      compareTitle: "Free 與 Pro 的差異",
      compareItem: "項目",
      compareFree: "Free 方案",
      comparePro: "Pro 方案",
      comparison: [
        { item: "價格", free: "免費", pro: "每組每月 JPY 380（含稅）" },
        { item: "翻譯量", free: "每月 50 則訊息", pro: "每月 8,000 則訊息" },
      ],
      pricingTitle: "價格與課費條件",
      pricing: [
        "方案名稱：KOTORI - AI 翻譯/การแปล Pro 方案",
        "價格：每組每月 JPY 380（含稅）",
        "付款方式：由一位代表線上刷卡付款（自動續訂）",
        "收費時間：首次購買即收取一個月，其後每月自動續訂",
        "解約：在群組標記機器人並傳送解約指令即可；已付費期間結束前仍可使用 Pro",
      ],
      flowTitle: "開始使用的步驟",
      flow: [
        "將官方的 KOTORI LINE 機器人邀請到目標群組。",
        "先以 Free 方案試用翻譯行為。",
        "在本頁點擊「購買 Pro 方案」按鈕前往結帳，並以信用卡付款。",
        "付款完成後數分鐘內即啟用該群組的 Pro 方案，於計費期間可持續使用。",
        "若要解約，在群組標記機器人並傳送解約指令；已付費期間結束前仍可使用 Pro。",
      ],
      notesTitle: "注意事項與條款",
      notes: [
        "Pro 為單一 LINE 群組訂閱。若有多個群組需各別訂閱。",
        "若機器人被移出群組，該群組的 Pro 方案將自動取消，剩餘期間不予退費。",
        "使用者主動解約亦不退費，已付費期間結束前仍可使用 Pro。",
        "服務內容、價格與支援語言可能不經預告調整。",
        "網路或外部服務（LINE、LLM、Stripe）故障時，翻譯可能暫時無法提供。",
        "為提升翻譯品質與脈絡理解，群組訊息會被儲存，部分近期對話可能傳送至外部 AI 模型（Gemini）。",
      ],
      notesTerms:
        "詳細條件請參閱以下頁面。<br />・<a href=\"./docs/terms-zh-tw.html\" target=\"_blank\" rel=\"noopener\">服務條款</a><br />・<a href=\"./docs/privacy-zh-tw.html\" target=\"_blank\" rel=\"noopener\">隱私權政策</a>",
      consumerLink: "消費者資訊",
    },
    th: {
      heroTitle: "ดูแลงานแปลในกลุ่ม LINE หลายภาษาง่าย ๆ ด้วย KOTORI Pro Plan",
      heroLead:
        "KOTORI Pro Plan คือแพ็กเกจเสียเงินของบอต LINE ที่แปลข้อความในกลุ่มแบบเข้าใจบริบท ใช้งานได้ทั้งธุรกิจและส่วนตัวโดยไม่ต้องกังวลข้อจำกัดของฟรีแพ็กเกจ",
      price:
        "ราคา: <span class=\"price-amount\"><span class=\"price-currency\">JPY</span><span class=\"price-number\">380</span></span> ต่อเดือน (รวมภาษี) ต่อ 1 กลุ่ม",
      audience: "เหมาะสำหรับ: ทีมธุรกิจหลายชาติ กลุ่มเพื่อน/นักศึกษาต่างชาติ และคอมมูนิตี้นานาชาติ",
      ctaLabel: "ซื้อ Pro Plan",
      ctaTerms:
        "เมื่อกดปุ่มด้านล่างถือว่ายอมรับเงื่อนไขต่อไปนี้แล้ว<br />・<a href=\"./docs/terms-th.html\" target=\"_blank\" rel=\"noopener\">ข้อตกลงการใช้บริการ</a><br />・<a href=\"./docs/privacy-th.html\" target=\"_blank\" rel=\"noopener\">นโยบายความเป็นส่วนตัว</a>",
      missingNotice: "ไม่มีลิงก์ชำระเงิน โปรดเปิดหน้านี้จากข้อความเชิญอย่างเป็นทางการ",
      featuresTitle: "สิ่งที่ได้ใน Pro Plan",
      features: [
        "แปลข้อความในกลุ่มเป็นภาษาที่ตั้งไว้ได้ทันที สูงสุด 5 ภาษา",
        "พิจารณาบริบทจากประวัติการสนทนาเพื่อการแปลที่เป็นธรรมชาติ",
        "ขีดจำกัดสูงกว่า Free ใช้งานได้แทบไม่อั้น",
        "รองรับภาษาหลากหลายตามที่ LLM ให้บริการ",
        "ใช้ได้ทั้งทีมธุรกิจนานาชาติและกลุ่มเพื่อนต่างชาติ",
      ],
      compareTitle: "เปรียบเทียบ Free vs Pro",
      compareItem: "รายการ",
      compareFree: "Free",
      comparePro: "Pro",
      comparison: [
        { item: "ราคา", free: "ฟรี", pro: "JPY 380 ต่อเดือน/กลุ่ม (รวมภาษี)" },
        { item: "ปริมาณการแปล", free: "50 ข้อความ/เดือน", pro: "8,000 ข้อความ/เดือน" },
      ],
      pricingTitle: "ราคาและเงื่อนไขการเรียกเก็บเงิน",
      pricing: [
        "ชื่อแพ็กเกจ: KOTORI - AI Translation/การแปล Pro Plan",
        "ราคา: JPY 380 ต่อเดือน (รวมภาษี) ต่อ 1 กลุ่ม",
        "การชำระเงิน: ผู้แทน 1 คนชำระออนไลน์ด้วยบัตรเครดิต (ต่ออายุอัตโนมัติ)",
        "รอบบิล: ชำระเดือนแรกตอนซื้อ จากนั้นตัดบัตรอัตโนมัติทุกเดือน",
        "ยกเลิก: แท็กบอตในกลุ่มแล้วส่งคำสั่งยกเลิกได้ตลอด ใช้ Pro ต่อได้จนจบรอบที่ชำระแล้ว",
      ],
      flowTitle: "วิธีเริ่มต้นใช้งาน",
      flow: [
        "เชิญบอต LINE อย่างเป็นทางการของ KOTORI เข้ากลุ่มที่ต้องการ",
        "ทดลองพฤติกรรมการแปลใน Free Plan",
        "กดปุ่ม “ซื้อ Pro Plan” บนหน้านี้เพื่อไปยังหน้าชำระเงินและจ่ายด้วยบัตรเครดิต",
        "หลังชำระเงิน แผน Pro ของกลุ่มจะเปิดภายในไม่กี่นาทีและใช้งานได้ตลอดรอบบิล",
        "หากต้องการยกเลิก ให้แท็กบอตแล้วส่งคำสั่งยกเลิก ใช้ Pro ต่อได้จนจบรอบที่จ่ายแล้ว",
      ],
      notesTitle: "ข้อควรทราบและเงื่อนไข",
      notes: [
        "Pro เป็นการสมัครแบบต่อ 1 กลุ่ม LINE หากมีหลายกลุ่มต้องสมัครแยกกัน",
        "ถ้าบอตถูกนำออกจากกลุ่ม Pro ของกลุ่มนั้นจะถูกยกเลิกอัตโนมัติ และไม่คืนเงินส่วนที่เหลือ",
        "การยกเลิกโดยผู้ใช้ก็ไม่คืนเงิน สามารถใช้ Pro ต่อได้จนจบรอบที่ชำระแล้ว",
        "รายละเอียดบริการ ราคา และภาษาที่รองรับอาจเปลี่ยนได้โดยไม่แจ้งล่วงหน้า",
        "อาจเกิดการหยุดให้บริการชั่วคราวจากปัญหาเครือข่ายหรือบริการภายนอก (LINE, LLM, Stripe)",
        "เพื่อปรับปรุงคุณภาพการแปลและเข้าใจบริบท ระบบจะบันทึกข้อความในกลุ่มและส่งบางส่วนของประวัติสนทนาไปยังโมเดล AI ภายนอก (Gemini)",
      ],
      notesTerms:
        "ดูรายละเอียดเพิ่มเติมได้ที่ลิงก์ด้านล่าง<br />・<a href=\"./docs/terms-th.html\" target=\"_blank\" rel=\"noopener\">ข้อตกลงการใช้บริการ</a><br />・<a href=\"./docs/privacy-th.html\" target=\"_blank\" rel=\"noopener\">นโยบายความเป็นส่วนตัว</a>",
      consumerLink: "กฎหมายการค้าขายของญี่ปุ่น",
    },
  };

  const elements = {
    heroTitle: document.querySelector("[data-i18n='heroTitle']"),
    heroLead: document.querySelector("[data-i18n='heroLead']"),
    price: document.querySelector("[data-i18n='price']"),
    audience: document.querySelector("[data-i18n='audience']"),
    ctaButton: document.getElementById("ctaButton"),
    ctaTerms: document.getElementById("ctaTerms"),
    featuresTitle: document.querySelector("[data-i18n='featuresTitle']"),
    featuresList: document.getElementById("featuresList"),
    compareTitle: document.querySelector("[data-i18n='compareTitle']"),
    compareItem: document.querySelector("[data-i18n='compareItem']"),
    compareFree: document.querySelector("[data-i18n='compareFree']"),
    comparePro: document.querySelector("[data-i18n='comparePro']"),
    compareBody: document.getElementById("compareBody"),
    pricingTitle: document.querySelector("[data-i18n='pricingTitle']"),
    pricingList: document.getElementById("pricingList"),
    flowTitle: document.querySelector("[data-i18n='flowTitle']"),
    flowList: document.getElementById("flowList"),
    notesTitle: document.querySelector("[data-i18n='notesTitle']"),
    notesList: document.getElementById("notesList"),
    notesTerms: document.getElementById("notesTerms"),
    ctaButtonBottom: document.getElementById("ctaButtonBottom"),
    consumerLink: document.getElementById("consumerLink"),
  };

  const langSelect = document.getElementById("langSelect");
  const flagPaths = {
    ja: "./assets/flags/jp.svg",
    en: "./assets/flags/gb.svg",
    "zh-TW": "./assets/flags/tw.svg",
    th: "./assets/flags/th.svg",
  };
  const consumerPaths = {
    ja: "./docs/consumer-ja.html",
    en: "./docs/consumer-en.html",
    "zh-TW": "./docs/consumer-zh-tw.html",
    th: "./docs/consumer-th.html",
  };
  const globeIconPath = "./assets/globe.svg";
  const triggerLabel = "LNGUAGE";

  const lineButtonAssets = {
    ja: {
      src: "https://scdn.line-apps.com/n/line_add_friends/btn/ja.png",
      alt: "友だち追加",
    },
    en: {
      src: "https://scdn.line-apps.com/n/line_add_friends/btn/en.png",
      alt: "Add friend",
    },
    "zh-TW": {
      src: "https://scdn.line-apps.com/n/line_add_friends/btn/zh-Hant.png",
      alt: "加入好友",
    },
    th: {
      src: "https://scdn.line-apps.com/n/line_add_friends/btn/th.png",
      alt: "เพิ่มเพื่อน",
    },
  };

  let customSelectEl;
  let customOptionsEl;
  let customTriggerEl;
  let customTriggerFlagEl;
  let customTriggerTextEl;
  let lineButtonTop;
  let lineButtonBottom;
  let currentLang = "ja";

  const params = new URLSearchParams(window.location.search);
  const checkoutId = params.get("session_id") || params.get("sessionId");
  const checkoutUrlParam = params.get("checkout_url");
  const checkoutUrl =
    checkoutUrlParam ||
    (checkoutId ? `/checkout?session_id=${encodeURIComponent(checkoutId)}` : null);

  function setList(id, items) {
    const el = elements[id];
    if (!el) return;
    el.innerHTML = items.map((item) => `<li>${item}</li>`).join("");
  }

  function setFlow(items) {
    elements.flowList.innerHTML = items.map((step) => `<li>${step}</li>`).join("");
  }

  function setComparison(rows) {
    elements.compareBody.innerHTML = rows
      .map(
        (row) =>
          `<tr><td>${row.item}</td><td>${row.free}</td><td>${row.pro}</td></tr>`
      )
      .join("");
  }

  function normalizeLang(raw) {
    if (!raw) return null;
    const value = raw.toLowerCase();
    if (value.startsWith("ja")) return "ja";
    if (value.startsWith("zh")) return "zh-TW";
    if (value.startsWith("th")) return "th";
    return "en";
  }

  function toggleOptions(forceOpen) {
    if (!customSelectEl || !customTriggerEl || !customOptionsEl) return;
    const willOpen =
      typeof forceOpen === "boolean"
        ? forceOpen
        : !customSelectEl.classList.contains("open");
    customSelectEl.classList.toggle("open", willOpen);
    customTriggerEl.setAttribute("aria-expanded", String(willOpen));
  }

  function getLineButtonAsset(lang) {
    return lineButtonAssets[lang] || lineButtonAssets.ja;
  }

  function updateLineButtonAssets(lang) {
    const asset = getLineButtonAsset(lang);
    [lineButtonTop, lineButtonBottom].forEach((btn) => {
      if (!btn) return;
      const img = btn.querySelector("img");
      if (!img) return;
      img.src = asset.src;
      img.alt = asset.alt;
    });
  }

  function updateCustomSelected(lang) {
    if (!customTriggerEl || !customTriggerFlagEl || !customTriggerTextEl) return;
    const selectedOption =
      Array.from(langSelect?.options || []).find((opt) => opt.value === lang) ||
      langSelect?.options?.[0];
    customTriggerFlagEl.src = globeIconPath;
    customTriggerTextEl.textContent = triggerLabel;

    if (customOptionsEl) {
      Array.from(customOptionsEl.children).forEach((li) => {
        const isActive = li.dataset.value === lang;
        li.classList.toggle("active", isActive);
        li.setAttribute("aria-selected", String(isActive));
      });
    }
  }

  function buildCustomSelect() {
    if (!langSelect) return;

    langSelect.style.display = "none";

    customSelectEl = document.createElement("div");
    customSelectEl.className = "lang-select-custom";

    customTriggerEl = document.createElement("button");
    customTriggerEl.type = "button";
    customTriggerEl.className = "lang-select-trigger";
    customTriggerEl.setAttribute("aria-haspopup", "listbox");
    customTriggerEl.setAttribute("aria-expanded", "false");
    customTriggerEl.setAttribute("aria-label", "Language selector");

    customTriggerFlagEl = document.createElement("img");
    customTriggerFlagEl.className = "lang-globe";
    customTriggerFlagEl.alt = "";
    customTriggerFlagEl.src = globeIconPath;

    customTriggerTextEl = document.createElement("span");
    customTriggerTextEl.className = "lang-selected-label";
    customTriggerTextEl.textContent = triggerLabel;

    const arrowEl = document.createElement("span");
    arrowEl.className = "lang-select-arrow";
    arrowEl.textContent = "▾";

    customTriggerEl.append(customTriggerFlagEl, customTriggerTextEl, arrowEl);

    customOptionsEl = document.createElement("ul");
    customOptionsEl.className = "lang-select-options";
    customOptionsEl.setAttribute("role", "listbox");

    Array.from(langSelect.options).forEach((opt) => {
      const li = document.createElement("li");
      li.className = "lang-select-option";
      li.dataset.value = opt.value;
      li.setAttribute("role", "option");

      const flagImg = document.createElement("img");
      flagImg.className = "lang-flag";
      flagImg.alt = "";
      flagImg.src = flagPaths[opt.value] || flagPaths.ja;

      const text = document.createElement("span");
      text.textContent = opt.textContent;

      li.append(flagImg, text);
      li.addEventListener("click", () => {
        applyLang(opt.value);
        toggleOptions(false);
      });

      customOptionsEl.appendChild(li);
    });

    customSelectEl.append(customTriggerEl, customOptionsEl);
    langSelect.insertAdjacentElement("afterend", customSelectEl);

    customTriggerEl.addEventListener("click", () => toggleOptions());
    document.addEventListener("click", (e) => {
      if (!customSelectEl.contains(e.target)) toggleOptions(false);
    });
  }

  function applyLang(lang) {
    currentLang = lang;
    const t = translations[lang] || translations.ja;
    document.documentElement.lang = lang;

    elements.heroTitle.textContent = t.heroTitle;
    elements.heroLead.textContent = t.heroLead;
    elements.price.innerHTML = t.price;
    elements.audience.textContent = t.audience;
    elements.ctaButton.textContent = t.ctaLabel;
    elements.ctaTerms.innerHTML = t.ctaTerms;
    if (elements.ctaButtonBottom) elements.ctaButtonBottom.textContent = t.ctaLabel;

    elements.featuresTitle.textContent = t.featuresTitle;
    setList("featuresList", t.features);

    elements.compareTitle.textContent = t.compareTitle;
    elements.compareItem.textContent = t.compareItem;
    elements.compareFree.textContent = t.compareFree;
    elements.comparePro.textContent = t.comparePro;
    setComparison(t.comparison);

    elements.pricingTitle.textContent = t.pricingTitle;
    setList("pricingList", t.pricing);

    elements.flowTitle.textContent = t.flowTitle;
    setFlow(t.flow);

    elements.notesTitle.textContent = t.notesTitle;
    setList("notesList", t.notes);
    elements.notesTerms.innerHTML = t.notesTerms;
    if (elements.consumerLink) {
      elements.consumerLink.textContent = t.consumerLink || "Consumer Information";
      elements.consumerLink.href = consumerPaths[lang] || consumerPaths.ja;
    }

    if (langSelect) {
      langSelect.value = lang;
      const flagUrl = flagPaths[lang] || flagPaths.ja;
      const cssUrl = `url("${flagUrl}")`;
      langSelect.style.setProperty("--flag-image", cssUrl);
    }

    updateCustomSelected(lang);
    updateLineButtonAssets(lang);
  }

  function createLineAddButton(lang) {
    const anchor = document.createElement("a");
    anchor.href = "https://lin.ee/5roFh0n";
    anchor.target = "_blank";
    anchor.rel = "noopener";
    anchor.className = "line-add-btn";

    const asset = getLineButtonAsset(lang);
    const img = document.createElement("img");
    img.src = asset.src;
    img.alt = asset.alt;
    img.height = 36;
    img.border = 0;

    anchor.appendChild(img);
    return anchor;
  }

  function showLineButton(isCheckoutAvailable) {
    const ctaTop = document.getElementById("cta");
    const ctaBottom = document.getElementById("ctaBottom");

    if (!isCheckoutAvailable) {
      if (!lineButtonTop && ctaTop) {
        lineButtonTop = createLineAddButton(currentLang);
        ctaTop.insertBefore(lineButtonTop, ctaTop.firstChild);
      }
      if (!lineButtonBottom && ctaBottom) {
        lineButtonBottom = createLineAddButton(currentLang);
        ctaBottom.insertBefore(lineButtonBottom, ctaBottom.firstChild);
      }
      updateLineButtonAssets(currentLang);
    } else {
      if (lineButtonTop?.parentNode) lineButtonTop.remove();
      if (lineButtonBottom?.parentNode) lineButtonBottom.remove();
      lineButtonTop = null;
      lineButtonBottom = null;
    }
  }

  function initLanguage() {
    const urlLang = normalizeLang(params.get("lang"));
    const browserLang = normalizeLang(navigator.language || navigator.userLanguage);
    const initial = urlLang || browserLang || "ja";
    applyLang(initial);
  }

  function initCta() {
    if (checkoutUrl) {
      elements.ctaButton.href = checkoutUrl;
      elements.ctaButton.removeAttribute("aria-disabled");
      if (elements.ctaButtonBottom) {
        elements.ctaButtonBottom.href = checkoutUrl;
        elements.ctaButtonBottom.removeAttribute("aria-disabled");
      }
      showLineButton(true);
    } else {
      elements.ctaButton.href = "#";
      elements.ctaButton.setAttribute("aria-disabled", "true");
      elements.ctaButton.style.display = "none";
      if (elements.ctaButtonBottom) {
        elements.ctaButtonBottom.href = "#";
        elements.ctaButtonBottom.setAttribute("aria-disabled", "true");
        elements.ctaButtonBottom.style.display = "none";
      }
      showLineButton(false);
    }
  }

  if (langSelect) {
    buildCustomSelect();
    langSelect.addEventListener("change", (e) => applyLang(e.target.value));
  }

  initLanguage();
  initCta();
})();
