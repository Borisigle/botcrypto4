'use client';

import { useEffect, useId, useMemo, useState } from 'react';
import type {
  ContextResponse,
  HealthResult,
  MetricsResponse,
  PricePayload,
  SessionState,
  WsHealthExtended,
} from './types';
import {
  fetchContext,
  fetchHealthStatus,
  fetchMetrics,
  fetchPrice,
  fetchWsHealth,
} from './api-client';

const CONTEXT_POLL_INTERVAL = 7000;
const HEALTH_POLL_INTERVAL = 5000;
const METRICS_POLL_INTERVAL = 2000;
const PRICE_POLL_INTERVAL = 1000;
const MAX_PRICE_POINTS = 240;

const SESSION_LABELS: Record<SessionState['state'], string> = {
  off: 'Off Session',
  london: 'London Session',
  overlap: 'Overlap Session',
};

const SESSION_BADGE_CLASS: Record<SessionState['state'], string> = {
  off: 'session-badge session-badge--off',
  london: 'session-badge session-badge--london',
  overlap: 'session-badge session-badge--overlap',
};

type HealthLevel = 'ok' | 'degraded' | 'down' | 'unknown';

type DashboardClientProps = {
  baseUrl: string;
  initialContext: ContextResponse | null;
  initialHealth: HealthResult | null;
  initialWsHealth: WsHealthExtended | null;
  initialPrice: PricePayload | null;
  initialMetrics: MetricsResponse | null;
};

type PricePoint = {
  price: number;
  ts: string;
};

type OverlayLine = {
  id: string;
  label: string;
  value: number;
  color: string;
  dashed?: boolean;
};

type HealthSummary = {
  level: HealthLevel;
  details: string[];
};

const DEFAULT_SESSION: SessionState = {
  state: 'off',
  nowUtc: new Date().toISOString(),
};

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function priceDigits(value: number): number {
  if (Math.abs(value) >= 100000) {
    return 0;
  }
  if (Math.abs(value) >= 1000) {
    return 1;
  }
  return 2;
}

