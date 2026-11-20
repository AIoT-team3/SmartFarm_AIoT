export interface SensorValues {
  air_temp_c: number | null;
  rh_pct: number | null;
  soil_moisture_pct: number | null;
  lux: number | null;
  ppfd: number | null;
  vpd_kpa: number | null;
  light_level: number | null;
}
export interface SensorReading extends SensorValues {
  ts: string;
  sample_count?: number;
}
export interface Device {
  name: string;
  role: string;
  online: boolean | null;
  ts: string | null;
  age_seconds: number | null;
}
export interface Overview {
  as_of: string;
  plant: { id: number; name: string; species: string; sown_at: string };
  latest_sensor: (SensorReading & { id: number; age_seconds: number }) | null;
  devices: Device[];
  settings: {
    device_name: string;
    applied_settings: Record<string, unknown>;
    settings_revision: number;
    settings_applied_at: string;
  } | null;
}
export interface Scan {
  id: string;
  ts: string;
  day: number;
  status: "pending" | "processing" | "completed" | "failed";
  height_cm: number | null;
  canopy_cm: number | null;
  volume_cm3: number | null;
  leaf_pairs_est: number | null;
  leaf_area_est: number | null;
  leaf_pairs_manual: number | null;
  image_count?: number;
}
export interface ScanImage {
  id: number;
  cam: string;
  angle_idx: number;
  url: string;
  mask_url: string | null;
  w: number;
  h: number;
}
export interface ScanDetail extends Scan {
  plant_id: number;
  cloud_url: string | null;
  images: ScanImage[];
}
export interface Prediction {
  id: number;
  origin_day: number;
  target_day: number;
  target_date: string;
  height_mu: number;
  p10: number | null;
  p90: number | null;
  pairs_mu: number | null;
  backtest: {
    prediction_id: number;
    horizon: number;
    predicted: number;
    observed: number;
    abs_err: number;
    in_band: boolean | null;
  } | null;
}
export interface Growth {
  plant_id: number;
  status: "available" | "no_model" | "no_prediction";
  model: { id: number; kind: string; status: string } | null;
  origin_scan: { id: string; ts: string; day: number } | null;
  predictions: Prediction[];
  backtest_summary: {
    evaluated_count: number;
    mae_cm: number | null;
    band_evaluated_count: number;
    band_hit_rate: number | null;
  };
  growth_state: {
    model_id: number;
    regime_start: string | null;
    anomaly_streak: number;
    updated_at: string;
  } | null;
  harvest: {
    status: string;
    estimated_date: string | null;
    remaining_days: number | null;
  };
}
export interface Actuation {
  id: string;
  ts: string;
  kind: string;
  amount: number | null;
  unit: string | null;
  ok: boolean;
  reason: string | null;
  source: string;
  context: Record<string, unknown> | null;
}
export interface Page<T> {
  items: T[];
  next_offset: number | null;
}
export interface Command {
  id: number;
  plant_id: number;
  kind: string;
  status: "pending" | "picked" | "done" | "failed";
  request_id: string;
  created_at: string;
  done_at: string | null;
  result: Record<string, unknown> | null;
}
export type Tab = "today" | "growth" | "environment" | "scans" | "devices";
