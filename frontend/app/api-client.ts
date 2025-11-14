import type {
  ContextResponse,
  HealthResult,
  MetricsResponse,
  PricePayload,
  StrategyStatus,
  WsHealthExtended,
} from './types';

const DEFAULT_TIMEOUT = 5000;

async function fetchWithTimeout(
  url: string,
  timeout: number = DEFAULT_TIMEOUT,
): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  try {
    return await fetch(url, {
      cache: 'no-store',
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

export async function fetchHealthStatus(
  baseUrl: string,
): Promise<HealthResult | null> {
  // Use /ready endpoint which includes backfill status
  const url = `${baseUrl}/ready`;
  try {
    const response = await fetchWithTimeout(url);
    if (!response.ok) {
      return {
        status: `http_${response.status}`,
        message: `Unexpected response (${response.status})`,
      };
    }
    return (await response.json()) as HealthResult;
  } catch (error) {
    return {
      status: 'unreachable',
      message: error instanceof Error ? error.message : 'Unable to reach backend',
    };
  }
}

export async function fetchContext(baseUrl: string): Promise<ContextResponse | null> {
  const url = `${baseUrl}/context`;
  try {
    const response = await fetchWithTimeout(url);
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as ContextResponse;
  } catch (error) {
    console.warn('fetchContext error:', error);
    return null;
  }
}

export async function fetchWsHealth(baseUrl: string): Promise<WsHealthExtended | null> {
  const url = `${baseUrl}/ws/health`;
  try {
    const response = await fetchWithTimeout(url);
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as WsHealthExtended;
  } catch (error) {
    console.warn('fetchWsHealth error:', error);
    return null;
  }
}

export async function fetchPrice(baseUrl: string): Promise<PricePayload | null> {
  const url = `${baseUrl}/price`;
  try {
    const response = await fetchWithTimeout(url);
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as PricePayload;
  } catch (error) {
    console.warn('fetchPrice error:', error);
    return null;
  }
}

export async function fetchMetrics(baseUrl: string): Promise<MetricsResponse | null> {
  const url = `${baseUrl}/strategy/metrics`;
  try {
    const response = await fetchWithTimeout(url);
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as MetricsResponse;
  } catch (error) {
    console.warn('fetchMetrics error:', error);
    return null;
  }
}

export async function fetchStrategyStatus(baseUrl: string): Promise<StrategyStatus | null> {
  const url = `${baseUrl}/strategy/status`;
  try {
    const response = await fetchWithTimeout(url);
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as StrategyStatus;
  } catch (error) {
    console.warn('fetchStrategyStatus error:', error);
    return null;
  }
}

export async function fetchWsMetrics(
  baseUrl: string,
): Promise<{
  trade_queue_size: number;
  depth_queue_size: number;
  events_received: number;
  last_trade_ts: string | null;
} | null> {
  const url = `${baseUrl}/metrics`;
  try {
    const response = await fetchWithTimeout(url);
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as {
      trade_queue_size: number;
      depth_queue_size: number;
      events_received: number;
      last_trade_ts: string | null;
    };
  } catch (error) {
    console.warn('fetchWsMetrics error:', error);
    return null;
  }
}
