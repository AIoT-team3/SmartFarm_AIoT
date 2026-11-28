export const deviceLabel = (name: string) => name.replace(/^demo[\s_-]*/i, "");

export const number = (value: number | null | undefined, digits = 1) =>
  value == null || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("ko-KR", { maximumFractionDigits: digits }).format(
        value,
      );
export const dateTime = (value: string | null | undefined, full = false) =>
  value
    ? new Intl.DateTimeFormat("ko-KR", {
        timeZone: "Asia/Seoul",
        month: "numeric",
        day: "numeric",
        ...(full
          ? { hour: "2-digit", minute: "2-digit", hourCycle: "h23" as const }
          : {}),
      }).format(new Date(value))
    : "기록 없음";
export const clock = (value: number | string) =>
  new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
export const statusLabel = (status: string) =>
  ({
    pending: "대기 중",
    picked: "장비가 처리 중",
    processing: "분석 중",
    completed: "완료",
    done: "완료",
    failed: "실패",
  })[status] || status;
export function growthDay(sownAt: string) {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return Math.floor(
    (Date.parse(formatter.format(new Date())) -
      Date.parse(formatter.format(new Date(sownAt)))) /
      86400000,
  );
}
export const wateringLabel = (mode: unknown) =>
  mode === "predictive"
    ? "예측 기반 자동 급수"
    : mode === "threshold"
      ? "임계값 기반 자동 급수"
      : mode === "off"
        ? "급수 중지"
        : "적용 설정 대기";
