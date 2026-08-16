import * as THREE from "./vendor/three.module.js";

const canvas = document.querySelector("#station-canvas");

if (canvas) {
  const wrap = document.querySelector("#station-canvas-wrap");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 100);
  let renderer = null;
  let fallbackContext = null;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "low-power" });
  } catch (error) {
    // Headless review environments may disable WebGL. Keep the same workcell
    // story visible with a lightweight 2D fallback instead of an empty stage.
    fallbackContext = canvas.getContext("2d");
    canvas.dataset.renderer = "2d-fallback";
  }
  const root = new THREE.Group();
  const tool = new THREE.Group();
  const p03 = new THREE.Group();
  const p03Ring = new THREE.Mesh(
    new THREE.TorusGeometry(0.34, 0.035, 10, 32),
    new THREE.MeshBasicMaterial({ color: 0xc28c50, transparent: true, opacity: 0.78 }),
  );
  const p03Halo = new THREE.Mesh(
    new THREE.RingGeometry(0.42, 0.52, 40),
    new THREE.MeshBasicMaterial({ color: 0xc28c50, transparent: true, opacity: 0.13, side: THREE.DoubleSide }),
  );
  const frameMaterial = new THREE.MeshStandardMaterial({ color: 0x71808a, roughness: 0.72, metalness: 0.18 });
  const darkMaterial = new THREE.MeshStandardMaterial({ color: 0x24313a, roughness: 0.82, metalness: 0.1 });
  const panelMaterial = new THREE.MeshStandardMaterial({ color: 0xb9c2c3, roughness: 0.82, metalness: 0.06 });
  const blueMaterial = new THREE.MeshStandardMaterial({ color: 0x547784, roughness: 0.62, metalness: 0.22 });
  const fastenerMaterial = new THREE.MeshStandardMaterial({ color: 0xc5a165, roughness: 0.36, metalness: 0.62 });
  const toolMaterial = new THREE.MeshStandardMaterial({ color: 0xd6dad8, roughness: 0.48, metalness: 0.38 });
  let mode = "baseline";
  let running = false;
  let elapsed = 0;
  let lastTick = performance.now();
  let visible = true;

  function box(width, height, depth, x, y, z, material, parent = root) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), material);
    mesh.position.set(x, y, z);
    parent.add(mesh);
    return mesh;
  }

  function cylinder(radius, height, x, y, z, material, parent = root, rotation = [0, 0, 0]) {
    const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius * 1.02, height, 24), material);
    mesh.position.set(x, y, z);
    mesh.rotation.set(...rotation);
    parent.add(mesh);
    return mesh;
  }

  function setText(selector, value) {
    const element = document.querySelector(selector);
    if (element) element.textContent = value;
  }

  function updateReadout(progress, forceMode = null) {
    const risk = forceMode ? forceMode === "risk" : mode === "risk" && progress > 0.48;
    const drift = risk && progress > 0.36;
    const activeMode = forceMode || mode;
    const torque = activeMode === "baseline" ? "48.13" : drift ? "49.09" : "48.13";
    const angle = activeMode === "baseline" ? "1.00×" : drift ? "2.17×" : "1.00×";
    const retry = activeMode === "baseline" ? "0.010" : drift ? "0.250" : "0.010";
    const stateLabel = activeMode === "baseline" ? "基线稳定" : risk ? "等待工程师复核" : "工位回放中";
    const stateClass = activeMode === "baseline" ? "station-state-ok" : risk ? "station-state-risk" : "station-state-running";
    setText("#station-replay-state", stateLabel);
    const state = document.querySelector("#station-replay-state");
    if (state) state.className = `station-state ${stateClass}`;
    setText("#station-window-label", activeMode === "baseline" ? "前 100 条基线" : risk ? "最近 24 条风险窗口" : "最近窗口回放中");
    setText("#station-torque-value", torque);
    setText("#station-angle-value", angle);
    setText("#station-retry-value", retry);
    setText("#station-replay-clock", `00:${String(Math.min(59, Math.round(progress * 12))).padStart(2, "0")}`);
    setText(
      "#station-replay-cue",
      activeMode === "baseline"
        ? "当前窗口保持稳定，系统不生成候选原因或处置任务。"
        : risk
          ? "扭矩仍在规格内，但多信号同向变化，风险卡进入人工复核。"
          : "工具正在完成 P03 的连续拧紧，先观察过程变化，再看风险结论。",
    );
    const bar = document.querySelector("#station-progress-bar");
    if (bar) bar.style.width = `${Math.round(progress * 100)}%`;
    const color = activeMode === "baseline" ? 0x72a785 : risk ? 0xc26a4d : 0xc28c50;
    p03Ring.material.color.setHex(color);
    p03Halo.material.color.setHex(color);
    p03Ring.material.opacity = risk ? 0.96 : 0.68;
    p03Halo.material.opacity = risk ? 0.22 : 0.11;
  }

  function focusObject(focus) {
    const details = {
      station: ["ST-FAS-07 · 总装工位", "当前页面只分析这一处工位和同一程序分层，避免把不同车型或紧固点混在一起。"],
      tool: ["TQ-17 · 电动拧紧工具", "工具健康信号用于形成待核验假设，不能直接替代标定检查或现场点检。"],
      point: ["P03 · 关键紧固点", "这是过程信号与质量影响连接的位置，所有风险卡证据都回到这个点位。"],
      signal: ["组合信号 · 规格内趋势变化", "扭矩均值偏移 1.56σ、角度离散 2.17×、重试均值升至 0.250 次/循环。"],
    };
    const [title, detail] = details[focus] || details.station;
    setText("#station-focus-title", title);
    setText("#station-focus-detail", detail);
    document.querySelectorAll("[data-station-focus]").forEach((button) => button.classList.toggle("is-selected", button.dataset.stationFocus === focus));
  }

  function setMode(nextMode) {
    mode = nextMode;
    running = false;
    elapsed = nextMode === "baseline" ? 0 : 12;
    updateReadout(nextMode === "baseline" ? 0 : 1, nextMode);
    tool.position.y = nextMode === "baseline" ? 2.55 : 2.18;
    tool.rotation.z = nextMode === "baseline" ? 0 : -0.06;
  }

  function start() {
    mode = "risk";
    if (reducedMotion.matches) {
      elapsed = 12;
      running = false;
      updateReadout(1, "risk");
      return;
    }
    if (elapsed >= 12) elapsed = 0;
    running = true;
    updateReadout(elapsed / 12);
    document.querySelector("#station-pause")?.removeAttribute("disabled");
    setText("#station-replay-cue", "回放进行中：工具从正常循环进入最近窗口，请观察 P03 标注。 ");
  }

  function pause() {
    running = false;
    document.querySelector("#station-pause")?.setAttribute("disabled", "");
    setText("#station-replay-cue", "回放已暂停。可以查看当前对象，或继续播放到风险卡状态。");
  }

  function reset() {
    mode = "risk";
    elapsed = 0;
    running = false;
    tool.position.y = 2.55;
    tool.rotation.z = 0;
    updateReadout(0, "risk");
    document.querySelector("#station-pause")?.setAttribute("disabled", "");
    setText("#station-replay-cue", "点击“开始回放”，观察 P03 从稳定窗口进入待复核状态。");
  }

  function resize() {
    const bounds = wrap?.getBoundingClientRect();
    const width = Math.max(320, bounds?.width || 800);
    const height = Math.max(280, bounds?.height || 480);
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    if (renderer) {
      renderer.setPixelRatio(ratio);
      renderer.setSize(width, height, false);
    } else if (fallbackContext) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      fallbackContext.setTransform(ratio, 0, 0, ratio, 0, 0);
    }
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }

  function drawFallback(progress) {
    if (!fallbackContext) return;
    const width = canvas.clientWidth || 800;
    const height = canvas.clientHeight || 430;
    const ctx = fallbackContext;
    ctx.clearRect(0, 0, width, height);
    const sx = width / 800;
    const sy = height / 430;
    ctx.save();
    ctx.scale(sx, sy);
    ctx.fillStyle = "#15232b";
    ctx.fillRect(0, 0, 800, 430);
    ctx.strokeStyle = "rgba(157,183,185,.22)";
    ctx.lineWidth = 1;
    for (let x = 34; x < 790; x += 56) { ctx.beginPath(); ctx.moveTo(x, 328); ctx.lineTo(x, 398); ctx.stroke(); }
    for (let z = 330; z < 400; z += 28) { ctx.beginPath(); ctx.moveTo(24, z); ctx.lineTo(786, z); ctx.stroke(); }
    ctx.fillStyle = "#71808a";
    ctx.fillRect(94, 112, 612, 11);
    ctx.fillRect(98, 122, 12, 184);
    ctx.fillRect(690, 122, 12, 184);
    ctx.fillStyle = "#b9c2c3";
    ctx.fillRect(192, 218, 416, 66);
    ctx.fillStyle = "#547784";
    ctx.fillRect(276, 274, 248, 22);
    ctx.fillStyle = "#d0a564";
    ctx.beginPath(); ctx.arc(342, 304, 13, 0, Math.PI * 2); ctx.fill();
    const p = Math.min(1, Math.max(0, progress));
    const toolY = 108 + p * 112;
    ctx.save();
    ctx.translate(342, toolY);
    ctx.rotate(-(p > .6 ? .06 : 0));
    ctx.fillStyle = "#d6dad8"; ctx.fillRect(-18, -52, 36, 82);
    ctx.fillStyle = "#24313a"; ctx.fillRect(-23, 30, 46, 14);
    ctx.fillStyle = "#c5a165"; ctx.fillRect(-7, 44, 14, 27);
    ctx.fillStyle = "#547784"; ctx.fillRect(18, -25, 8, 48);
    ctx.restore();
    const ringColor = mode === "baseline" ? "#72a785" : p > .48 ? "#c26a4d" : "#c28c50";
    ctx.strokeStyle = ringColor;
    ctx.lineWidth = 4;
    ctx.beginPath(); ctx.arc(342, 304, 25, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = `${ringColor}44`;
    ctx.lineWidth = 10;
    ctx.beginPath(); ctx.arc(342, 304, 34, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();
  }

  // Scene layout: an original, low-detail workcell built from primitives so the
  // visual remains fast, inspectable and clearly marked as a synthetic schematic.
  scene.add(new THREE.HemisphereLight(0xe7ecec, 0x26333a, 1.85));
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.2);
  keyLight.position.set(4, 7, 5);
  scene.add(keyLight);
  scene.add(root);
  root.rotation.y = -0.38;
  root.position.y = -0.25;
  box(13, 0.18, 7.5, 0, -0.64, 0, darkMaterial);
  for (let x = -5; x <= 5; x += 2.5) box(0.025, 0.02, 7, x, -0.53, 0, frameMaterial);
  for (let z = -3; z <= 3; z += 2) box(13, 0.02, 0.025, 0, -0.53, z, frameMaterial);
  box(8.3, 0.22, 2.9, 0, 0.08, -0.48, frameMaterial);
  box(7.8, 1.55, 0.24, 0, 1.1, -1.8, panelMaterial);
  box(0.22, 3.4, 0.22, -5.2, 1.25, -1.2, frameMaterial);
  box(0.22, 3.4, 0.22, 5.2, 1.25, -1.2, frameMaterial);
  box(10.6, 0.22, 0.22, 0, 2.92, -1.2, frameMaterial);
  box(10.6, 0.16, 0.16, 0, 2.25, -1.2, blueMaterial);
  box(2.3, 0.26, 1.35, 0, 0.42, 0.3, blueMaterial);
  box(2.65, 0.12, 1.68, 0, 0.6, 0.3, frameMaterial);
  cylinder(0.22, 0.12, -0.62, 0.72, 0.3, fastenerMaterial, root);
  cylinder(0.22, 0.12, 0.62, 0.72, 0.3, fastenerMaterial, root);
  p03.position.set(-0.62, 0.79, 0.3);
  p03Ring.rotation.x = Math.PI / 2;
  p03Halo.rotation.x = Math.PI / 2;
  p03.add(p03Ring, p03Halo);
  root.add(p03);
  tool.position.set(-0.62, 2.55, 0.3);
  tool.rotation.z = 0;
  box(0.46, 1.18, 0.46, 0, 0, 0, toolMaterial, tool);
  box(0.58, 0.22, 0.58, 0, -0.68, 0, darkMaterial, tool);
  cylinder(0.14, 0.44, 0, -0.92, 0, fastenerMaterial, tool);
  box(0.12, 0.7, 0.12, 0.26, 0.12, 0, blueMaterial, tool);
  root.add(tool);
  const cable = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(2.3, -1.2, 0.2), new THREE.Vector3(4.1, -1.2, 0.2)]),
    new THREE.LineBasicMaterial({ color: 0x27343c, transparent: true, opacity: 0.85 }),
  );
  cable.position.set(-0.35, 2.2, 0.3);
  tool.add(cable);
  camera.position.set(7.2, 5.3, 8.5);
  camera.lookAt(0, 0.9, 0);
  resize();
  updateReadout(0, "baseline");
  focusObject("point");

  function tick(now) {
    const delta = Math.min(0.05, (now - lastTick) / 1000);
    lastTick = now;
    if (running) {
      elapsed += delta;
      const progress = Math.min(1, elapsed / 12);
      const descent = progress < 0.3 ? progress / 0.3 : 1;
      const settle = progress > 0.6 ? (progress - 0.6) / 0.4 : 0;
      tool.position.y = 2.55 - descent * 0.32 + Math.sin(progress * Math.PI * 16) * 0.012;
      tool.rotation.z = -settle * 0.06;
      updateReadout(progress);
      if (progress >= 1) {
        running = false;
        document.querySelector("#station-pause")?.setAttribute("disabled", "");
        updateReadout(1, "risk");
        setText("#station-replay-cue", "最近窗口的多信号变化已进入风险卡，下一步由工程师决定点检和抽检范围。");
        window.dispatchEvent(new CustomEvent("qg:station-risk", { detail: { station: "ST-FAS-07", tool: "TQ-17", point: "P03" } }));
      }
    }
    if (visible) {
      if (renderer) renderer.render(scene, camera);
      else drawFallback(running ? elapsed / 12 : mode === "baseline" ? 0 : 1);
    }
    window.requestAnimationFrame(tick);
  }

  document.querySelector("#station-start")?.addEventListener("click", start);
  document.querySelector("#station-pause")?.addEventListener("click", pause);
  document.querySelector("#station-replay-button")?.addEventListener("click", reset);
  document.querySelector("#station-baseline")?.addEventListener("click", () => setMode("baseline"));
  document.querySelector("#start-live-test")?.addEventListener("click", start);
  document.querySelector("#stop-live-test")?.addEventListener("click", pause);
  document.querySelector("#reset-baseline")?.addEventListener("click", () => setMode("baseline"));
  document.querySelector("#inject-risk")?.addEventListener("click", () => setMode("risk"));
  document.querySelector("#station-show-evidence")?.addEventListener("click", () => document.querySelector("#evidence")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  document.querySelectorAll("[data-station-focus]").forEach((button) => button.addEventListener("click", () => focusObject(button.dataset.stationFocus)));
  window.addEventListener("qg:live-update", (event) => {
    const payload = event.detail || {};
    if (payload.card?.status === "monitoring_only") setMode("baseline");
    else if (payload.card) updateReadout(payload.running ? 0.72 : 1, "risk");
  });
  const observer = new IntersectionObserver((entries) => { visible = entries[0]?.isIntersecting !== false; }, { threshold: 0.02 });
  if (wrap) observer.observe(wrap);
  window.addEventListener("resize", resize, { passive: true });
  window.addEventListener("pagehide", () => observer.disconnect(), { once: true });
  window.stationReplay = { start, pause, reset, setMode };
  window.requestAnimationFrame(tick);
}
