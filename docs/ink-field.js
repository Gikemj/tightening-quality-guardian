const canvas = document.querySelector("#ink-field");

if (canvas) {
  const context = canvas.getContext("2d", { alpha: true });
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const washes = [
    { x: 0.08, y: 0.17, size: 0.26, hue: "37, 77, 82", alpha: 0.27, phase: 0.4 },
    { x: 0.88, y: 0.28, size: 0.23, hue: "34, 69, 76", alpha: 0.24, phase: 1.8 },
    { x: 0.74, y: 0.86, size: 0.31, hue: "53, 91, 87", alpha: 0.22, phase: 3.2 },
    { x: 0.20, y: 0.78, size: 0.18, hue: "54, 77, 80", alpha: 0.18, phase: 4.6 },
  ];
  const filaments = Array.from({ length: 12 }, (_, index) => ({
    offset: (index + 0.4) / 12,
    phase: index * 0.63,
    width: 0.65 + (index % 4) * 0.22,
    alpha: index % 3 === 0 ? 0.22 : 0.12,
  }));
  let width = 0;
  let height = 0;
  let ratio = 1;
  let frame = 0;
  let startedAt = performance.now();

  function resize() {
    ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    width = Math.max(320, window.innerWidth);
    height = Math.max(480, window.innerHeight);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  function blob(wash, elapsed) {
    const motion = reducedMotion.matches ? 0 : elapsed * 0.000025;
    const x = width * (wash.x + Math.sin(motion * (0.7 + wash.phase * 0.04) + wash.phase) * 0.035);
    const y = height * (wash.y + Math.cos(motion * 0.8 + wash.phase) * 0.035);
    const radius = Math.max(170, Math.min(width, height) * wash.size);
    const gradient = context.createRadialGradient(x, y, radius * 0.05, x, y, radius);
    gradient.addColorStop(0, `rgba(${wash.hue}, ${wash.alpha})`);
    gradient.addColorStop(0.32, `rgba(${wash.hue}, ${wash.alpha * 0.54})`);
    gradient.addColorStop(0.72, `rgba(${wash.hue}, ${wash.alpha * 0.14})`);
    gradient.addColorStop(1, `rgba(${wash.hue}, 0)`);
    context.fillStyle = gradient;
    context.beginPath();
    context.ellipse(x, y, radius * 1.18, radius * 0.72, Math.sin(wash.phase) * 0.55, 0, Math.PI * 2);
    context.fill();

    // A few translucent lobes make the wash look like pooled pigment rather
    // than a perfect radial gradient, while keeping the asset resolution-free.
    context.fillStyle = `rgba(${wash.hue}, ${wash.alpha * 0.22})`;
    context.beginPath();
    for (let index = 0; index <= 18; index += 1) {
      const angle = (index / 18) * Math.PI * 2;
      const wobble = 1 + Math.sin(angle * 3 + wash.phase + motion * 2) * 0.12;
      const px = x + Math.cos(angle) * radius * 0.7 * wobble;
      const py = y + Math.sin(angle) * radius * 0.46 * wobble;
      if (index === 0) context.moveTo(px, py);
      else context.lineTo(px, py);
    }
    context.closePath();
    context.fill();
  }

  function paint(now = performance.now()) {
    const elapsed = reducedMotion.matches ? 0 : now - startedAt;
    context.clearRect(0, 0, width, height);
    washes.forEach((wash) => blob(wash, elapsed));

    // Long, broken brush paths tie the four pools together. Their opacity is
    // deliberately low beneath the white work panels, but visible at the
    // page margins and between sections.
    filaments.forEach((filament, lineIndex) => {
      const baseY = (filament.offset * 1.12 - 0.06) * height;
      const drift = reducedMotion.matches ? 0 : elapsed * (0.000018 + lineIndex * 0.0000015);
      context.beginPath();
      for (let step = 0; step <= 90; step += 1) {
        const x = (step / 90) * width;
        const wave = Math.sin(x * 0.0044 + filament.phase + drift) * height * 0.045 * filament.width;
        const fold = Math.sin(x * 0.011 - drift * 1.7 + lineIndex) * height * 0.016;
        const y = baseY + wave + fold + Math.sin((x / width) * Math.PI) * (lineIndex % 3) * 3;
        if (step === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      }
      context.strokeStyle = `rgba(28, 61, 65, ${filament.alpha})`;
      context.lineWidth = lineIndex % 4 === 0 ? 3.2 : 1.35;
      context.lineCap = "round";
      context.stroke();
    });

    // Small dry-brush crescents are sparse enough to read as texture, not as
    // an animated pattern behind the actual controls.
    for (let index = 0; index < 10; index += 1) {
      const x = width * (0.05 + index * 0.104);
      const y = height * (0.16 + ((index * 17) % 66) / 100);
      const radius = 26 + (index % 4) * 11;
      context.beginPath();
      context.arc(x, y, radius, Math.PI * (0.08 + (index % 3) * 0.05), Math.PI * (0.76 + (index % 4) * 0.12));
      context.strokeStyle = "rgba(29, 63, 66, 0.15)";
      context.lineWidth = index % 3 === 0 ? 2.6 : 1.2;
      context.stroke();
    }
  }

  function render(now) {
    paint(now);
    frame = window.requestAnimationFrame(render);
  }

  resize();
  paint();
  window.addEventListener("resize", resize, { passive: true });
  reducedMotion.addEventListener?.("change", paint);
  frame = window.requestAnimationFrame(render);
  window.addEventListener("pagehide", () => window.cancelAnimationFrame(frame), { once: true });
}