function formatPrice(value: number | null | undefined): string {
  if (!isNumber(value)) {
    return 'N/A';
  }
  const numeric = value as number;
  const digits = priceDigits(numeric);
  return numeric.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatSigned(value: number | null | undefined): string {
  if (!isNumber(value)) {
    return 'N/A';
  }
  const numeric = value as number;
  const digits = Math.abs(numeric) >= 1000 ? 1 : 2;
  const magnitude = Math.abs(numeric).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  const prefix = numeric >= 0 ? '+' : '−';
  return `${prefix}${magnitude}`;
}

function formatVolume(value: number | null | undefined): string {
  if (!isNumber(value)) {
    return 'N/A';
  }
  const numeric = value as number;
  if (numeric >= 1000000) {
    return `${(numeric / 1000000).toFixed(2)}M`;
  }
  if (numeric >= 1000) {
    return `${(numeric / 1000).toFixed(2)}K`;
  }
  return numeric.toFixed(2);
}

function safeDate(source: string | null | undefined): Date | null {
  if (!source) {
    return null;
  }
  const parsed = new Date(source);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed;
}

function formatUtcClock(date: Date | null): string {
  if (!date || Number.isNaN(date.getTime())) {
    return '—';
  }
  return `${date.toISOString().slice(11, 19)} UTC`;
}

function formatIsoTime(iso: string | null | undefined): string {
  if (!iso) {
    return '—';
  }
  const parsed = safeDate(iso);
  if (!parsed) {
    return iso;
  }
  return `${parsed.toISOString().slice(11, 19)} UTC`;
}

function appendPricePoint(points: PricePoint[], point: PricePoint): PricePoint[] {
  if (!isNumber(point.price)) {
    return points;
  }
  const next = [...points];
  const last = next[next.length - 1];
  if (last) {
    if (last.ts === point.ts) {
      if (last.price === point.price) {
        return points;
      }
      next[next.length - 1] = point;
      return next;
    }
    if (new Date(point.ts).getTime() <= new Date(last.ts).getTime()) {
      return points;
    }
  }
  next.push(point);
  if (next.length > MAX_PRICE_POINTS) {
    return next.slice(next.length - MAX_PRICE_POINTS);
  }
  return next;
}

function payloadToPoint(payload: PricePayload | null | undefined): PricePoint | null {
  if (!payload || !isNumber(payload.price)) {
    return null;
  }
  const ts = payload.ts ?? new Date().toISOString();
  return { price: payload.price as number, ts };
}

function computeHealthSummary(
  health: HealthResult | null,
  ws: WsHealthExtended | null,
): HealthSummary {
  const backendStatus = health?.status?.toLowerCase() ?? 'unknown';
  const backendOk = backendStatus === 'ok';

  // Determine which health indicators to check
  const hasConnector = ws?.connector != null;
  const hasTrades = ws?.trades != null;
  const hasDepth = ws?.depth != null;

  let connectorConnected = false;
  let tradesConnected = false;
  let depthConnected = false;

  if (hasConnector) {
    connectorConnected = ws.connector?.connected ?? false;
  }
  if (hasTrades) {
    tradesConnected = ws.trades?.connected ?? false;
  }
  if (hasDepth) {
    depthConnected = ws.depth?.connected ?? false;
  }

  const details: string[] = [`API: ${backendOk ? 'OK' : backendStatus === 'unknown' ? 'Unknown' : health?.status ?? 'Unavailable'}`];

  if (hasConnector) {
    details.push(`Connector: ${connectorConnected ? 'Connected' : 'Down'}`);
  } else {
    details.push(`Trades WS: ${hasTrades ? (tradesConnected ? 'Connected' : 'Down') : 'Unknown'}`);
    details.push(`Depth WS: ${hasDepth ? (depthConnected ? 'Connected' : 'Down') : 'Unknown'}`);
  }

  let level: HealthLevel;

  const anyDown = connectorConnected === false || tradesConnected === false || depthConnected === false;

  if (backendOk && !anyDown) {
    level = hasConnector || hasTrades || hasDepth ? 'ok' : 'unknown';
  } else if (!backendOk && backendStatus !== 'unknown' && anyDown) {
    level = 'down';
  } else if (!backendOk || anyDown) {
    level = 'degraded';
  } else {
    level = 'unknown';
  }

  return { level, details };
}

const CHART_WIDTH = 1000;
const CHART_HEIGHT = 320;
const CHART_PADDING = 32;

function buildChart(points: PricePoint[], overlays: OverlayLine[]) {
  const numericPoints = points.filter((point) => isNumber(point.price));
  const overlayValues = overlays.filter((overlay) => isNumber(overlay.value));

  const values = [
    ...numericPoints.map((point) => point.price),
    ...overlayValues.map((overlay) => overlay.value),
  ];

  if (values.length === 0) {
    return {
      scaleReady: false,
      polylinePath: '',
      areaPath: '',
      overlays: [] as Array<OverlayLine & { y: number; labelText: string }>,
      lastPoint: null,
      showEmpty: true,
    };
  }

  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const valueRange = Math.max(maxValue - minValue, Math.max(Math.abs(maxValue), 1) * 0.01);
  const paddingValue = valueRange * 0.08;
  const lower = minValue - paddingValue;
  const upper = maxValue + paddingValue;
  const chartHeight = CHART_HEIGHT - 2 * CHART_PADDING;

  const valueToY = (value: number) => {
    const ratio = (value - lower) / (upper - lower || 1);
    return CHART_HEIGHT - CHART_PADDING - ratio * chartHeight;
  };

  const times = numericPoints.map((point) => new Date(point.ts).getTime());
  const minTime = times.length ? Math.min(...times) : Date.now();
  const maxTime = times.length ? Math.max(...times) : minTime + 1;
  const timeRange = Math.max(maxTime - minTime, 1);
  const chartWidth = CHART_WIDTH - 2 * CHART_PADDING;

  const coords = numericPoints.map((point) => {
    const timestamp = new Date(point.ts).getTime();
    const ratio = (timestamp - minTime) / timeRange;
    const x = CHART_PADDING + ratio * chartWidth;
    const y = valueToY(point.price);
    return { x, y, price: point.price, ts: point.ts };
  });

  let polylinePath = '';
  coords.forEach((coord, index) => {
    polylinePath += `${index === 0 ? 'M' : 'L'} ${coord.x} ${coord.y}`;
  });

  let areaPath = '';
  if (coords.length >= 2) {
    const baseLineY = CHART_HEIGHT - CHART_PADDING;
    areaPath = `${polylinePath} L ${coords[coords.length - 1].x} ${baseLineY} L ${coords[0].x} ${baseLineY} Z`;
  }

  const overlayPlacements = overlayValues.map((overlay) => ({
    ...overlay,
    y: valueToY(overlay.value),
    labelText: `${overlay.label} ${formatPrice(overlay.value)}`,
  }));

  return {
    scaleReady: true,
    polylinePath,
    areaPath,
    overlays: overlayPlacements,
    lastPoint: coords.length ? coords[coords.length - 1] : null,
    showEmpty: coords.length === 0,
  };
}

type PriceChartProps = {
  points: PricePoint[];
  overlays: OverlayLine[];
};

function PriceChart({ points, overlays }: PriceChartProps) {
  const gradientId = useId();
  const chart = useMemo(() => buildChart(points, overlays), [points, overlays]);

  return (
    <div className="chart-canvas">
      {chart.scaleReady && (
        <svg
          className="chart__svg"
          viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
          preserveAspectRatio="none"
          role="presentation"
        >
          <defs>
            <linearGradient id={`${gradientId}-stroke`} x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor="#facc15" />
              <stop offset="100%" stopColor="#fb923c" />
            </linearGradient>
            <linearGradient id={`${gradientId}-fill`} x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor="rgba(250, 204, 21, 0.35)" />
              <stop offset="100%" stopColor="rgba(59, 130, 246, 0.05)" />
            </linearGradient>
          </defs>

          {chart.areaPath && (
            <path d={chart.areaPath} fill={`url(#${gradientId}-fill)`} opacity={0.65} />
          )}
          {chart.polylinePath && (
            <path
              d={chart.polylinePath}
              fill="none"
              stroke={`url(#${gradientId}-stroke)`}
              strokeWidth={2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}

          {chart.overlays.map((overlay) => (
            <g key={overlay.id}>
              <line
                className="chart__overlay-line"
                x1={CHART_PADDING}
                x2={CHART_WIDTH - CHART_PADDING}
                y1={overlay.y}
                y2={overlay.y}
                stroke={overlay.color}
                strokeDasharray={overlay.dashed ? '6 6' : '0'}
              />
              <text
                className="chart__label"
                x={CHART_WIDTH - CHART_PADDING}
                y={overlay.y - 6}
                fill={overlay.color}
                textAnchor="end"
              >
                {overlay.labelText}
              </text>
            </g>
          ))}

          {chart.lastPoint && (
            <circle
              cx={chart.lastPoint.x}
              cy={chart.lastPoint.y}
              r={5}
              fill="#fde68a"
              stroke="#1e293b"
              strokeWidth={2}
            />
          )}
        </svg>
      )}
      {(!chart.scaleReady || chart.showEmpty) && (
        <div className="chart__empty">Waiting for price data…</div>
      )}
    </div>
  );
}

function MetricsPanel({ metrics }: { metrics: MetricsResponse | null }) {
  const metricsData = metrics?.metrics;
  const lastUpdate = metrics?.metadata?.last_update;

  return (
    <div className="panel metrics-card">
      <div className="panel__header">
        <h2>Live Metrics</h2>
        {lastUpdate && <span className="panel__meta">Updated: {formatIsoTime(lastUpdate)}</span>}
      </div>
      {metricsData ? (
        <div className="metrics-grid">
          <div className="metrics-item">
            <span className="metrics-label">VWAP</span>
            <span className="metrics-value metrics-value--vwap">{formatPrice(metricsData.vwap)}</span>
          </div>
          <div className="metrics-item">
            <span className="metrics-label">POC</span>
            <span className="metrics-value metrics-value--poc">{formatPrice(metricsData.poc)}</span>
          </div>
          <div className="metrics-item">
            <span className="metrics-label">Delta</span>
            <span
              className={`metrics-value ${
                isNumber(metricsData.delta)
                  ? metricsData.delta < 0
                    ? 'metrics-value--negative'
                    : metricsData.delta > 0
                    ? 'metrics-value--positive'
                    : ''
                  : ''
              }`}
            >
              {formatSigned(metricsData.delta)}
            </span>
          </div>
          <div className="metrics-item">
            <span className="metrics-label">Buy Vol</span>
            <span className="metrics-value metrics-value--buy">{formatVolume(metricsData.buy_volume)}</span>
          </div>
          <div className="metrics-item">
            <span className="metrics-label">Sell Vol</span>
            <span className="metrics-value metrics-value--sell">{formatVolume(metricsData.sell_volume)}</span>
          </div>
          <div className="metrics-item">
            <span className="metrics-label">Trades</span>
            <span className="metrics-value">{metricsData.trade_count}</span>
          </div>
        </div>
      ) : (
        <p className="panel__muted">No metrics data available yet.</p>
      )}
    </div>
  );
}

function SessionPanel({ context }: { context: ContextResponse | null }) {
  const session = context?.session ?? DEFAULT_SESSION;

  const getSessionColor = () => {
    switch (session.state) {
      case 'london':
        return 'session-panel--london';
      case 'overlap':
        return 'session-panel--overlap';
      default:
        return 'session-panel--off';
    }
  };

  return (
    <div className={`panel session-panel ${getSessionColor()}`}>
      <div className="panel__header">
        <h2>Trading Session</h2>
      </div>
      <div className="session-info">
        <div className="session-info__item">
          <span className="session-info__label">Current Session</span>
          <span className="session-info__value">{SESSION_LABELS[session.state]}</span>
        </div>
        <div className="session-info__item">
          <span className="session-info__label">Time (UTC)</span>
          <span className="session-info__value">{formatIsoTime(session.nowUtc)}</span>
        </div>
      </div>
    </div>
  );
}

function BackfillStatusPanel({ health, metrics }: { health: HealthResult | null; metrics: MetricsResponse | null }) {
  const backfillComplete = health?.backfill_complete ?? false;
  const backfillStatus = health?.backfill_status ?? 'idle';
  const backfillProgress = health?.backfill_progress;
  const tradingEnabled = health?.trading_enabled ?? false;
  const metricsPrecision = health?.metrics_precision ?? (backfillComplete ? 'PRECISE' : 'UNKNOWN');

  const tradingColor = tradingEnabled ? 'trading-badge--enabled' : 'trading-badge--disabled';

  const statusClass = useMemo(() => {
    switch (backfillStatus) {
      case 'complete':
        return 'backfill-status--complete';
      case 'in_progress':
        return 'backfill-status--progress';
      case 'error':
        return 'backfill-status--error';
      case 'cancelled':
        return 'backfill-status--cancelled';
      case 'skipped':
        return 'backfill-status--skipped';
      case 'disabled':
        return 'backfill-status--disabled';
      default:
        return 'backfill-status--idle';
    }
  }, [backfillStatus]);

  return (
    <div className="panel backfill-card">
      <div className="panel__header">
        <h2>Backfill & Trading Status</h2>
      </div>
      <div className="backfill-items">
        <div className="backfill-item">
          <span className="backfill-label">Trading</span>
          <span className={`trading-badge ${tradingColor}`}>
            {tradingEnabled ? '✅ ENABLED' : '❌ DISABLED'}
          </span>
        </div>
        <div className="backfill-item">
          <span className="backfill-label">Backfill Status</span>
          <span className={`backfill-status ${statusClass}`}>
            {backfillStatus === 'in_progress' ? '⏳ In Progress' : 
             backfillStatus === 'complete' ? '✅ Complete' :
             backfillStatus === 'skipped' ? '⏭️ Skipped' :
             backfillStatus === 'disabled' ? '🚫 Disabled' :
             backfillStatus === 'error' ? '❌ Error' :
             backfillStatus === 'cancelled' ? '🛑 Cancelled' :
             '⌛ Idle'}
          </span>
        </div>
        {backfillProgress && backfillProgress.total > 0 && (
          <>
            <div className="backfill-item">
              <span className="backfill-label">Progress</span>
              <span className="backfill-value">
                {backfillProgress.current}/{backfillProgress.total} chunks ({backfillProgress.percentage.toFixed(1)}%)
              </span>
            </div>
            {backfillProgress.estimated_seconds_remaining != null && backfillProgress.estimated_seconds_remaining > 0 && (
              <div className="backfill-item">
                <span className="backfill-label">Est. Remaining</span>
                <span className="backfill-value">{backfillProgress.estimated_seconds_remaining}s</span>
              </div>
            )}
            <div className="backfill-progress-bar">
              <div 
                className="backfill-progress-fill" 
                style={{ width: `${Math.min(100, backfillProgress.percentage)}%` }}
              />
            </div>
          </>
        )}
        <div className="backfill-item">
          <span className="backfill-label">Metrics Precision</span>
          <span className={`metrics-precision ${backfillComplete ? 'metrics-precision--precise' : 'metrics-precision--imprecise'}`}>
            {metricsPrecision}
          </span>
        </div>
        {metrics?.metrics?.warning && (
          <div className="backfill-warning">
            ⚠️ {metrics.metrics.warning}
          </div>
        )}
      </div>
    </div>
  );
}

function ConnectorHealthPanel({ wsHealth }: { wsHealth: WsHealthExtended | null }) {
  const hasConnector = wsHealth?.connector != null;
  const hasTrades = wsHealth?.trades != null;
  const hasDepth = wsHealth?.depth != null;

  return (
    <div className="panel health-card">
      <div className="panel__header">
        <h2>Connector Health</h2>
      </div>
      {hasConnector ? (
        <div className="health-items">
          <div className="health-item">
            <span className="health-label">Status</span>
            <span
              className={`health-badge ${
                wsHealth?.connector?.connected ? 'health-badge--connected' : 'health-badge--disconnected'
              }`}
            >
              {wsHealth?.connector?.connected ? '● Connected' : '● Disconnected'}
            </span>
          </div>
          {wsHealth?.connector?.last_ts && (
            <div className="health-item">
              <span className="health-label">Last Update</span>
              <span className="health-value">{formatIsoTime(wsHealth.connector.last_ts)}</span>
            </div>
          )}
        </div>
      ) : (
        <div className="health-items">
          <div className="health-item">
            <span className="health-label">Trades</span>
            <span
              className={`health-badge ${
                wsHealth?.trades?.connected ? 'health-badge--connected' : 'health-badge--disconnected'
              }`}
            >
              {wsHealth?.trades?.connected ? '● Connected' : '● Disconnected'}
            </span>
          </div>
          <div className="health-item">
            <span className="health-label">Depth</span>
            <span
              className={`health-badge ${
                wsHealth?.depth?.connected ? 'health-badge--connected' : 'health-badge--disconnected'
              }`}
            >
              {wsHealth?.depth?.connected ? '● Connected' : '● Disconnected'}
            </span>
          </div>
          {wsHealth?.trades?.last_ts && (
            <div className="health-item">
              <span className="health-label">Last Trade</span>
              <span className="health-value">{formatIsoTime(wsHealth.trades.last_ts)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function FootprintPanel({ metrics }: { metrics: MetricsResponse | null }) {
  const footprint = metrics?.metrics?.footprint ?? [];
  const topEntries = footprint.slice(0, 8);

  return (
    <div className="panel footprint-card">
      <div className="panel__header">
        <h2>Volume Footprint (Top 8)</h2>
      </div>
      {topEntries.length > 0 ? (
        <div className="footprint-table">
          {topEntries.map((entry) => {
            const total = entry.buy_vol + entry.sell_vol;
            const buyRatio = total > 0 ? (entry.buy_vol / total) * 100 : 0;
            return (
              <div key={entry.price} className="footprint-row">
                <div className="footprint-price">{formatPrice(entry.price)}</div>
                <div className="footprint-volume">{formatVolume(entry.volume)}</div>
                <div className="footprint-bar">
                  <div
                    className="footprint-bar__buy"
                    style={{ width: `${buyRatio}%` }}
                    title={`Buy: ${formatVolume(entry.buy_vol)}`}
                  />
                  <div
                    className="footprint-bar__sell"
                    style={{ width: `${100 - buyRatio}%` }}
                    title={`Sell: ${formatVolume(entry.sell_vol)}`}
                  />
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="panel__muted">No footprint data available yet.</p>
      )}
    </div>
  );
}

export default function DashboardClient({
  baseUrl,
  initialContext,
  initialHealth,
  initialWsHealth,
  initialPrice,
  initialMetrics,
}: DashboardClientProps): JSX.Element {
  const [context, setContext] = useState<ContextResponse | null>(initialContext);
  const [health, setHealth] = useState<HealthResult | null>(initialHealth);
  const [wsHealth, setWsHealth] = useState<WsHealthExtended | null>(initialWsHealth);
  const [pricePoints, setPricePoints] = useState<PricePoint[]>(() => {
    const point = payloadToPoint(initialPrice ?? initialContext?.price ?? null);
    return point ? [point] : [];
  });
  const [metrics, setMetrics] = useState<MetricsResponse | null>(initialMetrics);

  const session = context?.session ?? DEFAULT_SESSION;
  const [utcClock, setUtcClock] = useState<Date>(() => safeDate(session.nowUtc) ?? new Date());

  useEffect(() => {
    const parsed = safeDate(session.nowUtc);
    if (parsed) {
      setUtcClock(parsed);
    }
  }, [session.nowUtc]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setUtcClock((prev) => {
        if (!prev || Number.isNaN(prev.getTime())) {
          return new Date();
        }
        return new Date(prev.getTime() + 1000);
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  // Fetch context
  useEffect(() => {
    let active = true;

    const fetchContextData = async () => {
      try {
        const data = await fetchContext(baseUrl);
        if (!active) {
          return;
        }
        if (data) {
          setContext(data);
        }
      } catch (error) {
        console.warn('context_fetch_error', error);
      }
    };

    const interval = window.setInterval(fetchContextData, CONTEXT_POLL_INTERVAL);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [baseUrl]);

  // Fetch health
  useEffect(() => {
    let active = true;

    const fetchHealthData = async () => {
      try {
        const [healthResponse, wsResponse] = await Promise.all([
          fetchHealthStatus(baseUrl),
          fetchWsHealth(baseUrl),
        ]);

        if (active) {
          if (healthResponse) {
            setHealth(healthResponse);
          }
          if (wsResponse) {
            setWsHealth(wsResponse);
          }
        }
      } catch (error) {
        console.warn('health_fetch_error', error);
      }
    };

    const interval = window.setInterval(fetchHealthData, HEALTH_POLL_INTERVAL);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [baseUrl]);

  // Fetch metrics
  useEffect(() => {
    let active = true;

    const fetchMetricsData = async () => {
      try {
        const data = await fetchMetrics(baseUrl);
        if (active && data) {
          setMetrics(data);
        }
      } catch (error) {
        console.warn('metrics_fetch_error', error);
      }
    };

    const interval = window.setInterval(fetchMetricsData, METRICS_POLL_INTERVAL);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [baseUrl]);

  // Fetch price
  useEffect(() => {
    let active = true;

    const fetchPriceData = async () => {
      try {
        const payload = await fetchPrice(baseUrl);
        if (active && payload) {
          const point = payloadToPoint(payload);
          if (point) {
            setPricePoints((prev) => appendPricePoint(prev, point));
          }
        }
      } catch (error) {
        console.warn('price_fetch_error', error);
      }
    };

    const interval = window.setInterval(fetchPriceData, PRICE_POLL_INTERVAL);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [baseUrl]);

  // Update price from context
  useEffect(() => {
    const point = payloadToPoint(context?.price);
    if (point) {
      setPricePoints((prev) => appendPricePoint(prev, point));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [context?.price]);

  const overlayLines = useMemo<OverlayLine[]>(() => {
    const levels = context?.levels;
    if (!levels) {
      return [];
    }
    const lines: OverlayLine[] = [];

    if (isNumber(levels.VWAP)) {
      lines.push({ id: 'VWAP', label: 'VWAP', value: levels.VWAP as number, color: '#38bdf8' });
    }

    if (isNumber(levels.POCd)) {
      lines.push({ id: 'POCd', label: 'POC (Current)', value: levels.POCd as number, color: '#fb923c' });
    }

    if (levels.OR) {
      if (isNumber(levels.OR.hi)) {
        lines.push({ id: 'OR_HI', label: 'OR High', value: levels.OR.hi as number, color: '#facc15' });
      }
      if (isNumber(levels.OR.lo)) {
        lines.push({
          id: 'OR_LO',
          label: 'OR Low',
          value: levels.OR.lo as number,
          color: '#facc15',
          dashed: true,
        });
      }
    }

    const prevColor = '#cbd5f5';
    if (isNumber(levels.PDH)) {
      lines.push({
        id: 'PDH',
        label: 'Prev Day High',
        value: levels.PDH as number,
        color: prevColor,
        dashed: true,
      });
    }
    if (isNumber(levels.PDL)) {
      lines.push({
        id: 'PDL',
        label: 'Prev Day Low',
        value: levels.PDL as number,
        color: prevColor,
        dashed: true,
      });
    }
    if (isNumber(levels.VAHprev)) {
      lines.push({
        id: 'VAHprev',
        label: 'Prev VAH',
        value: levels.VAHprev as number,
        color: prevColor,
        dashed: true,
      });
    }
    if (isNumber(levels.VALprev)) {
      lines.push({
        id: 'VALprev',
        label: 'Prev VAL',
        value: levels.VALprev as number,
        color: prevColor,
        dashed: true,
      });
    }
    if (isNumber(levels.POCprev)) {
      lines.push({
        id: 'POCprev',
        label: 'Prev POC',
        value: levels.POCprev as number,
        color: prevColor,
        dashed: true,
      });
    }

    return lines;
  }, [context?.levels]);

  const healthSummary = useMemo(() => computeHealthSummary(health, wsHealth), [health, wsHealth]);

  const latestPoint = pricePoints[pricePoints.length - 1] ?? payloadToPoint(context?.price) ?? null;
  const latestPrice = latestPoint?.price ?? null;
  const lastUpdateLabel = formatIsoTime(latestPoint?.ts ?? context?.price?.ts ?? null);
  const stats = context?.stats;
  const levels = context?.levels;

  const levelRows = useMemo(
    () => {
      if (!levels) {
        return [] as Array<{ id: string; label: string; value: number | null; accent: string }>;
      }
      return [
        { id: 'VWAP', label: 'VWAP', value: levels.VWAP, accent: 'vwap' },
        { id: 'OR_HI', label: 'Opening Range High', value: levels.OR?.hi ?? null, accent: 'or' },
        { id: 'OR_LO', label: 'Opening Range Low', value: levels.OR?.lo ?? null, accent: 'or' },
        { id: 'POCd', label: 'POC (Current Day)', value: levels.POCd, accent: 'poc' },
        { id: 'PDH', label: 'Previous Day High', value: levels.PDH, accent: 'prev' },
        { id: 'PDL', label: 'Previous Day Low', value: levels.PDL, accent: 'prev' },
        { id: 'VAHprev', label: 'Prev Value Area High', value: levels.VAHprev, accent: 'prev' },
        { id: 'VALprev', label: 'Prev Value Area Low', value: levels.VALprev, accent: 'prev' },
        { id: 'POCprev', label: 'Prev Point of Control', value: levels.POCprev, accent: 'prev' },
      ];
    },
    [levels],
  );

  return (
    <main className="dashboard">
      <header className="dashboard__header">
        <div>
          <h1 className="dashboard__title">Botcrypto4</h1>
          <p className="dashboard__subtitle">
            Live trading context dashboard with real-time metrics and session awareness.
          </p>
        </div>
        <div className="session-status">
          <span className={SESSION_BADGE_CLASS[session.state]}>{SESSION_LABELS[session.state]}</span>
          <span className="session-clock">{formatUtcClock(utcClock)}</span>
        </div>
      </header>

      <section className={`health-banner health-banner--${healthSummary.level}`}>
        <span className="health-banner__dot" aria-hidden />
        <div className="health-banner__content">
          <span className="health-banner__summary">
            System health:{' '}
            {healthSummary.level === 'ok'
              ? 'Operational'
              : healthSummary.level === 'down'
              ? 'Down'
              : healthSummary.level === 'degraded'
              ? 'Degraded'
              : 'Unknown'}
          </span>
          <div className="health-banner__details">
            {healthSummary.details.map((detail) => (
              <span key={detail}>{detail}</span>
            ))}
          </div>
        </div>
      </section>

      <div className="dashboard__main">
        <section className="panel chart-card">
          <div className="chart-card__header">
            <div>
              <h2>BTC Price Context</h2>
              <p className="price-meta">Last update: {lastUpdateLabel}</p>
            </div>
            <div className="price-value">{formatPrice(latestPrice)}</div>
          </div>
          <PriceChart points={pricePoints} overlays={overlayLines} />
        </section>

        <aside className="panel levels-card">
          <div className="panel__header">
            <h2>Context Levels</h2>
          </div>
          {levels ? (
            <>
              <div className="levels-table">
                {levelRows.map((row) => (
                  <div key={row.id} className="levels-row">
                    <span className="levels-row__label">{row.label}</span>
                    <span className={`levels-row__value levels-row__value--${row.accent}`}>
                      {formatPrice(row.value)}
                    </span>
                  </div>
                ))}
              </div>
              <div className="levels-meta">
                <span>
                  Opening range window:{' '}
                  {levels.OR
                    ? `${formatIsoTime(levels.OR.startTs)} → ${formatIsoTime(levels.OR.endTs)}`
                    : '—'}
                </span>
              </div>
              {stats && (
                <div className="levels-stats">
                  <div className="levels-stats__item">
                    <span>Range Today</span>
                    <span className="levels-stats__value">{formatPrice(stats.rangeToday)}</span>
                  </div>
                  <div className="levels-stats__item">
                    <span>Pre-market Δ</span>
                    <span
                      className={`levels-stats__value ${
                        isNumber(stats.cd_pre)
                          ? stats.cd_pre < 0
                            ? 'levels-stats__value--negative'
                            : stats.cd_pre > 0
                            ? 'levels-stats__value--positive'
                            : ''
                          : ''
                      }`}
                    >
                      {formatSigned(stats.cd_pre)}
                    </span>
                  </div>
                </div>
              )}
            </>
          ) : (
            <p className="panel__muted">Context data unavailable.</p>
          )}
        </aside>
      </div>

      <div className="dashboard__panels">
        <MetricsPanel metrics={metrics} />
        <SessionPanel context={context} />
        <BackfillStatusPanel health={health} metrics={metrics} />
        <ConnectorHealthPanel wsHealth={wsHealth} />
        <FootprintPanel metrics={metrics} />
      </div>
    </main>
  );
}
