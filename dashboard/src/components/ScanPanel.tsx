import { useState } from "react";
import { Camera, Download, Maximize2 } from "lucide-react";
import type { Scan, ScanDetail } from "../types";
import { dateTime, number, statusLabel } from "../format";
import { fileUrl } from "../api";
import { Empty, ErrorNote, Modal } from "./UI";

export function ScanPanel({
  scans,
  selected,
  detail,
  loading,
  error,
  onSelect,
}: {
  scans: Scan[];
  selected?: string;
  detail?: ScanDetail;
  loading: boolean;
  error: string | null;
  onSelect: (id: string) => void;
}) {
  const [camera, setCamera] = useState("all");
  const [opened, setOpened] = useState<string | null>(null);
  const cameras = Array.from(
    new Set(detail?.images.map((item) => item.cam) || []),
  );
  const activeCamera = cameras.includes(camera) ? camera : "all";
  const images = (detail?.images || [])
    .filter((item) => activeCamera === "all" || item.cam === activeCamera)
    .sort((a, b) => a.cam.localeCompare(b.cam) || a.angle_idx - b.angle_idx);
  const cloudUrl = fileUrl(detail?.cloud_url);
  return (
    <div className="scan-layout">
      <section className="card scan-main">
        <div className="card-heading">
          <div>
            <span className="eyebrow">CAPTURE RECORD</span>
            <h2>
              {detail
                ? `D+${detail.day} · ${dateTime(detail.ts, true)}`
                : "스캔 상세"}
            </h2>
            {detail && (
              <p>
                {statusLabel(detail.status)} · 사진 {detail.images.length}장
              </p>
            )}
          </div>
          <span className="section-icon">
            <Camera size={21} />
          </span>
        </div>
        <ErrorNote message={error} />
        {loading ? (
          <div className="loading-panel">스캔을 불러오고 있어요…</div>
        ) : !detail ? (
          <Empty
            title="저장된 스캔이 없어요"
            detail="스캔이 등록되면 사진과 측정 결과를 확인할 수 있어요."
          />
        ) : (
          <>
            <div className="scan-tools">
              <select
                aria-label="카메라 필터"
                value={activeCamera}
                onChange={(event) => setCamera(event.target.value)}
              >
                <option value="all">모든 카메라</option>
                {cameras.map((cam) => (
                  <option key={cam} value={cam}>
                    {cam}
                  </option>
                ))}
              </select>
            </div>
            {images.length ? (
              <div className="photo-grid">
                {images.map((image) => {
                  const url = fileUrl(image.url);
                  return (
                    <button
                      key={image.id}
                      className="photo"
                      disabled={!url}
                      onClick={() => setOpened(url)}
                      aria-label={`${image.cam} 순번 ${image.angle_idx} 사진 확대`}
                    >
                      {url ? (
                        <img
                          key={url}
                          src={url}
                          loading="lazy"
                          alt={`${image.cam}, 각도 순번 ${image.angle_idx}`}
                          onError={(event) => {
                            event.currentTarget.style.visibility = "hidden";
                            event.currentTarget.parentElement?.classList.add(
                              "image-error",
                            );
                          }}
                        />
                      ) : (
                        <span>사진 경로 없음</span>
                      )}
                      <span className="photo-caption">
                        {image.cam}
                        <span>#{image.angle_idx}</span>
                      </span>
                      <Maximize2 className="photo-expand" size={15} />
                    </button>
                  );
                })}
              </div>
            ) : (
              <Empty title="업로드된 사진이 없어요" />
            )}
            {cloudUrl && (
              <a className="button secondary" href={cloudUrl} download>
                <Download size={15} />
                점군 파일 다운로드
              </a>
            )}
          </>
        )}
      </section>
      <aside className="card scan-history">
        <div className="card-heading">
          <div>
            <h2>지난 스캔</h2>
            <p>최근 30일 · {scans.length}건</p>
          </div>
        </div>
        {!scans.length ? (
          <Empty title="스캔 기록 없음" />
        ) : (
          <div className="scan-list">
            {scans.map((scan) => (
              <button
                key={scan.id}
                className={`scan-list-item ${selected === scan.id ? "selected" : ""}`}
                onClick={() => {
                  setOpened(null);
                  onSelect(scan.id);
                }}
              >
                <span className="scan-date">{dateTime(scan.ts)}</span>
                <span>
                  <strong>
                    키 {number(scan.height_cm)} <small>cm</small>
                    <i>·</i>잎 {number(scan.leaf_pairs_est)} <small>쌍</small>
                  </strong>
                  <span>
                    D+{scan.day} · {statusLabel(scan.status)} ·{" "}
                    {scan.image_count ?? 0}장
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
      </aside>
      {opened && (
        <Modal title="스캔 사진" onClose={() => setOpened(null)}>
          <img
            className="lightbox-image"
            src={opened}
            alt="선택한 스캔 사진 확대"
          />
        </Modal>
      )}
    </div>
  );
}
