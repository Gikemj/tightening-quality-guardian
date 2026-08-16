import * as THREE from "./vendor/three.module.js";

const canvas = document.querySelector("#station-canvas");

if (canvas) {
  const wrap = document.querySelector("#station-canvas-wrap");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(31, 1, 0.1, 100);
  const cameraPosition = new THREE.Vector3();
  const cameraTarget = new THREE.Vector3();
  const desiredCameraPosition = new THREE.Vector3();
  const desiredCameraTarget = new THREE.Vector3();
  let renderer = null;
  let fallbackContext = null;

  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "low-power" });
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor(0x0b151a, 0);
  } catch (error) {
    // Headless review environments may disable WebGL. Keep the same four-shot
    // story visible with a lightweight 2D fallback instead of an empty stage.
    fallbackContext = canvas.getContext("2d");
    canvas.dataset.renderer = "2d-fallback";
  }

  const root = new THREE.Group();
  const tool = new THREE.Group();
  const spindle = new THREE.Group();
  const p03 = new THREE.Group();
  const signalLine = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-0.62, 0.88, 0.3),
      new THREE.Vector3(0.6, 1.32, -1.55),
      new THREE.Vector3(2.35, 1.42, -1.55),
    ]),
    new THREE.LineBasicMaterial({ color: 0xd5ae68, transparent: true, opacity: 0.2 }),
  );
  const p03Ring = new THREE.Mesh(
    new THREE.TorusGeometry(0.34, 0.035, 10, 32),
    new THREE.MeshBasicMaterial({ color: 0xc28c50, transparent: true, opacity: 0.78 }),
  );
  const p03Halo = new THREE.Mesh(
    new THREE.RingGeometry(0.42, 0.52, 40),
    new THREE.MeshBasicMaterial({ color: 0xc28c50, transparent: true, opacity: 0.13, side: THREE.DoubleSide }),
  );
  const frameMaterial = new THREE.MeshStandardMaterial({ color: 0x78909a, roughness: 0.68, metalness: 0.22 });
  const darkMaterial = new THREE.MeshStandardMaterial({ color: 0x1d2b32, roughness: 0.8, metalness: 0.12 });
  const panelMaterial = new THREE.MeshStandardMaterial({ color: 0xc6cfce, roughness: 0.76, metalness: 0.08 });
  const blueMaterial = new THREE.MeshStandardMaterial({ color: 0x4e7d8a, roughness: 0.54, metalness: 0.25 });
  const fastenerMaterial = new THREE.MeshStandardMaterial({ color: 0xd4ad69, roughness: 0.32, metalness: 0.7 });
  const toolMaterial = new THREE.MeshStandardMaterial({ color: 0xe0e4e1, roughness: 0.4, metalness: 0.48 });
  const screenMaterial = new THREE.MeshStandardMaterial({ color: 0x19313a, roughness: 0.36, metalness: 0.18, emissive: 0x0d252b, emissiveIntensity: 0.75 });
  let mode = "baseline";
  let running = false;
  let elapsed = 0;
  let speed = 2;
  let activeShot = "overview";
  let lastTick = performance.now();
  let visible = true;

  const shotDefinitions = {
    overview: {
      index: "01", label: "总览", kicker: "机位 01 · 总览", title: "确认工位、工具和关键点位",
      detail: "先锁定同一工位与同一程序分层，再观察过程信号如何进入风险卡。",
      position: [7.8, 5.3, 8.6], target: [0, 0.8, 0], focus: "station",
    },
    tool: {
      index: "02", label: "工具近景", kicker: "机位 02 · 工具近景", title: "观察 TQ-17 的连续拧紧动作",
      detail: "扭矩仍在规格内时，工具电流、角度离散与重试率共同提供设备侧线索。",
      position: [4.2, 3.0, 4.4], target: [-0.62, 1.35, 0.3], focus: "tool",
    },
    point: {
      index: "03", label: "P03 点位", kicker: "机位 03 · P03 点位", title: "把过程信号落到关键紧固点",
      detail: "P03 是设备信号与连接质量之间的工程接口，证据和后续点检都回到这里。",
      position: [2.45, 1.85, 2.65], target: [-0.62, 0.64, 0.3], focus: "point",
    },
    signal: {
      index: "04", label: "信号路径", kicker: "机位 04 · 信号路径", title: "沿数据路径进入人工复核",
      detail: "从 P03 到规则、证据和任务预览，系统只提出待验证假设，不自动停线。",
      position: [-3.8, 3.7, 5.1], target: [1.1, 1.35, -1.2], focus: "signal",
    },
  };

  function box(width, height, depth, x, y, z, material, parent = root) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), material);
    mesh.position.set(x, y, z);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
  }

  function cylinder(radius, height, x, y, z, material, parent = root, rotation = [0, 0, 0]) {
    const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius * 1.02, height, 24), material);
    mesh.position.set(x, y, z);
    mesh.rotation.set(...rotation);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
  }

  function setText(selector, value) {
    const element = document.querySelector(selector);
    if (element) element.textContent = value;
  }

  function setShot(nextShot, immediate = false) {
    const definition = shotDefinitions[nextShot] || shotDefinitions.overview;
    activeShot = nextShot in shotDefinitions ? nextShot : "overview";
    desiredCameraPosition.set(...definition.position);
    desiredCameraTarget.set(...definition.target);
    if (immediate) {
      cameraPosition.copy(desiredCameraPosition);
      cameraTarget.copy(desiredCameraTarget);
      camera.position.copy(cameraPosition);
      camera.lookAt(cameraTarget);
    }
    setText("#station-shot-kicker", definition.kicker);
    setText("#station-shot-title", definition.title);
    setText("#station-shot-detail", definition.detail);
    document.querySelectorAll("[data-station-shot]").forEach((button) => {
      const selected = button.dataset.stationShot === activeShot;
      button.classList.toggle("is-selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    document.querySelectorAll("[data-station-marker]").forEach((marker) => marker.classList.toggle("is-active", marker.dataset.stationMarker === activeShot));
    document.querySelectorAll("[data-station-focus]").forEach((button) => button.classList.toggle("is-selected", button.dataset.stationFocus === definition.focus));
  }

  function shotForProgress(progress) {
    if (progress < 0.25) return "overview";
    if (progress < 0.5) return "tool";
    if (progress < 0.75) return "point";
    return "signal";
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
    setText("#station-replay-clock", `00:${String(Math.min(59, Math.round(progress * 8))).padStart(2, "0")}`);
    setText(
      "#station-replay-cue",
      activeMode === "baseline"
        ? "当前窗口保持稳定，系统不生成候选原因或处置任务。"
        : risk
          ? "扭矩仍在规格内，但多信号同向变化，风险卡进入人工复核。"
          : `机位 ${shotDefinitions[activeShot].index} 正在回放：${shotDefinitions[activeShot].title}`,
    );
    const bar = document.querySelector("#station-progress-bar");
    if (bar) bar.style.width = `${Math.round(progress * 100)}%`;
    const color = activeMode === "baseline" ? 0x72a785 : risk ? 0xc26a4d : 0xc28c50;
    p03Ring.material.color.setHex(color);
    p03Halo.material.color.setHex(color);
    p03Ring.material.opacity = risk ? 0.96 : 0.68;
    p03Halo.material.opacity = risk ? 0.22 : 0.11;
    signalLine.material.opacity = risk ? 0.86 : 0.24;
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
    elapsed = nextMode === "baseline" ? 0 : 8;
    updateReadout(nextMode === "baseline" ? 0 : 1, nextMode);
    tool.position.y = nextMode === "baseline" ? 2.55 : 2.18;
    tool.rotation.z = nextMode === "baseline" ? 0 : -0.06;
    setShot(nextMode === "baseline" ? "overview" : "signal");
    document.querySelector("#station-pause")?.setAttribute("disabled", "");
  }

  function start() {
    mode = "risk";
    if (reducedMotion.matches) {
      elapsed = 8;
      running = false;
      setShot("signal", true);
      updateReadout(1, "risk");
      return;
    }
    if (elapsed >= 8) elapsed = 0;
    running = true;
    setShot(shotForProgress(elapsed / 8));
    updateReadout(elapsed / 8);
    document.querySelector("#station-pause")?.removeAttribute("disabled");
    setText("#station-replay-cue", `回放进行中 · ${speed}×：镜头将依次切换总览、工具近景、P03 点位和信号路径。`);
  }

  function pause() {
    running = false;
    document.querySelector("#station-pause")?.setAttribute("disabled", "");
    setText("#station-replay-cue", "回放已暂停。可以切换机位查看对象，也可以继续播放到风险卡状态。");
  }

  function reset() {
    mode = "risk";
    elapsed = 0;
    running = false;
    tool.position.y = 2.55;
    tool.rotation.z = 0;
    setShot("overview", true);
    updateReadout(0, "risk");
    document.querySelector("#station-pause")?.setAttribute("disabled", "");
    setText("#station-replay-cue", "点击“开始回放”，按四个工程机位观察 P03 从稳定窗口进入待复核状态。");
  }

  function resize() {
    const bounds = wrap?.getBoundingClientRect();
    const width = Math.max(320, bounds?.width || 800);
    const height = Math.max(280, bounds?.height || 480);
    const ratio = Math.min(window.devicePixelRatio || 1, 1.35);
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
    const gradient = ctx.createLinearGradient(0, 0, 800, 430);
    gradient.addColorStop(0, "#172a31");
    gradient.addColorStop(1, "#0b151a");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 800, 430);
    ctx.strokeStyle = "rgba(171,207,203,.17)";
    ctx.lineWidth = 1;
    for (let x = 34; x < 790; x += 56) { ctx.beginPath(); ctx.moveTo(x, 328); ctx.lineTo(x, 398); ctx.stroke(); }
    for (let z = 330; z < 400; z += 28) { ctx.beginPath(); ctx.moveTo(24, z); ctx.lineTo(786, z); ctx.stroke(); }
    ctx.fillStyle = "#78909a"; ctx.fillRect(94, 112, 612, 11); ctx.fillRect(98, 122, 12, 184); ctx.fillRect(690, 122, 12, 184);
    ctx.fillStyle = "#c6cfce"; ctx.fillRect(192, 218, 416, 66);
    ctx.fillStyle = "#4e7d8a"; ctx.fillRect(276, 274, 248, 22);
    ctx.fillStyle = "#d4ad69"; ctx.beginPath(); ctx.arc(342, 304, 13, 0, Math.PI * 2); ctx.fill();
    const p = Math.min(1, Math.max(0, progress));
    const shotScale = activeShot === "point" ? 1.22 : activeShot === "tool" ? 1.08 : 1;
    const toolY = 108 + p * 112;
    ctx.save(); ctx.translate(342, toolY); ctx.scale(shotScale, shotScale); ctx.rotate(-(p > .6 ? .06 : 0));
    ctx.fillStyle = "#e0e4e1"; ctx.fillRect(-18, -52, 36, 82); ctx.fillStyle = "#1d2b32"; ctx.fillRect(-23, 30, 46, 14); ctx.fillStyle = "#d4ad69"; ctx.fillRect(-7, 44, 14, 27); ctx.fillStyle = "#4e7d8a"; ctx.fillRect(18, -25, 8, 48); ctx.restore();
    const ringColor = mode === "baseline" ? "#72a785" : p > .48 ? "#c26a4d" : "#c28c50";
    ctx.strokeStyle = ringColor; ctx.lineWidth = 4; ctx.beginPath(); ctx.arc(342, 304, 25, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = `${ringColor}66`; ctx.lineWidth = 10; ctx.beginPath(); ctx.arc(342, 304, 34 + Math.sin(p * Math.PI * 8) * 3, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = "#d4ad69"; ctx.font = "600 13px ui-monospace, monospace"; ctx.fillText(`CAM ${shotDefinitions[activeShot].index} · ${shotDefinitions[activeShot].label}`, 22, 34);
    ctx.restore();
  }

  // Scene layout: an original, low-detail workcell built from primitives. The
  // camera changes are intentional: each shot answers one engineering question.
  scene.add(new THREE.HemisphereLight(0xe9f1ef, 0x22313a, 1.7));
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.4);
  keyLight.position.set(4, 7, 5);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(512, 512);
  scene.add(keyLight);
  const rimLight = new THREE.PointLight(0x7ab5bc, 1.1, 12);
  rimLight.position.set(-3, 3.5, 2.5);
  scene.add(rimLight);
  scene.add(root);
  root.rotation.y = -0.34;
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
  for (const x of [-0.62, 0.62]) cylinder(0.22, 0.12, x, 0.72, 0.3, fastenerMaterial, root);
  p03.position.set(-0.62, 0.79, 0.3);
  p03Ring.rotation.x = Math.PI / 2;
  p03Halo.rotation.x = Math.PI / 2;
  p03.add(p03Ring, p03Halo);
  root.add(p03);
  const monitor = new THREE.Group();
  box(1.5, 0.95, 0.09, 3.15, 1.55, -1.58, darkMaterial, monitor);
  box(1.26, 0.68, 0.02, 3.15, 1.55, -1.525, screenMaterial, monitor);
  box(0.16, 0.42, 0.16, 3.15, 0.92, -1.58, frameMaterial, monitor);
  root.add(monitor);
  root.add(signalLine);
  tool.position.set(-0.62, 2.55, 0.3);
  tool.rotation.z = 0;
  box(0.46, 1.18, 0.46, 0, 0, 0, toolMaterial, tool);
  box(0.58, 0.22, 0.58, 0, -0.68, 0, darkMaterial, tool);
  tool.add(spindle);
  cylinder(0.14, 0.44, 0, -0.92, 0, fastenerMaterial, spindle);
  box(0.12, 0.7, 0.12, 0.26, 0.12, 0, blueMaterial, tool);
  root.add(tool);
  const cable = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(2.3, -1.2, 0.2), new THREE.Vector3(4.1, -1.2, 0.2)]),
    new THREE.LineBasicMaterial({ color: 0x27343c, transparent: true, opacity: 0.85 }),
  );
  cable.position.set(-0.35, 2.2, 0.3);
  tool.add(cable);
  setShot("overview", true);
  resize();
  updateReadout(0, "baseline");
  focusObject("station");

  function tick(now) {
    const delta = Math.min(0.05, (now - lastTick) / 1000);
    lastTick = now;
    if (running) {
      elapsed += delta * speed;
      const progress = Math.min(1, elapsed / 8);
      const shot = shotForProgress(progress);
      if (shot !== activeShot) setShot(shot);
      const descent = progress < 0.3 ? progress / 0.3 : 1;
      const settle = progress > 0.6 ? (progress - 0.6) / 0.4 : 0;
      tool.position.y = 2.55 - descent * 0.32 + Math.sin(progress * Math.PI * 16) * 0.012;
      tool.rotation.z = -settle * 0.06;
      spindle.rotation.y += delta * 18 * speed;
      const pulse = 1 + Math.sin(now * 0.008) * (progress > 0.52 ? 0.1 : 0.035);
      p03Halo.scale.setScalar(pulse);
      updateReadout(progress);
      if (progress >= 1) {
        running = false;
        document.querySelector("#station-pause")?.setAttribute("disabled", "");
        setShot("signal");
        updateReadout(1, "risk");
        setText("#station-replay-cue", "四个机位已完成：最近窗口的多信号变化已进入风险卡，下一步由工程师决定点检和抽检范围。");
        window.dispatchEvent(new CustomEvent("qg:station-risk", { detail: { station: "ST-FAS-07", tool: "TQ-17", point: "P03" } }));
      }
    }
    cameraPosition.lerp(desiredCameraPosition, reducedMotion.matches ? 1 : Math.min(1, delta * 5.5));
    cameraTarget.lerp(desiredCameraTarget, reducedMotion.matches ? 1 : Math.min(1, delta * 6.5));
    camera.position.copy(cameraPosition);
    camera.lookAt(cameraTarget);
    if (visible) {
      if (renderer) renderer.render(scene, camera);
      else drawFallback(running ? elapsed / 8 : mode === "baseline" ? 0 : 1);
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
  document.querySelectorAll("[data-station-shot]").forEach((button) => button.addEventListener("click", () => setShot(button.dataset.stationShot)));
  document.querySelectorAll("[data-station-speed]").forEach((button) => button.addEventListener("click", () => {
    speed = Math.max(1, Math.min(4, Number(button.dataset.stationSpeed) || 2));
    document.querySelectorAll("[data-station-speed]").forEach((item) => {
      const selected = item === button;
      item.classList.toggle("is-selected", selected);
      item.setAttribute("aria-pressed", String(selected));
    });
    setText("#station-replay-cue", running ? `回放进行中 · ${speed}×：镜头按工程顺序推进。` : `已选择 ${speed}× 回放速度，点击开始回放。`);
  }));
  document.querySelectorAll("[data-station-focus]").forEach((button) => button.addEventListener("click", () => {
    const focus = button.dataset.stationFocus;
    focusObject(focus);
    const shot = Object.entries(shotDefinitions).find(([, definition]) => definition.focus === focus)?.[0];
    if (shot) setShot(shot);
  }));
  window.addEventListener("qg:live-update", (event) => {
    const payload = event.detail || {};
    if (payload.card?.status === "monitoring_only") setMode("baseline");
    else if (payload.card) {
      // A completed workcell replay must stay completed when the background
      // simulator publishes another batch. Running feeds show the live
      // signal path at 72%; an idle feed is rendered at the completed risk
      // state instead of snapping the progress bar back to 72%.
      setShot("signal");
      if (running || elapsed < 8) updateReadout(payload.running && running ? 0.72 : 1, "risk");
    }
  });
  const observer = new IntersectionObserver((entries) => { visible = entries[0]?.isIntersecting !== false; }, { threshold: 0.02 });
  if (wrap) observer.observe(wrap);
  window.addEventListener("resize", resize, { passive: true });
  window.addEventListener("pagehide", () => observer.disconnect(), { once: true });
  window.stationReplay = { start, pause, reset, setMode, setShot };
  window.requestAnimationFrame(tick);
}
