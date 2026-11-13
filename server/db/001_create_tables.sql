BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '5s';

-- 화분 정보
CREATE TABLE plant (
    id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
    name VARCHAR(100) NOT NULL,
    species VARCHAR(100) NOT NULL,
    sown_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT plant_pkey PRIMARY KEY (id)
);

-- 촬영·분석 기록
CREATE TABLE scan (
    id UUID NOT NULL,
    plant_id BIGINT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    day INTEGER NOT NULL,
    height_cm DOUBLE PRECISION,
    canopy_cm DOUBLE PRECISION,
    volume_cm3 DOUBLE PRECISION,
    leaf_pairs_est DOUBLE PRECISION,
    leaf_area_est DOUBLE PRECISION,
    leaf_pairs_manual INTEGER,
    status VARCHAR(20) DEFAULT 'pending' NOT NULL,
    cloud_file TEXT,
    CONSTRAINT scan_pkey PRIMARY KEY (id),
    CONSTRAINT scan_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES plant (id) ON DELETE RESTRICT,
    CONSTRAINT scan_day_nonnegative_check CHECK (day >= 0),
    CONSTRAINT scan_height_cm_nonnegative_check CHECK (height_cm >= 0),
    CONSTRAINT scan_canopy_cm_nonnegative_check CHECK (canopy_cm >= 0),
    CONSTRAINT scan_volume_cm3_nonnegative_check CHECK (volume_cm3 >= 0),
    CONSTRAINT scan_leaf_pairs_est_nonnegative_check CHECK (leaf_pairs_est >= 0),
    CONSTRAINT scan_leaf_area_est_nonnegative_check CHECK (leaf_area_est >= 0),
    CONSTRAINT scan_leaf_pairs_manual_nonnegative_check CHECK (leaf_pairs_manual >= 0),
    CONSTRAINT scan_status_check CHECK (status IN ('pending', 'processing', 'completed', 'failed'))
);

-- 사진 정보
CREATE TABLE image (
    id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
    scan_id UUID NOT NULL,
    cam VARCHAR(50) NOT NULL,
    angle_idx INTEGER NOT NULL,
    file TEXT NOT NULL,
    mask TEXT,
    sha256 VARCHAR(64),
    w INTEGER NOT NULL,
    h INTEGER NOT NULL,
    CONSTRAINT image_pkey PRIMARY KEY (id),
    CONSTRAINT image_scan_id_fkey FOREIGN KEY (scan_id) REFERENCES scan (id) ON DELETE RESTRICT,
    CONSTRAINT image_scan_id_cam_angle_idx_key UNIQUE (scan_id, cam, angle_idx),
    CONSTRAINT image_angle_nonnegative_check CHECK (angle_idx >= 0),
    CONSTRAINT image_width_positive_check CHECK (w > 0),
    CONSTRAINT image_height_positive_check CHECK (h > 0)
);

-- 모델 버전 정보
CREATE TABLE model_version (
    id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
    kind VARCHAR(20) NOT NULL,
    status VARCHAR(20) DEFAULT 'candidate' NOT NULL,
    params JSONB,
    metrics JSONB,
    parent BIGINT,
    trained_at TIMESTAMPTZ,
    file_path TEXT,
    CONSTRAINT model_version_pkey PRIMARY KEY (id),
    CONSTRAINT model_version_parent_fkey FOREIGN KEY (parent) REFERENCES model_version (id) ON DELETE RESTRICT,
    CONSTRAINT model_version_kind_check CHECK (kind IN ('growth', 'perceive')),
    CONSTRAINT model_version_status_check CHECK (status IN ('candidate', 'active', 'archived'))
);

-- 생장 예측 결과
CREATE TABLE prediction (
    id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
    model_id BIGINT NOT NULL,
    origin_scan_id UUID NOT NULL,
    origin_day INTEGER NOT NULL,
    target_day INTEGER NOT NULL,
    height_mu DOUBLE PRECISION NOT NULL,
    p10 DOUBLE PRECISION,
    p90 DOUBLE PRECISION,
    pairs_mu DOUBLE PRECISION,
    CONSTRAINT prediction_pkey PRIMARY KEY (id),
    CONSTRAINT prediction_model_id_fkey FOREIGN KEY (model_id) REFERENCES model_version (id) ON DELETE RESTRICT,
    CONSTRAINT prediction_origin_scan_id_fkey FOREIGN KEY (origin_scan_id) REFERENCES scan (id) ON DELETE RESTRICT,
    CONSTRAINT prediction_origin_scan_id_model_id_target_day_key UNIQUE (origin_scan_id, model_id, target_day),
    CONSTRAINT prediction_origin_day_nonnegative_check CHECK (origin_day >= 0),
    CONSTRAINT prediction_target_day_check CHECK (target_day > origin_day),
    CONSTRAINT prediction_height_mu_nonnegative_check CHECK (height_mu >= 0),
    CONSTRAINT prediction_p10_nonnegative_check CHECK (p10 >= 0),
    CONSTRAINT prediction_p90_nonnegative_check CHECK (p90 >= 0),
    CONSTRAINT prediction_pairs_mu_nonnegative_check CHECK (pairs_mu >= 0),
    CONSTRAINT prediction_interval_check CHECK ((p10 IS NULL AND p90 IS NULL) OR (p10 IS NOT NULL AND p90 IS NOT NULL AND p10 <= p90))
);

