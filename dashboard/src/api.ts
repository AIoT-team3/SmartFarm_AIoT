import { useEffect, useState } from "react";
import type { Page } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
  ) {
    super(message);
  }
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const timeout = AbortSignal.timeout(12000);
  const signal = options.signal
    ? AbortSignal.any([timeout, options.signal])
    : timeout;
  let response: Response;
  try {
    response = await fetch(path, { ...options, signal });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new ApiError(
      "서버에 연결할 수 없어요. FastAPI 실행 상태와 연결 설정을 확인해 주세요.",
      0,
    );
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      body?.error?.message ||
      (response.status === 404
        ? "등록된 화분과 연결 주소를 확인해 주세요."
        : "서버에서 요청을 처리하지 못했어요.");
    throw new ApiError(message, response.status, body?.error?.code);
  }
  if (body === null)
    throw new ApiError("서버 응답 형식을 확인해 주세요.", response.status);
  return body as T;
}

export async function getAllPages<T>(
  path: string,
  signal: AbortSignal,
): Promise<T[]> {
  const result: T[] = [];
  let offset: number | null = 0;
  const visited = new Set<number>();
  while (offset !== null) {
    if (visited.has(offset))
      throw new Error(
        "페이지 조회가 반복되고 있어요. 서버의 next_offset을 확인해 주세요.",
      );
    visited.add(offset);
    const page: Page<T> = await request(`${path}&limit=1000&offset=${offset}`, {
      signal,
    });
    result.push(...page.items);
    offset = page.next_offset;
  }
  return result;
}

export function useResource<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  interval = 15000,
) {
  const [state, setState] = useState<{
    key: string | null;
    data?: T;
    loading: boolean;
    error: string | null;
    updated: number | null;
  }>({ key: null, loading: !!key, error: null, updated: null });
  useEffect(() => {
    if (!key) return;
    let active = true;
    let controller: AbortController;
    let timer: ReturnType<typeof setTimeout>;
    const run = async () => {
      controller = new AbortController();
      try {
        const data = await load(controller.signal);
        if (active)
          setState({
            key,
            data,
            loading: false,
            error: null,
            updated: Date.now(),
          });
      } catch (error) {
        if (active)
          setState((previous) => ({
            key,
            data: previous.key === key ? previous.data : undefined,
            loading: false,
            error:
              error instanceof Error
                ? error.message
                : "데이터 조회에 실패했어요.",
            updated: previous.key === key ? previous.updated : null,
          }));
      } finally {
        if (active && interval) timer = setTimeout(run, interval);
      }
    };
    void run();
    return () => {
      active = false;
      controller?.abort();
      clearTimeout(timer);
    };
    // key captures every request input so a changed plant or filter aborts the previous request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, interval]);
  if (state.key !== key)
    return { data: undefined, loading: !!key, error: null, updated: null };
  return state;
}

export function fileUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const path = new URL(url, window.location.origin).pathname;
    return path.startsWith("/files/") ? path : null;
  } catch {
    return null;
  }
}

export function historyQuery(days: number, anchor: number): string {
  return new URLSearchParams({
    from: new Date(anchor - days * 86400000).toISOString(),
    to: new Date(anchor).toISOString(),
  }).toString();
}
