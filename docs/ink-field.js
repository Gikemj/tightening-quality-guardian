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
    context.fillStyle = "#e8e9e6";
    context.fillRect(0, 0, width, height);

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
      context.strokeStyle = streamIndex % 4 === 0 ? "rgba(75, 82, 83, 0.095)" : "rgba(133, 139, 136, 0.075)";
      context.lineWidth = stream.width * (streamIndex % 5 === 0 ? 2.4 : 1.25);
      context.stroke();
    });

    // A second set of short curved strokes creates the impression of ink pooling
    // without using a large image or a browser-dependent shader.
    for (let index = 0; index < 9; index += 1) {
      const x = width * (0.12 + index * 0.105);
      const y = height * (0.18 + ((index * 13) % 47) / 100);
      context.beginPath();
      context.arc(x, y, 34 + index * 7, Math.PI * 0.1, Math.PI * 1.15);
      context.strokeStyle = "rgba(109, 116, 112, 0.045)";
      context.lineWidth = 2;
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