-- 예측 결과 채점
CREATE TABLE backtest (
    id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
    prediction_id BIGINT NOT NULL,
    variant VARCHAR(50),
    origin_day INTEGER NOT NULL,
    target_day INTEGER NOT NULL,
    horizon INTEGER NOT NULL,
    predicted DOUBLE PRECISION NOT NULL,
    observed DOUBLE PRECISION NOT NULL,
    abs_err DOUBLE PRECISION NOT NULL,
    in_band BOOLEAN,
    CONSTRAINT backtest_pkey PRIMARY KEY (id),
    CONSTRAINT backtest_prediction_id_fkey FOREIGN KEY (prediction_id) REFERENCES prediction (id) ON DELETE RESTRICT,
    CONSTRAINT backtest_prediction_id_key UNIQUE (prediction_id),
    CONSTRAINT backtest_origin_day_nonnegative_check CHECK (origin_day >= 0),
    CONSTRAINT backtest_target_day_check CHECK (target_day > origin_day),
    CONSTRAINT backtest_horizon_check CHECK (horizon > 0 AND horizon = target_day - origin_day),
    CONSTRAINT backtest_predicted_nonnegative_check CHECK (predicted >= 0),
    CONSTRAINT backtest_observed_nonnegative_check CHECK (observed >= 0),
    CONSTRAINT backtest_abs_err_nonnegative_check CHECK (abs_err >= 0)
);

-- 화분별 최신 생장 상태
CREATE TABLE growth_state (
    plant_id BIGINT NOT NULL,
    model_id BIGINT NOT NULL,
    posterior JSONB NOT NULL,
    regime_start TIMESTAMPTZ,
    anomaly_streak INTEGER DEFAULT 0 NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT growth_state_pkey PRIMARY KEY (plant_id),
    CONSTRAINT growth_state_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES plant (id) ON DELETE RESTRICT,
    CONSTRAINT growth_state_model_id_fkey FOREIGN KEY (model_id) REFERENCES model_version (id) ON DELETE RESTRICT,
    CONSTRAINT growth_state_anomaly_streak_nonnegative_check CHECK (anomaly_streak >= 0)
);

-- 센서 측정 기록
CREATE TABLE sensor_reading (
    id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
    plant_id BIGINT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    air_temp_c DOUBLE PRECISION,
    rh_pct DOUBLE PRECISION,
    soil_moisture_pct DOUBLE PRECISION,
    lux DOUBLE PRECISION,
    ppfd DOUBLE PRECISION,
    vpd_kpa DOUBLE PRECISION,
    light_level SMALLINT,
    CONSTRAINT sensor_reading_pkey PRIMARY KEY (id),
    CONSTRAINT sensor_reading_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES plant (id) ON DELETE RESTRICT,
    CONSTRAINT sensor_reading_plant_id_ts_key UNIQUE (plant_id, ts),
    CONSTRAINT sensor_reading_rh_range_check CHECK (rh_pct BETWEEN 0 AND 100),
    CONSTRAINT sensor_reading_soil_range_check CHECK (soil_moisture_pct BETWEEN 0 AND 100),
    CONSTRAINT sensor_reading_lux_nonnegative_check CHECK (lux >= 0),
    CONSTRAINT sensor_reading_ppfd_nonnegative_check CHECK (ppfd >= 0),
    CONSTRAINT sensor_reading_vpd_kpa_nonnegative_check CHECK (vpd_kpa >= 0)
);

-- 제어 결과
CREATE TABLE actuation (
    id UUID NOT NULL,
    plant_id BIGINT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    kind VARCHAR(30) NOT NULL,
    amount DOUBLE PRECISION,
    unit VARCHAR(20),
    ok BOOLEAN NOT NULL,
    reason TEXT,
    source VARCHAR(20) NOT NULL,
    context JSONB,
    CONSTRAINT actuation_pkey PRIMARY KEY (id),
    CONSTRAINT actuation_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES plant (id) ON DELETE RESTRICT,
    CONSTRAINT actuation_kind_check CHECK (kind IN ('water', 'light')),
    CONSTRAINT actuation_amount_nonnegative_check CHECK (amount >= 0),
    CONSTRAINT actuation_amount_unit_check CHECK (amount IS NULL OR unit IS NOT NULL),
    CONSTRAINT actuation_source_check CHECK (source IN ('auto', 'manual', 'guard'))
);

-- 경고·알림
CREATE TABLE event (
    id UUID NOT NULL,
    plant_id BIGINT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    kind VARCHAR(50) NOT NULL,
    level VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    data JSONB,
    CONSTRAINT event_pkey PRIMARY KEY (id),
    CONSTRAINT event_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES plant (id) ON DELETE RESTRICT,
    CONSTRAINT event_level_check CHECK (level IN ('info', 'warning', 'error'))
);

-- 명령 대기열
CREATE TABLE command (
    id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
    request_id UUID NOT NULL,
    plant_id BIGINT NOT NULL,
    kind VARCHAR(50) NOT NULL,
    payload JSONB,
    result JSONB,
    status VARCHAR(20) DEFAULT 'pending' NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    picked_at TIMESTAMPTZ,
    done_at TIMESTAMPTZ,
    CONSTRAINT command_pkey PRIMARY KEY (id),
    CONSTRAINT command_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES plant (id) ON DELETE RESTRICT,
    CONSTRAINT command_request_id_key UNIQUE (request_id),
    CONSTRAINT command_status_check CHECK (status IN ('pending', 'picked', 'done', 'failed'))
);

-- 장비 최신 상태
CREATE TABLE device (
    name VARCHAR(50) NOT NULL,
    role VARCHAR(30) NOT NULL,
    online BOOLEAN,
    ts TIMESTAMPTZ,
    info JSONB,
    CONSTRAINT device_pkey PRIMARY KEY (name)
);

COMMIT;
