export const dynamic = "force-dynamic";

type HealthResult = {
  status: string;
  message: string;
};

async function fetchHealthStatus(): Promise<HealthResult> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const sanitizedBaseUrl = baseUrl.endsWith("/") ? baseUrl.slice(0, -1) : baseUrl;
  const url = `${sanitizedBaseUrl}/health`;

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

export default async function Home(): Promise<JSX.Element> {
  const health = await fetchHealthStatus();
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const isHealthy = health.status.toLowerCase() === "ok";

  return (
    <main className="page">
      <header className="page__header">
        <h1>Botcrypto4</h1>
        <p>Monorepo scaffold with Next.js frontend and FastAPI backend</p>
      </header>

      <section className="card">
        <h2>Backend Health</h2>
        <p className="card__status">
          Status: <span className={isHealthy ? "status-ok" : "status-error"}>{health.status}</span>
        </p>
        <p className="card__message">{health.message}</p>
      </section>

      <footer className="page__footer">
        Checking <code>{`${apiBaseUrl.replace(/\/$/, "")}/health`}</code>
      </footer>
    </main>
  );
}
