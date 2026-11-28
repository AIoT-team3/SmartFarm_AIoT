import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import {
  ArrowUpRight,
  Camera,
  Check,
  ChevronRight,
  CircleStop,
  Clock3,
  Droplets,
  Leaf,
  LoaderCircle,
  Monitor,
  Radio,
  RefreshCw,
  ScanLine,
  Settings2,
  Sprout,
  Thermometer,
  Wifi,
  WifiOff,
} from "lucide-react";
import {
  ApiError,
  getAllPages,
  historyQuery,
  request,
  useResource,
} from "./api";
import type {
  Actuation,
  Command,
  Growth,
  Overview,
  Prediction,
  Scan,
  ScanDetail,
  SensorReading,
  Tab,
} from "./types";
import {
  clock,
  deviceLabel,
  dateTime,
  growthDay,
  number,
  statusLabel,
  wateringLabel,
} from "./format";
const PlantScene = lazy(() =>
  import("./components/PlantScene").then((module) => ({
    default: module.PlantScene,
  })),
);
import { Empty, ErrorNote, Metric, Modal } from "./components/UI";
import { GrowthView } from "./components/GrowthView";
import { ScanPanel } from "./components/ScanPanel";
import { Environment } from "./components/Environment";

const samplePreview = import.meta.env.VITE_SAMPLE_PREVIEW === "true";

const tabs: { id: Tab; label: string }[] = [
  { id: "today", label: "지금" },
  { id: "growth", label: "생장" },
  { id: "environment", label: "환경" },
  { id: "scans", label: "스캔" },
  { id: "devices", label: "장비" },
];
function savedPlant() {
  try {
    const value = Number(localStorage.getItem("greentwin-plant") || "1");
    return Number.isSafeInteger(value) && value > 0 ? value : 1;
  } catch {
    return 1;
  }
}

