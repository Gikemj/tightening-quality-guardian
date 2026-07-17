export function mean(values) {
  return values.reduce((total, value) => total + value, 0) / Math.max(values.length, 1);
}

export function standardDeviation(values) {
  if (values.length < 2) return 0;
  const center = mean(values);
  return Math.sqrt(mean(values.map((value) => (value - center) ** 2)));
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

export function analyzeSeries(series, baselineCount = 60, recentCount = 24) {
  if (series.length < baselineCount + recentCount) {
    throw new Error(`至少需要 ${baselineCount + recentCount} 条记录`);
  }

  const baseline = series.slice(0, baselineCount);
  const recent = series.slice(-recentCount);
  const baselineTorque = baseline.map((row) => Number(row.torque_nm));
  const recentTorque = recent.map((row) => Number(row.torque_nm));
  const baselineAngle = baseline.map((row) => Number(row.angle_deg));
  const recentAngle = recent.map((row) => Number(row.angle_deg));
  const torqueCenter = mean(baselineTorque);
  const torqueSigma = standardDeviation(baselineTorque) || 0.01;
  const meanShiftSigma = Math.abs(mean(recentTorque) - torqueCenter) / torqueSigma;
  const angleRatio = standardDeviation(recentAngle) / Math.max(standardDeviation(baselineAngle), 0.01);
  const retryBaseline = mean(baseline.map((row) => Number(row.retry_count)));
  const retryRecent = mean(recent.map((row) => Number(row.retry_count)));
  const retryDelta = retryRecent - retryBaseline;
  const signalActive = meanShiftSigma >= 1.2 || angleRatio >= 1.8 || retryDelta >= 0.08;

  const process = meanShiftSigma >= 1.2 ? 22 : 8;
  const equipment = clamp(Math.round(Math.max(angleRatio - 1, 0) * 9 + Math.max(retryDelta, 0) * 45), 0, 25);
  const quality = 22;
  const context = signalActive ? 13 : 4;
  const score = clamp(process + equipment + quality + context, 0, 100);
  const level = score >= 75 ? "high" : score >= 45 ? "medium" : "low";
  const inSpecRate = recentTorque.filter((value) => value >= 43 && value <= 53).length / recentTorque.length;

  return {
    score,
    level,
    meanShiftSigma,
    angleRatio,
    retryBaseline,
    retryRecent,
    recentMean: mean(recentTorque),
    baselineMean: torqueCenter,
    inSpecRate,
    breakdown: { process, equipment, quality, context },
  };
}

export function pathFor(values, width, height, minimum, maximum) {
  const span = Math.max(maximum - minimum, 0.01);
  return values
    .map((value, index) => {
      const x = values.length === 1 ? 0 : (index / (values.length - 1)) * width;
      const y = height - ((value - minimum) / span) * height;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}
