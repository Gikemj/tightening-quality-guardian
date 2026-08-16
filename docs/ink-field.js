const canvas = document.querySelector("#ink-field");

if (canvas) {
  const context = canvas.getContext("2d", { alpha: true });
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const streams = Array.from({ length: 18 }, (_, index) => ({
    offset: index / 18,
    width: 0.7 + ((index * 7) % 9) / 12,
    speed: 0.000018 + ((index * 3) % 5) * 0.000004,
    phase: index * 0.71,
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

  function paint(now = performance.now()) {
    const elapsed = reducedMotion.matches ? 0 : now - startedAt;
    context.clearRect(0, 0, width, height);

    // Transparent pigment lets the page's cool paper tone remain readable
    // while the moving washes are visibly present around and beneath panels.
    const wash = (x, y, radius, alpha) => {
      const gradient = context.createRadialGradient(x, y, radius * 0.08, x, y, radius);
      gradient.addColorStop(0, `rgba(44, 78, 79, ${alpha})`);
      gradient.addColorStop(0.48, `rgba(70, 111, 108, ${alpha * 0.35})`);
      gradient.addColorStop(1, "rgba(70, 111, 108, 0)");
      context.fillStyle = gradient;
      context.beginPath();
      context.arc(x, y, radius, 0, Math.PI * 2);
      context.fill();
    };
    const drift = reducedMotion.matches ? 0 : elapsed * 0.00003;
    wash(width * (0.16 + Math.sin(drift * 1.7) * 0.035), height * 0.18, Math.max(180, width * 0.2), 0.24);
    wash(width * (0.82 + Math.cos(drift * 1.2) * 0.04), height * 0.72, Math.max(220, width * 0.24), 0.18);
    wash(width * (0.46 + Math.sin(drift * 0.8) * 0.05), height * 1.04, Math.max(190, width * 0.22), 0.14);

    streams.forEach((stream, streamIndex) => {
      const baseY = (stream.offset * 1.18 - 0.08) * height;
      context.beginPath();
      for (let step = 0; step <= 72; step += 1) {
        const x = (step / 72) * width;
        const wave = Math.sin(x * 0.006 + stream.phase + elapsed * stream.speed) * (height * 0.055 * stream.width);
        const curl = Math.sin(x * 0.014 - elapsed * stream.speed * 1.7 + streamIndex) * (height * 0.018);
        const y = baseY + wave + curl + Math.sin((x / width) * Math.PI) * (streamIndex % 3) * 5;
        if (step === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      }
      context.strokeStyle = streamIndex % 4 === 0 ? "rgba(35, 66, 68, 0.20)" : "rgba(62, 94, 92, 0.13)";
      context.lineWidth = stream.width * (streamIndex % 5 === 0 ? 3.2 : 1.65);
      context.stroke();
    });

    // A second set of short curved strokes creates the impression of ink pooling
    // without using a large image or a browser-dependent shader.
    for (let index = 0; index < 9; index += 1) {
      const x = width * (0.12 + index * 0.105);
      const y = height * (0.18 + ((index * 13) % 47) / 100);
      context.beginPath();
      context.arc(x, y, 34 + index * 7, Math.PI * 0.1, Math.PI * 1.15);
      context.strokeStyle = "rgba(48, 84, 82, 0.09)";
      context.lineWidth = 2.4;
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