export default function App() {
  const [tab, setTab] = useState<Tab>("today");
  const [plantId, setPlantId] = useState(savedPlant);
  const [version, setVersion] = useState(0);
  const [days, setDays] = useState(1);
  const [selectedScan, setSelectedScan] = useState<string | null>(null);
  const [frameKey, setFrameKey] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [draftPlant, setDraftPlant] = useState(String(plantId));
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [action, setAction] = useState<{
    kind: "scan_now" | "estop";
    requestId: string;
    plant: number;
  } | null>(null);
  const [password, setPassword] = useState("");
  const [sending, setSending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [command, setCommand] = useState<Command | null>(null);
  const [commandNotice, setCommandNotice] = useState(false);
  const base = `/api/v1/plants/${plantId}`;
  const overview = useResource<Overview>(
    `${base}/overview:${version}`,
    (signal) => request(`${base}/overview`, { signal }),
  );
  const growth = useResource<Growth>(
    `${base}/growth:${version}`,
    (signal) => request(`${base}/growth`, { signal }),
    30000,
  );
  const scans = useResource<Scan[]>(
    `${base}/scans:${version}`,
    (signal) =>
      getAllPages(`${base}/scans?${historyQuery(30, Date.now())}`, signal),
    30000,
  );
  const sensors = useResource<SensorReading[]>(
    `${base}/sensor-readings:${days}:${version}`,
    (signal) =>
      getAllPages(
        `${base}/sensor-readings?${historyQuery(days, Date.now())}&interval=10m`,
        signal,
      ),
    30000,
  );
  const actuations = useResource<Actuation[]>(
    `${base}/actuations:${days}:${version}`,
    (signal) =>
      getAllPages(
        `${base}/actuations?${historyQuery(days, Date.now())}`,
        signal,
      ),
    30000,
  );
  const scanId = selectedScan || scans.data?.[0]?.id;
  const detail = useResource<ScanDetail>(
    scanId ? `/api/v1/scans/${scanId}:${version}` : null,
    (signal) => request(`/api/v1/scans/${scanId}`, { signal }),
    30000,
  );
  const commandResult = useResource<Command>(
    command ? `/api/v1/commands/${command.id}` : null,
    (signal) => request(`/api/v1/commands/${command?.id}`, { signal }),
    command && !["done", "failed"].includes(command.status) ? 2500 : 0,
  );
  useEffect(() => {
    if (commandResult.data) setCommand(commandResult.data);
  }, [commandResult.data]);

  const record = overview.data;
  const latestScan = scans.data?.[0];
  const frames = useMemo(() => {
    const observed = [...(scans.data || [])]
      .sort((a, b) => a.ts.localeCompare(b.ts))
      .map((scan) => ({
        key: `scan:${scan.id}`,
        day: scan.day,
        ts: scan.ts,
        height: scan.height_cm,
        scan,
        prediction: null as Prediction | null,
      }));
    const predicted = (growth.data?.predictions || []).map((prediction) => ({
      key: `prediction:${prediction.id}`,
      day: prediction.target_day,
      ts: prediction.target_date,
      height: prediction.height_mu,
      scan: null as Scan | null,
      prediction,
    }));
    return [...observed, ...predicted].sort(
      (a, b) =>
        a.day - b.day ||
        Number(Boolean(a.prediction)) - Number(Boolean(b.prediction)),
    );
  }, [scans.data, growth.data]);
  const chosen =
    frames.find((frame) => frame.key === frameKey) ||
    frames.find((frame) => frame.key === `scan:${latestScan?.id}`) ||
    frames[0];
  const future = !!chosen?.prediction;
  const day = record ? growthDay(record.plant.sown_at) : null;
  const connected = !!record && !overview.error;
  const sensorAge = record?.latest_sensor
    ? record.latest_sensor.age_seconds +
      Math.max(0, (Date.now() - (overview.updated || Date.now())) / 1000)
    : null;
  const staleSensor = sensorAge != null && sensorAge > 300;
  const submitCommand = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!action || sending) return;
    setSending(true);
    setActionError(null);
    try {
      const result = await request<Command>("/api/v1/commands", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Command-Password": password,
        },
        body: JSON.stringify({
          request_id: action.requestId,
          plant_id: action.plant,
          kind: action.kind,
          payload: action.kind === "estop" ? { enabled: true } : null,
        }),
      });
      setCommand(result);
      setCommandNotice(true);
      setAction(null);
      setPassword("");
      setVersion((value) => value + 1);
    } catch (error) {
      setActionError(
        error instanceof ApiError
          ? error.message
          : "요청에 실패했어요. 다시 시도하면 같은 요청 ID로 확인해요.",
      );
    } finally {
      setSending(false);
    }
  };
  const saveSettings = (event: React.FormEvent) => {
    event.preventDefault();
    const value = Number(draftPlant);
    if (
      !/^\d+$/.test(draftPlant) ||
      !Number.isSafeInteger(value) ||
      value < 1
    ) {
      setSettingsError("등록된 화분의 양의 정수 ID를 입력해 주세요.");
      return;
    }
    try {
      localStorage.setItem("greentwin-plant", String(value));
    } catch {
      /* The current session can still select a plant. */
    }
    setPlantId(value);
    setSelectedScan(null);
    setFrameKey(null);
    setCommand(null);
    setCommandNotice(false);
    setSettingsOpen(false);
    setVersion((value) => value + 1);
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <a
          className="brand"
          href="#today"
          onClick={(event) => {
            event.preventDefault();
            setTab("today");
          }}
        >
          <span className="brand-mark">
            <Leaf size={21} strokeWidth={1.6} />
          </span>
          GreenTwin
          <span className="brand-divider" />
        </a>
        <nav aria-label="주 메뉴">
          {tabs.map((item) => (
            <button
              key={item.id}
              className={tab === item.id ? "active" : ""}
              aria-current={tab === item.id ? "page" : undefined}
              onClick={() => setTab(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="toolbar">
          <span className={`connection-status ${connected ? "connected" : ""}`}>
            <span />
            {overview.loading
              ? "연결 중"
              : connected
                ? "서버 연결됨"
                : "연결 확인"}
          </span>
          <button
            className="icon-button"
            aria-label="연결 설정"
            title="연결 설정"
            onClick={() => {
              setDraftPlant(String(plantId));
              setSettingsError(null);
              setSettingsOpen(true);
            }}
          >
            <Settings2 size={19} />
          </button>
        </div>
      </header>
      <main>
        {samplePreview && (
          <div className="info-note" role="status">
            샘플 데이터 미리보기 · 키·온습도·생장 예측은 화면 확인을 위한 예시 값이에요.
          </div>
        )}
        <div className="page-meta">
          <span className="plant-chip">
            <Sprout size={14} />
            <strong>{record?.plant.name || `화분 #${plantId}`}</strong>
            <span className="chip-divider" />
            {day == null ? "연결 대기" : `파종 ${day}일째`}
            {record?.plant.species && (
              <span className="plant-species">{record.plant.species}</span>
            )}
          </span>
          <div className="sync-meta">
            <span>
              {overview.updated
                ? `${clock(overview.updated)} 갱신 · 15초 간격`
                : "서버 데이터 확인 중"}
            </span>
            <button
              className="icon-button"
              aria-label="새로고침"
              title="새로고침"
              onClick={() => setVersion((value) => value + 1)}
            >
              <RefreshCw size={15} className={overview.loading ? "spin" : ""} />
            </button>
          </div>
        </div>
        <ErrorNote message={overview.error} />
        {overview.error && (
          <div className="connection-help">
            FastAPI 주소는 <code>dashboard/.env</code>의{" "}
            <code>API_PROXY_TARGET</code>에서 설정할 수 있어요.{" "}
            <button
              className="text-button"
              onClick={() => {
                setDraftPlant(String(plantId));
                setSettingsOpen(true);
              }}
            >
              화분 ID 확인
            </button>
          </div>
        )}
        {commandNotice && command && (
          <div
            className={`command-notice ${command.status === "failed" ? "failed" : ""}`}
            role="status"
          >
            <span>
              {["pending", "picked"].includes(command.status) ? (
                <LoaderCircle size={17} className="spin" />
              ) : (
                <Check size={17} />
              )}{" "}
              {command.kind === "scan_now" ? "스캔 요청" : "비상정지 요청"} #
              {command.id} · {statusLabel(command.status)}
              {typeof command.result?.message === "string"
                ? ` · ${command.result.message}`
                : ""}
            </span>
          </div>
        )}
        <ErrorNote message={commandNotice ? commandResult.error : null} />
        {tab === "today" && (
          <>
            <div className="section-title-row today-title">
              <div>
                <span className="eyebrow">A LITTLE GROWTH, EVERY DAY</span>
                <h1>식물의 오늘과 내일</h1>
                <p>
                  {record?.latest_sensor
                    ? `${dateTime(record.latest_sensor.ts, true)}에 전해진 재배실의 기록이에요.`
                    : "작은 생장의 변화와 재배 환경을 한눈에 확인해요."}
                </p>
              </div>
              <span className="date-stamp">
                {new Intl.DateTimeFormat("ko-KR", {
                  timeZone: "Asia/Seoul",
                  year: "numeric",
                  month: "long",
                  day: "numeric",
                  weekday: "short",
                }).format(new Date())}
              </span>
            </div>
            <div className="today-layout">
              <div className="visual-column">
                <div className="visual-wrap">
                  <div
                    className={`capture-chip ${future ? "prediction-chip" : ""}`}
                  >
                    <span />
                    {chosen
                      ? `D+${chosen.day} · ${dateTime(chosen.ts)} · ${future ? "예측" : "스캔"}`
                      : "생장 기록 대기"}
                  </div>
                  <Suspense
                    fallback={
                      <div className="plant-scene loading-panel">
                        3D 식물을 준비하고 있어요…
                      </div>
                    }
                  >
                    <PlantScene future={future} />
                  </Suspense>
                </div>
                <div className="timeline-card">
                  <span className="timeline-icon">
                    <Clock3 size={17} />
                  </span>
                  <div className="timeline-content">
                    <div className="timeline-top">
                      <span>생장 타임라인</span>
                      <button
                        className="text-button"
                        onClick={() => setFrameKey(null)}
                      >
                        최근 스캔으로
                      </button>
                    </div>
                    <input
                      type="range"
                      aria-label="생장 기록 시점 선택"
                      disabled={!frames.length}
                      min={0}
                      max={Math.max(0, frames.length - 1)}
                      value={Math.max(
                        0,
                        frames.findIndex((frame) => frame.key === chosen?.key),
                      )}
                      onChange={(event) =>
                        setFrameKey(frames[Number(event.target.value)].key)
                      }
                    />
                    <div className="timeline-labels">
                      <span>
                        {frames.length ? `D+${frames[0].day}` : "기록 대기"}
                      </span>
                      <span className={future ? "purple-text" : ""}>
                        {chosen
                          ? `D+${chosen.day} · ${future ? "예측" : "관측"}`
                          : "—"}
                      </span>
                      <span>
                        {frames.length
                          ? `D+${frames[frames.length - 1].day}`
                          : "—"}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
              <aside className="card today-summary">
                <div className="summary-heading">
                  <span>키</span>
                  <span
                    className={`status-pill ${future ? "purple-pill" : "success"}`}
                  >
                    {future ? "예측 평균" : "스캔 관측"}
                  </span>
                </div>
                <div className="hero-reading">
                  {number(chosen?.height)}
                  <small>cm</small>
                </div>
                <p className="reading-caption">
                  {future && chosen?.prediction?.p10 != null
                    ? `예측 80% 구간 ${number(chosen.prediction.p10)}–${number(chosen.prediction.p90)} cm`
                    : chosen
                      ? `${dateTime(chosen.ts, true)} 기준`
                      : "스캔 측정값이 도착하면 표시돼요"}
                </p>
                <div className="summary-metrics">
                  <Metric
                    label="수확 예상"
                    value={
                      growth.data?.harvest.status === "available"
                        ? growth.data.harvest.remaining_days === 0
                          ? "지금 가능"
                          : dateTime(growth.data.harvest.estimated_date)
                        : growth.data?.harvest.status === "not_reached"
                          ? "아직 미도달"
                          : "준비 중"
                    }
                    note="키 24cm 이상 기준"
                  />
                </div>
                <div className="summary-section-heading">
                  <h3>지금 환경</h3>
                  <button
                    className="text-button"
                    onClick={() => setTab("environment")}
                  >
                    자세히 <ChevronRight size={13} />
                  </button>
                </div>
                <div className="sensor-summary">
                  <div>
                    <Thermometer size={16} />
                    <span>기온</span>
                    <strong>
                      {number(record?.latest_sensor?.air_temp_c)}
                      <small>°C</small>
                    </strong>
                  </div>
                  <div>
                    <Droplets size={16} />
                    <span>습도</span>
                    <strong>
                      {number(record?.latest_sensor?.rh_pct)}
                      <small>%</small>
                    </strong>
                  </div>
                </div>
                <p className={`sensor-time ${staleSensor ? "stale" : ""}`}>
                  <Clock3 size={12} />
                  {staleSensor
                    ? "마지막 측정 후 5분 이상 경과"
                    : record?.latest_sensor
                      ? `${dateTime(record.latest_sensor.ts, true)} 측정`
                      : "측정값 수신 대기"}
                </p>
                <div className="summary-section-heading">
                  <h3>재배 상태</h3>
                </div>
                <div className="care-row">
                  <span className="care-icon">
                    <Droplets size={17} />
                  </span>
                  <div>
                    <strong>자동 급수</strong>
                    <span>
                      {wateringLabel(
                        record?.settings?.applied_settings.watering_mode,
                      )}
                    </span>
                  </div>
                </div>
                <div className="care-row">
                  <span className="care-icon lavender">
                    <Camera size={17} />
                  </span>
                  <div>
                    <strong>최근 스캔</strong>
                    <span>{dateTime(latestScan?.ts, true)}</span>
                  </div>
                </div>
                <button
                  className="summary-link"
                  onClick={() => setTab("growth")}
                >
                  생장 그래프 살펴보기 <ArrowUpRight size={16} />
                </button>
              </aside>
            </div>
            <div className="bottom-strip">
              <span>
                <Radio size={15} />
                {connected ? "서버 데이터 연결" : "서버 연결 대기"}
              </span>
              <span>
                <Camera size={15} />
                {latestScan
                  ? `최근 스캔 ${latestScan.image_count ?? 0}장`
                  : "스캔 기록 대기"}
              </span>
              <button onClick={() => setTab("devices")}>
                장비 {record?.devices.length ?? 0}대 확인{" "}
                <ChevronRight size={13} />
              </button>
            </div>
          </>
        )}
        {tab === "growth" && (
          <GrowthView
            scans={scans.data || []}
            growth={growth.data}
            error={growth.error || scans.error}
          />
        )}
        {tab === "environment" && (
          <Environment
            overview={record}
            sensors={sensors.data || []}
            actuations={actuations.data || []}
            days={days}
            setDays={setDays}
            error={sensors.error}
            historyError={actuations.error}
            loading={sensors.loading}
          />
        )}
        {tab === "scans" && (
          <>
            <div className="section-title-row">
              <div>
                <h1>사진 한 장씩, 생장의 기록</h1>
                <p>카메라별 사진과 스캔에서 얻은 측정값을 확인해요.</p>
              </div>
            </div>
            <ErrorNote message={scans.error} />
            <ScanPanel
              scans={scans.data || []}
              selected={scanId}
              detail={detail.data}
              loading={scans.loading || detail.loading}
              error={detail.error}
              onSelect={setSelectedScan}
            />
          </>
        )}
        {tab === "devices" && (
          <>
            <div className="section-title-row">
              <div>
                <h1>작은 재배실의 연결 상태</h1>
                <p>장비가 마지막으로 보고한 연결 상태를 확인해요.</p>
              </div>
            </div>
            <div className="device-grid">
              {record?.devices.length ? (
                record.devices.map((device) => (
                  <section className="card device-card" key={device.name}>
                    <div className="device-card-top">
                      <span className="device-symbol">
                        <Monitor size={25} />
                      </span>
                      <span
                        className={`status-pill ${device.online === true ? "success" : device.online === false ? "danger" : ""}`}
                      >
                        {device.online === true ? (
                          <Wifi size={12} />
                        ) : (
                          <WifiOff size={12} />
                        )}{" "}
                        {device.online === true
                          ? "온라인 보고"
                          : device.online === false
                            ? "오프라인 보고"
                            : "상태 미확인"}
                      </span>
                    </div>
                    <h2>{deviceLabel(device.name)}</h2>
                    <p>{device.role}</p>
                    <dl>
                      <dt>마지막 보고</dt>
                      <dd>{dateTime(device.ts, true)}</dd>
                      <dt>보고 후 경과</dt>
                      <dd>
                        {device.age_seconds == null
                          ? "—"
                          : `${number(Math.max(0, device.age_seconds) / 60, 0)}분`}
                      </dd>
                    </dl>
                  </section>
                ))
              ) : (
                <section className="card full-width">
                  <Empty
                    title="등록된 장비 보고가 없어요"
                    detail="장비 상태 보고 API로 도착한 장비가 이곳에 표시돼요."
                  />
                </section>
              )}
            </div>
          </>
        )}
      </main>
      <footer>
        <span>
          <Leaf size={13} /> GreenTwin
        </span>
        <span>오늘의 기록이 내일의 생장이 되도록.</span>
      </footer>
      {settingsOpen && (
        <Modal title="연결 설정" onClose={() => setSettingsOpen(false)}>
          <form onSubmit={saveSettings}>
            <p className="modal-description">
              허브 DB에 등록된 화분 ID를 선택해요.
            </p>
            <label htmlFor="plant-id">화분 ID</label>
            <input
              id="plant-id"
              inputMode="numeric"
              value={draftPlant}
              onChange={(event) => setDraftPlant(event.target.value)}
              autoFocus
              required
            />
            <ErrorNote message={settingsError} />
            <div className="config-note">
              <strong>서버 연결</strong>
              <code>/api/v1 → API_PROXY_TARGET</code>
              <p>
                기본값은 http://127.0.0.1:8000이에요. 다른 허브에 연결하려면
                dashboard/.env에서 주소를 변경하고 개발 서버를 재시작해 주세요.
              </p>
            </div>
            <button className="button primary full-width" type="submit">
              적용하기
            </button>
          </form>
        </Modal>
      )}
      {action && (
        <Modal
          title={
            action.kind === "scan_now"
              ? "새 스캔을 요청할까요?"
              : "비상정지를 요청할까요?"
          }
          onClose={() => {
            if (!sending) {
              setAction(null);
              setPassword("");
            }
          }}
        >
          <form onSubmit={submitCommand}>
            <p className="modal-description">
              {action.kind === "scan_now"
                ? "허브에 촬영 명령을 등록해요. 연결된 라즈베리파이가 명령을 가져가 처리해요."
                : "허브에 비상정지 명령을 등록해요. 장비가 명령을 받아 적용한 결과를 반드시 확인해 주세요."}
            </p>
            <label htmlFor="command-password">명령 비밀번호</label>
            <input
              id="command-password"
              type="password"
              autoComplete="off"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              autoFocus
              disabled={sending}
            />
            <p className="footnote">
              서버의 COMMAND_PASSWORD 값을 입력해 주세요.
            </p>
            <ErrorNote message={actionError} />
            <button
              className={`button full-width ${action.kind === "estop" ? "danger-solid" : "primary"}`}
              disabled={sending || !password}
            >
              {sending ? (
                <LoaderCircle className="spin" size={16} />
              ) : action.kind === "scan_now" ? (
                <ScanLine size={16} />
              ) : (
                <CircleStop size={16} />
              )}{" "}
              {sending
                ? "요청 중…"
                : action.kind === "scan_now"
                  ? "스캔 요청 보내기"
                  : "비상정지 요청 보내기"}
            </button>
          </form>
        </Modal>
      )}
    </div>
  );
}
