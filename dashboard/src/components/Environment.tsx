import {
  Droplets,
  Thermometer,
  History,
  Sun,
  Check,
  AlertCircle,
} from "lucide-react";
import type { Actuation, Overview, SensorReading } from "../types";
import { dateTime, deviceLabel, number, wateringLabel } from "../format";
import { SensorChart } from "./Charts";
import { Empty, ErrorNote } from "./UI";

export function Environment({
  overview,
  sensors,
  actuations,
  days,
  setDays,
  error,
  historyError,
  loading,
}: {
  overview?: Overview;
  sensors: SensorReading[];
  actuations: Actuation[];
  days: number;
  setDays: (days: number) => void;
  error: string | null;
  historyError: string | null;
  loading: boolean;
}) {
  const values = overview?.latest_sensor;
  return (
    <>
      <div className="section-title-row">
        <div>
          <h1>편안한 환경, 꾸준한 기록</h1>
          <p>온습도는 10분 평균으로, 급수는 실제 실행 기록으로 확인해요.</p>
        </div>
        <div className="segmented" aria-label="조회 기간">
          {[1, 7, 30].map((day) => (
            <button
              key={day}
              onClick={() => setDays(day)}
              className={days === day ? "selected" : ""}
              aria-pressed={days === day}
            >
              {day === 1 ? "24시간" : `${day}일`}
            </button>
          ))}
        </div>
      </div>
      <ErrorNote message={error} />
      <div className="two-columns">
        <section className="card">
          <div className="card-heading">
            <div>
              <span className="eyebrow">TEMPERATURE</span>
              <h2>기온</h2>
            </div>
            <Thermometer className="muted" size={20} />
          </div>
          <div className="large-reading">
            {number(values?.air_temp_c)}
            <span>°C</span>
          </div>
          {loading ? (
            <div className="loading-panel">측정 기록을 불러오는 중…</div>
          ) : (
            <SensorChart
              data={sensors}
              field="air_temp_c"
              color="#528866"
              unit="°C"
            />
          )}
        </section>
        <section className="card">
          <div className="card-heading">
            <div>
              <span className="eyebrow">HUMIDITY</span>
              <h2>상대습도</h2>
            </div>
            <Droplets className="muted" size={20} />
          </div>
          <div className="large-reading">
            {number(values?.rh_pct)}
            <span>%</span>
          </div>
          {loading ? (
            <div className="loading-panel">측정 기록을 불러오는 중…</div>
          ) : (
            <SensorChart
              data={sensors}
              field="rh_pct"
              color="#7c9ead"
              unit="%"
            />
          )}
        </section>
      </div>
      <div className="history-layout">
        <section className="card">
          <div className="card-heading">
            <div>
              <span className="eyebrow">ACTUATION HISTORY</span>
              <h2>급수·조명 실행 이력</h2>
              <p>선택한 기간에 장비가 보고한 결과예요.</p>
            </div>
            <History size={20} className="muted" />
          </div>
          <ErrorNote message={historyError} />
          {!actuations.length ? (
            <Empty
              title="아직 제어 기록이 없어요"
              detail="자동 급수나 조명 제어 결과가 전송되면 표시돼요."
            />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>실행 시각</th>
                    <th>종류</th>
                    <th>수행량</th>
                    <th>실행 주체</th>
                    <th>결과</th>
                  </tr>
                </thead>
                <tbody>
                  {actuations.map((item) => (
                    <tr key={item.id}>
                      <td>{dateTime(item.ts, true)}</td>
                      <td>
                        <span className="inline-icon">
                          {item.kind === "water" ? (
                            <Droplets size={14} />
                          ) : (
                            <Sun size={14} />
                          )}
                          {item.kind === "water" ? "급수" : "조명"}
                        </span>
                      </td>
                      <td>
                        {number(item.amount)} {item.unit || ""}
                      </td>
                      <td>
                        {(
                          {
                            auto: "자동",
                            manual: "수동",
                            guard: "보호 제어",
                          } as Record<string, string>
                        )[item.source] || item.source}
                      </td>
                      <td>
                        <span
                          className={`status-pill ${item.ok ? "success" : "danger"}`}
                        >
                          {item.ok ? (
                            <Check size={12} />
                          ) : (
                            <AlertCircle size={12} />
                          )}{" "}
                          {item.ok ? "성공" : "실패"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <aside className="card settings-summary">
          <span className="eyebrow">WATERING</span>
          <h2>자동 급수</h2>
          <div className="watering-icon">
            <Droplets size={28} />
          </div>
          <h3>
            {wateringLabel(overview?.settings?.applied_settings.watering_mode)}
          </h3>
          <p>장비에서 마지막으로 확인된 적용 설정이에요.</p>
          <dl>
            <dt>설정 보고 장비</dt>
            <dd>{overview?.settings?.device_name ? deviceLabel(overview.settings.device_name) : "—"}</dd>
            <dt>적용 버전</dt>
            <dd>
              {overview?.settings
                ? `v${overview.settings.settings_revision}`
                : "—"}
            </dd>
            <dt>적용 시각</dt>
            <dd>{dateTime(overview?.settings?.settings_applied_at, true)}</dd>
            <dt>최근 급수</dt>
            <dd>
              {dateTime(
                actuations.find((item) => item.kind === "water" && item.ok)?.ts,
                true,
              )}
            </dd>
          </dl>
        </aside>
      </div>
    </>
  );
}
