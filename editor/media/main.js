// @ts-nocheck
(function () {
  const vscode = acquireVsCodeApi();
  const diagram = document.getElementById("diagram");
  const statusEl = document.getElementById("status");
  const hopsInput = document.getElementById("hops");
  const hopsVal = document.getElementById("hops-val");
  const followInput = document.getElementById("follow");
  const refreshBtn = document.getElementById("refresh");

  // 直近の状態を復元（パネルが隠れて戻ってきたとき用）
  const prev = vscode.getState() || {};
  if (prev.svg) {
    renderSvg(prev.svg);
  }
  if (typeof prev.hops === "number") {
    hopsInput.value = String(prev.hops);
    hopsVal.textContent = String(prev.hops);
  }

  hopsInput.addEventListener("input", () => {
    hopsVal.textContent = hopsInput.value;
    vscode.postMessage({ type: "setHops", hops: Number(hopsInput.value) });
  });
  followInput.addEventListener("change", () => {
    vscode.postMessage({ type: "setFollow", follow: followInput.checked });
  });
  refreshBtn.addEventListener("click", () => {
    vscode.postMessage({ type: "refresh" });
  });

  window.addEventListener("message", (event) => {
    const msg = event.data;
    switch (msg.type) {
      case "loading":
        setStatus("更新中…");
        if (typeof msg.hops === "number") {
          hopsInput.value = String(msg.hops);
          hopsVal.textContent = String(msg.hops);
        }
        break;
      case "status":
        setStatus(msg.message || "");
        if (!msg.keepSvg) {
          // SVG は保持（keepSvg のとき）／それ以外はヒント表示のみ差し替えない
        }
        break;
      case "error":
        setStatus("");
        diagram.innerHTML = '<div class="hint error"></div>';
        diagram.firstChild.textContent = msg.message || "エラーが発生しました。";
        break;
      case "preview":
        applyPreview(msg.data);
        break;
    }
  });

  function applyPreview(data) {
    const focusTxt = data.focus && data.focus.length
      ? data.focus.join(", ")
      : "(未特定)";
    const ctx = contextLabel(data.context);
    if (data.svg) {
      renderSvg(data.svg);
      vscode.setState({ svg: data.svg, hops: data.hops });
      setStatus(`注目: ${focusTxt} ・ ${ctx} ・ ${data.hops} ホップ`);
    } else if (data.not_found && data.not_found.length) {
      setStatus(`存在しない device-id: ${data.not_found.join(", ")}`);
    } else {
      setStatus(data.message || `注目: ${focusTxt}`);
    }
  }

  function contextLabel(ctx) {
    switch (ctx) {
      case "device":
        return "ノード定義";
      case "connection":
        return "接続定義";
      case "explicit":
        return "明示指定";
      default:
        return "—";
    }
  }

  function renderSvg(svg) {
    diagram.innerHTML = svg;
    const svgEl = diagram.querySelector("svg");
    if (svgEl) {
      svgEl.removeAttribute("width");
      svgEl.removeAttribute("height");
      svgEl.style.maxWidth = "100%";
    }
    // device ノードのクリックで YAML の定義行へジャンプ
    diagram.querySelectorAll('[id^="device:"]').forEach((node) => {
      node.style.cursor = "pointer";
      node.addEventListener("click", () => {
        const deviceId = node.id.slice("device:".length);
        vscode.postMessage({ type: "jump", deviceId });
      });
    });
  }

  function setStatus(text) {
    statusEl.textContent = text;
  }
})();
