import DashboardClient from './dashboard-client';
import type { ContextResponse, HealthResult, PricePayload, WsHealth } from './types';

export const dynamic = 'force-dynamic';

function sanitizeBaseUrl(url: string): string {
  return url.endsWith('/') ? url.slice(0, -1) : url;
}

async function fetchHealthStatus(baseUrl: string): Promise<HealthResult> {
  const url = `${baseUrl}/health`;
  try {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) {
      return {
        status: `http_${response.status}`,
        message: `Unexpected response (${response.status})`,
      };
    }
    const payload = (await response.json()) as HealthResult;
    return payload;
  } catch (error) {
    return {
      status: 'unreachable',
      message: error instanceof Error ? error.message : 'Unable to reach backend',
    };
  }
}

async function fetchContext(baseUrl: string): Promise<ContextResponse | null> {
  const url = `${baseUrl}/context`;
  try {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) {
      return null;
    }
    const payload = (await response.json()) as ContextResponse;
    return payload;
  } catch (_error) {
    return null;
  }
}

async function fetchWsHealth(baseUrl: string): Promise<WsHealth | null> {
  const url = `${baseUrl}/ws/health`;
  try {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as WsHealth;
  } catch (_error) {
    return null;
  }
}

async function fetchPrice(baseUrl: string): Promise<PricePayload | null> {
  const url = `${baseUrl}/price`;
  try {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as PricePayload;
  } catch (_error) {
    return null;
  }
}

export default async function Home(): Promise<JSX.Element> {
  const baseUrl = sanitizeBaseUrl(process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000');

  const [initialHealth, initialContext, initialWsHealth, initialPrice] = await Promise.all([
    fetchHealthStatus(baseUrl),
    fetchContext(baseUrl),
    fetchWsHealth(baseUrl),
    fetchPrice(baseUrl),
  ]);

  return (
    <DashboardClient
      baseUrl={baseUrl}
      initialContext={initialContext}
      initialHealth={initialHealth}
      initialWsHealth={initialWsHealth}
      initialPrice={initialPrice}
    />
  );
}
