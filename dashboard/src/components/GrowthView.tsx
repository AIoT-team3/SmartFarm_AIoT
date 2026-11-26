import {
  ChartNoAxesCombined,
  CalendarDays,
  CheckCheck,
} from "lucide-react";
import type { Growth, Scan } from "../types";
import { dateTime, number } from "../format";
import { GrowthChart } from "./Charts";
import { ErrorNote, Metric } from "./UI";

export function GrowthView({
  scans,
  growth,
  error,
}: {
  scans: Scan[];
  growth?: Growth;
  error: string | null;
}) {
  const summary = growth?.backtest_summary;
  const evaluated =
    growth?.predictions.flatMap((item) =>
      item.backtest ? [{ day: item.target_day, ...item.backtest }] : [],
    ) || [];
  return (
    <>
      <div className="section-title-row">
        <div>
          <h1>조금씩 자라는, 매일의 변화</h1>
          <p>
            최근 30일 스캔 관측값과 선택된 모델의 생장 예측을 함께 확인해요.
          </p>
        </div>
      </div>
      <ErrorNote message={error} />
      <div className="growth-context">
        <span>
          <span className="legend-dot green" />
          스캔 관측
        </span>
        <span>
          <span className="legend-line purple" />
          예측 평균
        </span>
        <span>
          <span className="legend-band" />키 예측 80% 구간
        </span>
        <span className="context-date">
          기준 스캔 · {dateTime(growth?.origin_scan?.ts, true)}
        </span>
      </div>
      {growth?.status === "no_model" && (
        <div className="info-note">
          활성 생장 모델이 아직 등록되지 않았어요. 저장된 스캔 관측값을 먼저
          확인할 수 있어요.
        </div>
      )}
      {growth?.status === "no_prediction" && (
        <div className="info-note">
          이 모델에 저장된 예측이 아직 없어요. 예측 결과가 저장되면 자동으로
          표시돼요.
        </div>
      )}
      <section className="card growth-height">
        <div className="card-heading">
          <div>
            <h2>키</h2>
            <p>cm · 스캔 관측과 예측 평균</p>
          </div>
          <ChartNoAxesCombined size={20} className="muted" />
        </div>
        <GrowthChart scans={scans} growth={growth} />
      </section>
      <div className="two-columns lower-growth">
        <section className="card">
          <div className="card-heading">
            <div>
              <span className="eyebrow">PREDICTION ACCURACY</span>
              <h2>예측과 실제, 얼마나 가까웠을까요?</h2>
            </div>
            <CheckCheck size={20} className="muted" />
          </div>
          <div className="score-row">
            <div>
              <strong>
                {number(summary?.mae_cm, 2)}
                <small>cm</small>
              </strong>
              <p>평균 절대 오차 · 채점 {summary?.evaluated_count ?? 0}건</p>
            </div>
            <Metric
              label="예측 구간 적중률"
              value={number(
                summary?.band_hit_rate == null
                  ? null
                  : summary.band_hit_rate * 100,
                0,
              )}
              unit="%"
              note={`구간 채점 ${summary?.band_evaluated_count ?? 0}건`}
            />
          </div>
          <p className="footnote">
            현재 화면의 기준 스캔·모델에 연결된 채점만 집계해요.
          </p>
        </section>
        <section className="card">
          <div className="card-heading">
            <div>
              <span className="eyebrow">HARVEST ESTIMATE</span>
              <h2>수확 예상</h2>
            </div>
            <CalendarDays size={20} className="muted" />
          </div>
          <div className="harvest-value">
            {growth?.harvest.status === "available"
              ? dateTime(growth.harvest.estimated_date)
              : "아직 기다리고 있어요"}
          </div>
          <p className="muted">
            {growth?.harvest.status === "available"
              ? growth.harvest.remaining_days === 0
                ? "키 25cm 기준을 충족해 수확할 수 있어요."
                : `${growth.harvest.remaining_days ?? "—"}일 뒤 · 키 25cm 이상 예상`
              : growth?.harvest.status === "not_reached"
                ? "저장된 예측 범위에서 키 25cm에 아직 도달하지 않았어요."
                : "수확 기준과 계산 결과가 준비되면 표시돼요."}
          </p>
        </section>
      </div>
      {evaluated.length > 0 && (
        <section className="card">
          <div className="card-heading">
            <h2>대상일별 예측 채점</h2>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>대상일</th>
                  <th>예측 간격</th>
                  <th>예측 키</th>
                  <th>실측 키</th>
                  <th>오차</th>
                  <th>구간 포함</th>
                </tr>
              </thead>
              <tbody>
                {evaluated.map((item) => (
                  <tr key={item.prediction_id}>
                    <td>D+{item.day}</td>
                    <td>{item.horizon}일 뒤</td>
                    <td>{number(item.predicted)} cm</td>
                    <td>{number(item.observed)} cm</td>
                    <td>{number(item.abs_err, 2)} cm</td>
                    <td>
                      {item.in_band == null
                        ? "—"
                        : item.in_band
                          ? "포함"
                          : "미포함"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
