import { expect, test, type Page } from "@playwright/test";

const now = new Date().toISOString();
const scanId = "00000000-0000-4000-8000-000000000001";
const overview = {
  as_of: now,
  plant: {
    id: 1,
    name: "메인 바질",
    species: "바질",
    sown_at: new Date(Date.now() - 20 * 86400000).toISOString(),
  },
  latest_sensor: {
    id: 7,
    ts: now,
    age_seconds: 0,
    air_temp_c: 24.3,
    rh_pct: 61.2,
  },
  devices: [
    { name: "demo-edge", role: "control", online: true, ts: now, age_seconds: 10 },
  ],
  settings: {
    device_name: "demo-edge",
    settings_revision: 1,
    settings_applied_at: now,
    applied_settings: { watering_mode: "predictive" },
  },
};
const scan = {
  id: scanId,
  ts: now,
  day: 20,
  status: "completed",
  height_cm: 17.1,
  canopy_cm: 14.8,
  volume_cm3: 323,
  leaf_pairs_est: 7.1,
  leaf_pairs_manual: null,
  leaf_area_est: 251,
  image_count: 1,
};
const growth = {
  plant_id: 1,
  status: "available",
  model: { id: 1, kind: "growth", status: "active" },
  origin_scan: { id: scanId, ts: now, day: 20 },
  predictions: [
    {
      id: 1,
      origin_day: 20,
      target_day: 21,
      target_date: now.slice(0, 10),
      height_mu: 18.3,
      p10: 17.8,
      p90: 18.9,
      pairs_mu: 7.3,
      backtest: null,
    },
  ],
  backtest_summary: {
    evaluated_count: 0,
    mae_cm: null,
    band_evaluated_count: 0,
    band_hit_rate: null,
  },
  growth_state: null,
  harvest: {
    status: "not_available",
    estimated_date: null,
    remaining_days: null,
  },
};
async function api(page: Page, options: { empty?: boolean } = {}) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    let body: unknown;
    if (url.pathname.endsWith("/overview"))
      body = options.empty
        ? { ...overview, latest_sensor: null, devices: [], settings: null }
        : overview;
    else if (url.pathname.endsWith("/growth"))
      body = options.empty
        ? {
            ...growth,
            status: "no_model",
            model: null,
            origin_scan: null,
            predictions: [],
          }
        : growth;
    else if (url.pathname.endsWith("/sensor-readings"))
      body = {
        items: options.empty
          ? []
          : [{ ts: now, air_temp_c: 24.3, rh_pct: 61.2, sample_count: 2 }],
        next_offset: null,
      };
    else if (url.pathname.endsWith("/actuations"))
      body = {
        items: options.empty
          ? []
          : [
              {
                id: "water-1",
                ts: now,
                kind: "water",
                amount: 12,
                unit: "mL",
                source: "auto",
                ok: true,
                reason: "자동 급수",
                context: null,
              },
            ],
        next_offset: null,
      };
    else if (url.pathname.endsWith(`/scans/${scanId}`))
      body = {
        ...scan,
        plant_id: 1,
        images: [
          {
            id: 1,
            cam: "demo-cam0",
            angle_idx: 0,
            url: `/files/${scanId}/photo.png`,
            mask_url: null,
            w: 1,
            h: 1,
          },
        ],
        cloud_url: null,
      };
    else if (url.pathname.endsWith("/scans"))
      body = { items: options.empty ? [] : [scan], next_offset: null };
    else {
      await route.continue();
      return;
    }
    await route.fulfill({ json: body });
  });
  await page.route("**/files/**", (route) =>
    route.fulfill({
      contentType: "image/png",
      body: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4j8AAAAASUVORK5CYII=",
        "base64",
      ),
    }),
  );
}

