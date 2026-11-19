import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Growth, Scan, SensorReading } from "../types";
import { dateTime, number } from "../format";
import { Empty } from "./UI";

export function SensorChart({
  data,
  field,
  color,
  unit,
}: {
  data: SensorReading[];
  field: "air_temp_c" | "rh_pct";
  color: string;
  unit: string;
}) {
  if (!data.some((item) => item[field] != null))
    return (
      <Empty
        title="아직 측정 기록이 없어요"
        detail="라즈베리파이가 전송한 측정값이 이곳에 쌓여요."
      />
    );
  const points = data.map((item) => ({
    ts: Date.parse(item.ts),
    value: item[field],
  }));
  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={points}
          margin={{ top: 18, right: 10, bottom: 4, left: -24 }}
        >
          <defs>
            <linearGradient id={`fill-${field}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.17} />
              <stop offset="100%" stopColor={color} stopOpacity={0.01} />
            </linearGradient>
          </defs>
          <CartesianGrid
            vertical={false}
            stroke="#e8ede6"
            strokeDasharray="3 4"
          />
          <XAxis
            dataKey="ts"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(value) =>
              new Intl.DateTimeFormat("ko-KR", {
                timeZone: "Asia/Seoul",
                month: "numeric",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                hourCycle: "h23",
              }).format(new Date(value))
            }
            minTickGap={45}
            tickLine={false}
            axisLine={false}
            tick={{ fill: "#7d897f", fontSize: 12 }}
          />
          <YAxis
            unit={unit}
            tickLine={false}
            axisLine={false}
            tick={{ fill: "#7d897f", fontSize: 12 }}
            domain={field === "rh_pct" ? [0, 100] : ["auto", "auto"]}
          />
          <Tooltip
            labelFormatter={(label) =>
              dateTime(new Date(Number(label)).toISOString(), true)
            }
            formatter={(value) => [
              `${number(Number(value))} ${unit}`,
              "10분 평균",
            ]}
            contentStyle={{
              borderRadius: 12,
              borderColor: "#e0e7de",
              fontSize: 13,
            }}
          />
          <Area
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={2}
            fill={`url(#fill-${field})`}
            connectNulls={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export function GrowthChart({
  scans,
  growth,
  pairs = false,
}: {
  scans: Scan[];
  growth?: Growth;
  pairs?: boolean;
}) {
  const records = new Map<
    number,
    {
      day: number;
      observed?: number;
      predicted?: number;
      band?: [number, number];
    }
  >();
  for (const scan of [...scans].sort((a, b) => a.ts.localeCompare(b.ts))) {
    const value = pairs
      ? (scan.leaf_pairs_manual ?? scan.leaf_pairs_est)
      : scan.height_cm;
    if (value != null)
      records.set(scan.day, { day: scan.day, observed: value });
  }
  for (const prediction of growth?.predictions || []) {
    const value = pairs ? prediction.pairs_mu : prediction.height_mu;
    if (value == null) continue;
    records.set(prediction.target_day, {
      ...records.get(prediction.target_day),
      day: prediction.target_day,
      predicted: value,
      ...(!pairs && prediction.p10 != null && prediction.p90 != null
        ? { band: [prediction.p10, prediction.p90] as [number, number] }
        : {}),
    });
  }
  const data = Array.from(records.values()).sort((a, b) => a.day - b.day);
  if (!data.length)
    return (
      <Empty
        title="생장 기록을 기다리고 있어요"
        detail="스캔 측정값과 저장된 예측 결과를 그래프로 보여줘요."
      />
    );
  return (
    <div className="chart growth-chart">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={data}
          margin={{ top: 20, right: 14, bottom: 5, left: -25 }}
        >
          <CartesianGrid vertical={false} stroke="#e8ede6" />
          <XAxis
            dataKey="day"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(value) => `D+${value}`}
            tickLine={false}
            axisLine={false}
            tick={{ fill: "#7d897f", fontSize: 12 }}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            tick={{ fill: "#7d897f", fontSize: 12 }}
          />
          <Tooltip
            labelFormatter={(label) => `파종 후 ${label}일`}
            formatter={(value, name) => [
              Array.isArray(value)
                ? value.map((item) => number(Number(item))).join("–")
                : number(Number(value)),
              name === "observed"
                ? "스캔 관측"
                : name === "band"
                  ? "예측 80% 구간"
                  : "예측 평균",
            ]}
            contentStyle={{
              borderRadius: 12,
              borderColor: "#e0e7de",
              fontSize: 13,
            }}
          />
          <Area
            type="monotone"
            dataKey="band"
            fill="#e7e1fa"
            stroke="none"
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="predicted"
            stroke="#8371bf"
            dot={false}
            strokeWidth={2.5}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="observed"
            stroke="#347b50"
            dot={{ r: 3, fill: "#347b50", strokeWidth: 0 }}
            strokeWidth={1.5}
            connectNulls={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
