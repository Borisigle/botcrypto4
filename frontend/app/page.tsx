import DashboardClient from './dashboard-client';
import type {
  ContextResponse,
  HealthResult,
  MetricsResponse,
  PricePayload,
  WsHealthExtended,
} from './types';
import {
  fetchContext,
  fetchHealthStatus,
  fetchMetrics,
  fetchPrice,
  fetchWsHealth,
} from './api-client';

export const dynamic = 'force-dynamic';

function sanitizeBaseUrl(url: string): string {
  return url.endsWith('/') ? url.slice(0, -1) : url;
}

export default async function Home(): Promise<JSX.Element> {
  const baseUrl = sanitizeBaseUrl(process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000');

  const [initialHealth, initialContext, initialWsHealth, initialPrice, initialMetrics] =
    await Promise.all([
      fetchHealthStatus(baseUrl),
      fetchContext(baseUrl),
      fetchWsHealth(baseUrl),
      fetchPrice(baseUrl),
      fetchMetrics(baseUrl),
    ]);

  return (
    <DashboardClient
      baseUrl={baseUrl}
      initialContext={initialContext}
      initialHealth={initialHealth}
      initialWsHealth={initialWsHealth}
      initialPrice={initialPrice}
      initialMetrics={initialMetrics}
    />
  );
}
