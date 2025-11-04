export const dynamic = "force-dynamic";

type HealthResult = {
  status: string;
  message: string;
};

type OpeningRange = {
  hi: number | null;
  lo: number | null;
  startTs: string | null;
  endTs: string | null;
};

type ContextLevels = {
  OR: OpeningRange;
  VWAP: number | null;
  PDH: number | null;
  PDL: number | null;
  VAHprev: number | null;
  VALprev: number | null;
  POCd: number | null;
  POCprev: number | null;
};

type ContextStats = {
  rangeToday: number | null;
  cd_pre: number | null;
};

type SessionState = {
  state: "off" | "london" | "overlap";
  nowUtc: string;
};

type ContextResponse = {
  session: SessionState;
  levels: ContextLevels;
  stats: ContextStats;
};

type SessionKey = SessionState["state"];

const SESSION_LABELS: Record<SessionKey, string> = {
  off: "Market Closed",
  london: "London Session",
  overlap: "US–London Overlap",
};

const DEFAULT_SESSION: SessionState = {
  state: "off",
  nowUtc: new Date().toISOString(),
};

function sanitizeBaseUrl(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

function formatNumeric(value: number | null | undefined, digits = 2): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "—";
  }

  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatSigned(value: number | null | undefined, digits = 2): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "—";
  }

  const prefix = value >= 0 ? "+" : "−";
  return `${prefix}${Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

function formatWindow(start: string | null | undefined, end: string | null | undefined): string {
  if (!start || !end) {
    return "—";
  }

  const startIso = new Date(start);
  const endIso = new Date(end);

  if (Number.isNaN(startIso.getTime()) || Number.isNaN(endIso.getTime())) {
    return `${start} – ${end}`;
  }

  const startLabel = startIso.toISOString().slice(11, 16);
  const endLabel = endIso.toISOString().slice(11, 16);

  return `${startLabel} – ${endLabel} UTC`;
}

async function fetchHealthStatus(): Promise<HealthResult> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const url = `${sanitizeBaseUrl(baseUrl)}/health`;

  try {
    const response = await fetch(url, { cache: "no-store" });

    if (!response.ok) {
      return {
        status: "unreachable",
        message: `Unexpected response (${response.status})`,
      };
    }

    const data = (await response.json()) as { status?: string };

    return {
      status: data.status ?? "unknown",
      message: "Backend reachable",
    };
  } catch (error) {
    return {
      status: "unreachable",
      message: error instanceof Error ? error.message : "Unknown error",
    };
  }
}

async function fetchContext(): Promise<ContextResponse | null> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const url = `${sanitizeBaseUrl(baseUrl)}/context`;

  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      return null;
    }
    const data = (await response.json()) as ContextResponse;
    return data;
  } catch (_error) {
    return null;
  }
}

export default async function Home(): Promise<JSX.Element> {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const sanitizedUrl = sanitizeBaseUrl(apiBaseUrl);
  const [health, context] = await Promise.all([fetchHealthStatus(), fetchContext()]);
  const isHealthy = health.status.toLowerCase() === "ok";

  const session = context?.session ?? DEFAULT_SESSION;
  const levels = context?.levels;
  const stats = context?.stats;

  return (
    <main className="page">
      <header className="page__header">
        <div className="page__header-top">
          <h1>Botcrypto4</h1>
          {context && (
            <span className={`session-chip session-chip--${session.state}`} data-state={session.state}>
              {SESSION_LABELS[session.state] ?? session.state}
            </span>
          )}
        </div>
        <p>Monorepo scaffold with Next.js frontend and FastAPI backend</p>
        {context && (
          <p className="page__header-meta">
            Now (UTC): <code>{session.nowUtc}</code>
          </p>
        )}
      </header>

      <section className="card">
        <h2>Backend Health</h2>
        <p className="card__status">
          Status: <span className={isHealthy ? "status-ok" : "status-error"}>{health.status}</span>
        </p>
        <p className="card__message">{health.message}</p>
      </section>

      <section className="card card--context">
        <h2>Market Context</h2>
        {context && levels && stats ? (
          <div className="context-grid">
            <div className="context-grid__group">
              <h3>Opening Range</h3>
              <div className="context-metric">
                <span className="context-metric__label">High</span>
                <span className="context-metric__value">{formatNumeric(levels.OR.hi)}</span>
              </div>
              <div className="context-metric">
                <span className="context-metric__label">Low</span>
                <span className="context-metric__value">{formatNumeric(levels.OR.lo)}</span>
              </div>
              <p className="context-meta">Window: {formatWindow(levels.OR.startTs, levels.OR.endTs)}</p>
            </div>

            <div className="context-grid__group">
              <h3>Intraday</h3>
              <div className="context-metric">
                <span className="context-metric__label">VWAP</span>
                <span className="context-metric__value">{formatNumeric(levels.VWAP)}</span>
              </div>
              <div className="context-metric">
                <span className="context-metric__label">Range</span>
                <span className="context-metric__value">{formatNumeric(stats.rangeToday)}</span>
              </div>
              <div className="context-metric">
                <span className="context-metric__label">POC</span>
                <span className="context-metric__value">{formatNumeric(levels.POCd)}</span>
              </div>
              <div className="context-metric">
                <span className="context-metric__label">Δ Pre-market</span>
                <span className="context-metric__value">{formatSigned(stats.cd_pre)}</span>
              </div>
            </div>

            <div className="context-grid__group">
              <h3>Previous Day</h3>
              <div className="context-metric">
                <span className="context-metric__label">PDH</span>
                <span className="context-metric__value">{formatNumeric(levels.PDH)}</span>
              </div>
              <div className="context-metric">
                <span className="context-metric__label">PDL</span>
                <span className="context-metric__value">{formatNumeric(levels.PDL)}</span>
              </div>
              <div className="context-metric">
                <span className="context-metric__label">VAH</span>
                <span className="context-metric__value">{formatNumeric(levels.VAHprev)}</span>
              </div>
              <div className="context-metric">
                <span className="context-metric__label">VAL</span>
                <span className="context-metric__value">{formatNumeric(levels.VALprev)}</span>
              </div>
              <div className="context-metric">
                <span className="context-metric__label">POC</span>
                <span className="context-metric__value">{formatNumeric(levels.POCprev)}</span>
              </div>
            </div>
          </div>
        ) : (
          <p className="card__message">Context data unavailable.</p>
        )}
      </section>

      <footer className="page__footer">
        Checking <code>{`${sanitizedUrl}/health`}</code> & <code>{`${sanitizedUrl}/context`}</code>
      </footer>
    </main>
  );
}