test("조회 화면·사진·예측·환경 이력을 연결하고 모바일에서 넘치지 않는다", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await api(page);
  await page.goto("/");
  await expect(page.getByText("서버 연결됨")).toBeVisible();
  await expect(page.getByRole("banner").getByRole("button", { name: "지금 스캔", exact: true })).toHaveCount(0);
  await expect(page.getByRole("banner").getByRole("button", { name: "비상정지", exact: true })).toHaveCount(0);
  await expect(page.locator(".hero-reading")).toContainText("17.1");
  await expect(
    page.locator(".today-summary").getByText("잎 쌍", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.locator(".today-summary").getByText("캐노피 폭", { exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".summary-metrics .metric")).toHaveCount(1);

  await expect(page.getByText("임시 3D 시각화")).toHaveCount(0);
  await page.getByRole("slider").fill("1");
  await expect(page.locator(".hero-reading")).toContainText("18.3");
  await expect(page.getByText("예측 평균", { exact: true })).toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "환경", exact: true })
    .click();
  await expect(page.locator(".large-reading").first()).toContainText("24.3");
  await expect(page.getByRole("columnheader", { name: "사유", exact: true })).toHaveCount(0);
  await expect(page.getByText("온습도 기록을 표로 보기", { exact: true })).toHaveCount(0);
  await expect(page.locator(".history-layout thead th")).toHaveCount(5);
  await expect(
    page.getByRole("cell", { name: "급수", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "7일", exact: true }).click();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "스캔", exact: true })
    .click();
  await expect(page.locator(".scan-main .metric")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "지금 스캔", exact: true })).toHaveCount(0);
  await expect(page.locator(".photo-caption")).toHaveText("cam0");
  await expect(page.getByRole("option", { name: "cam0", exact: true })).toHaveAttribute("value", "demo-cam0");
  await page.getByRole("button", { name: "cam0 순번 0 사진 확대" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "닫기", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "마스크", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "생장", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "잎 쌍", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "키", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".growth-chart")).toHaveCount(1);
  await expect(
    page.getByText("아직 기다리고 있어요", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "장비", exact: true })
    .click();
  await expect(page.getByText("온라인 보고", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "edge", exact: true })).toBeVisible();
  await expect(page.getByText("장비 보고 기준", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "이번에 요청한 명령", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "장비에 적용된 설정", exact: true }),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  for (const name of ["지금", "생장", "환경", "스캔", "장비"]) {
    await page
      .getByRole("navigation")
      .getByRole("button", { name, exact: true })
      .click();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      name,
    ).toBe(true);
  }
  expect(errors).toEqual([]);
});

test("서버 오류와 빈 결과를 표시하고 화분 변경 시 이전 값을 제거한다", async ({
  page,
}) => {
  await api(page, { empty: true });
  await page.goto("/");
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "스캔", exact: true })
    .click();
  await expect(page.getByText("저장된 스캔이 없어요")).toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "환경", exact: true })
    .click();
  await expect(page.getByText("아직 측정 기록이 없어요").first()).toBeVisible();
  await page.route("**/api/v1/plants/2/**", (route) =>
    route.fulfill({
      status: 404,
      json: {
        error: {
          code: "PLANT_NOT_FOUND",
          message: "등록되지 않은 화분입니다.",
        },
      },
    }),
  );
  await page.getByRole("button", { name: "연결 설정", exact: true }).click();
  await page.getByLabel("화분 ID").fill("2");
  await page.getByRole("button", { name: "적용하기", exact: true }).click();
  await expect(
    page.getByText("등록되지 않은 화분입니다.").first(),
  ).toBeVisible();
  await expect(page.getByText("화분 #2", { exact: true })).toBeVisible();
  await expect(page.getByText("메인 바질", { exact: true })).toHaveCount(0);
});

test("24cm 수확 기준을 충족하면 현재 수확 가능 상태를 표시한다", async ({ page }) => {
  await api(page);
  await page.route("**/api/v1/plants/1/growth", (route) => route.fulfill({ json: {
    ...growth,
    harvest: { status: "available", estimated_date: "2026-11-24", remaining_days: 0 },
  } }));
  await page.goto("/");
  await expect(page.locator(".summary-metrics")).toContainText("지금 가능");
  await expect(page.locator(".summary-metrics")).toContainText("24cm");
  await page.getByRole("navigation").getByRole("button", { name: "생장", exact: true }).click();
  await expect(page.getByText("키 24cm 기준을 충족해 수확할 수 있어요.", { exact: true })).toBeVisible();
});
