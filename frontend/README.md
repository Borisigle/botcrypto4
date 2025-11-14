# Botcrypto4 Frontend Dashboard

A real-time live trading context dashboard built with **Next.js 14** and **TypeScript**, displaying live bot metrics, trading sessions, and connector health status.

## Overview

The frontend dashboard provides a comprehensive view of live trading data with real-time metric updates, visual indicators for trading sessions, and websocket health monitoring.

### Key Features

- **Live Price Chart**: Real-time BTC price with overlaid technical levels (VWAP, POC, Opening Range, Previous Day levels)
- **Metrics Panel**: VWAP, Point of Control (POC), Delta, Buy/Sell volumes, Trade count
- **Session State Panel**: Current trading session display with color-coded visual indicators
- **Connector Health Panel**: Live status of data source connection (Binance WebSocket, HFT Connector, or Bybit Connector)
- **Volume Footprint**: Top 8 price levels by trading volume with buy/sell breakdown visualization
- **System Health Banner**: Overall system status at a glance
- **Responsive Design**: Mobile-friendly layout with adaptive breakpoints
- **Real-time Updates**: Automatic data refresh every 1-2 seconds

## Architecture

### Component Structure

```
app/
├── page.tsx                 # Server-side entry point, fetches initial data
├── dashboard-client.tsx     # Main client component with all panels
├── api-client.ts           # API utility functions for data fetching
├── types.ts                # TypeScript type definitions
├── layout.tsx              # Root layout component
├── globals.css             # Global styles and dark theme
└── next.config.js          # Next.js configuration
```

### Data Flow

1. **Server-side Fetch** (page.tsx): Initial data fetch from backend APIs on mount
2. **Client Hydration**: Page hydrates with initial data on client
3. **Polling Loop** (dashboard-client.tsx): Client-side React hooks continuously fetch fresh data:
   - **Context**: Every 7 seconds (price levels, session info)
   - **Health**: Every 5 seconds (API health, WebSocket status)
   - **Metrics**: Every 2 seconds (VWAP, POC, Delta, Volumes)
   - **Price**: Every 1 second (latest BTC price)

### API Integration

The dashboard fetches from the following backend endpoints:

| Endpoint | Interval | Purpose |
|----------|----------|---------|
| `GET /health` | 5s | Backend API liveness check |
| `GET /ws/health` | 5s | WebSocket stream health (trades, depth, or connector) |
| `GET /context` | 7s | Trading context (session state, price levels, stats) |
| `GET /price` | 1s | Current BTC price and timestamp |
| `GET /strategy/metrics` | 2s | Live order flow metrics (VWAP, POC, volumes, footprint) |

For detailed readiness information (session status, backfill progress, trading flags), use `GET /ready`. This endpoint is designed for slower readiness checks and is not polled by default.

## Setup & Installation

### Prerequisites

- Node.js 18+ (recommend 20.x)
- npm or yarn
- Backend API running on `http://localhost:8000` (or configured via `NEXT_PUBLIC_API_URL`)

### Installation

```bash
cd frontend
npm install
```

### Environment Configuration

Create a `.env.local` file in the `frontend/` directory:

```env
# Backend API URL (optional, defaults to http://localhost:8000)
NEXT_PUBLIC_API_URL=http://localhost:8000

# Optional: Set Next.js environment
NODE_ENV=development
```

The `NEXT_PUBLIC_API_URL` environment variable is used by the frontend to connect to the backend API.

## Running the Dashboard

### Development Mode

```bash
npm run dev
```

The dashboard will be available at `http://localhost:3000`

### Production Build

```bash
npm run build
npm start
```

### Docker

```bash
docker build -t botcrypto4-frontend .
docker run -p 3000:3000 -e NEXT_PUBLIC_API_URL=http://backend:8000 botcrypto4-frontend
```

## Dashboard Layout

### Top Section

- **Title & Description**: "Botcrypto4" with live trading context dashboard subtitle
- **Session Badge**: Color-coded indicator (Green=London, Blue=Overlap, Gray=Off)
- **UTC Clock**: Live updating wall clock showing current UTC time

### Health Banner

System health status bar showing:
- **Overall Health Level**: Operational (green), Degraded (yellow), Down (red), or Unknown (gray)
- **Component Status**: Individual status of API, Trades WS, Depth WS, or Connector

### Main Content Area

#### Left Column (70% width, responsive)
- **BTC Price Chart**: 
  - Real-time price candlestick with gradient fill
  - Overlaid technical levels with color coding:
    - Blue = VWAP (Volume Weighted Average Price)
    - Orange = POC (Point of Control - Current Day)
    - Yellow = Opening Range (OR High/Low)
    - Light Blue Dashed = Previous Day levels (PDH, PDL, VAH, VAL, POC)
  - Live price display in top right
  - Last update timestamp

- **Context Levels Table** (Right side of chart):
  - VWAP, Opening Range, POC, Previous Day levels
  - Opening range window timestamps
  - Range Today and Pre-market Delta statistics
  - Color-coded values by level type

#### Bottom Section (4-column grid, responsive)

1. **Live Metrics Panel**:
   - VWAP (blue)
   - POC (orange)
   - Delta (green=bullish, red=bearish)
   - Buy Volume (green)
   - Sell Volume (red)
   - Trade Count

2. **Trading Session Panel**:
   - Current session name (London/Overlap/Off)
   - UTC time display
   - Color-coded background matching session state

3. **Connector Health Panel**:
   - Connection status (● Connected / ● Disconnected)
   - Trades and Depth WS status (for Binance mode)
   - Or single Connector status (for HFT/Bybit mode)
   - Last update timestamp

4. **Volume Footprint Panel**:
   - Top 8 price levels by trading volume
   - Total volume per level
   - Buy/Sell split visualization (stacked bar)
   - Color-coded: Green=Buy, Red=Sell

## Styling & Theme

### Color Scheme

- **Primary**: Dark slate (`#020617`, `#0f172a`)
- **Text**: Slate (`#f8fafc`, `#cbd5f5`, `#94a3b8`)
- **Accents**:
  - VWAP: Cyan (`#38bdf8`)
  - POC: Orange (`#fb923c`)
  - Opening Range: Amber (`#facc15`)
  - Buy/Bullish: Green (`#4ade80`)
  - Sell/Bearish: Red (`#f87171`)
  - Session London: Green
  - Session Overlap: Blue

### Responsive Breakpoints

- **Desktop** (900px+): Multi-column grid layout
- **Tablet** (720-900px): Adjusted font sizes, single column for panels
- **Mobile** (<720px): Single column layout, optimized touch targets
- **Small Mobile** (<520px): Further optimizations for compact screens

## Code Quality

### TypeScript

All components are fully typed with TypeScript for type safety:
- `types.ts`: Central type definitions for all data structures
- `api-client.ts`: Typed API response handling
- Components: Full JSX/React type coverage

### Error Handling

- **Network Errors**: Gracefully handled with fallback UI and console warnings
- **Missing Data**: Components safely handle null/undefined values
- **Type Safety**: TypeScript prevents runtime type errors

### Performance

- **Memoization**: `useMemo` prevents unnecessary re-renders of computed values
- **Polling Intervals**: Staggered API calls reduce backend load
- **Price Point Limits**: Chart history capped at 240 points to limit memory usage
- **Lazy Loading**: Components only render when data is available

## Development

### Building

```bash
npm run build
```

### Linting

```bash
npm run lint
```

### Code Formatting

```bash
npm run format
```

### TypeScript Checking

```bash
npx tsc --noEmit
```

## Extending the Dashboard

### Adding New Metrics Panel

1. Define types in `types.ts`:
```typescript
export type NewMetric = {
  value: number;
  timestamp: string;
};
```

2. Add API client function in `api-client.ts`:
```typescript
export async function fetchNewMetric(baseUrl: string): Promise<NewMetric | null> {
  const url = `${baseUrl}/new-endpoint`;
  try {
    const response = await fetchWithTimeout(url);
    if (!response.ok) return null;
    return (await response.json()) as NewMetric;
  } catch (error) {
    console.warn('fetchNewMetric error:', error);
    return null;
  }
}
```

3. Create component in `dashboard-client.tsx`:
```typescript
function NewMetricPanel({ data }: { data: NewMetric | null }) {
  return (
    <div className="panel new-metric-card">
      {/* Panel content */}
    </div>
  );
}
```

4. Add styling in `globals.css`:
```css
.new-metric-card {
  /* Your styles */
}
```

5. Integrate into main dashboard and polling loop.

### Adding New API Endpoint

1. Ensure backend exposes new endpoint
2. Add TypeScript type in `types.ts`
3. Create fetch function in `api-client.ts`
4. Add polling effect in `dashboard-client.tsx`
5. Create/update panel component with new data

## Troubleshooting

### Dashboard Not Loading

**Issue**: Blank page or "Cannot GET /" error

**Solution**:
- Verify Next.js dev server is running: `npm run dev`
- Check port 3000 is not in use: `lsof -i :3000`
- Clear Next.js cache: `rm -rf .next`
- Restart dev server

### No Data Displayed

**Issue**: Dashboard loads but shows "N/A" or "No metrics data available"

**Solution**:
- Verify backend API is running: `curl http://localhost:8000/health`
- Check `NEXT_PUBLIC_API_URL` environment variable is correct
- Verify CORS is enabled on backend
- Check browser console for fetch errors
- Inspect network tab in DevTools to see API responses

### Metrics Not Updating

**Issue**: Data shows but doesn't refresh

**Solution**:
- Check browser DevTools Network tab to see polling requests
- Verify backend endpoints are returning data
- Check for JavaScript errors in console
- Increase `METRICS_POLL_INTERVAL` if backend is slow
- Ensure backend services are ingesting data (check backend logs)

### Chart Not Rendering

**Issue**: Price chart shows "Waiting for price data…"

**Solution**:
- Verify `/price` endpoint is returning valid data
- Check WebSocket connection is active (should see green indicator in Health panel)
- Wait 10-20 seconds for initial data to accumulate
- Check browser console for SVG rendering errors

## Performance Tips

1. **Reduce Polling Frequency**: Increase intervals if backend load is high
   ```typescript
   const METRICS_POLL_INTERVAL = 5000; // 5 seconds instead of 2
   ```

2. **Limit Chart History**: Reduce `MAX_PRICE_POINTS` for older browsers
   ```typescript
   const MAX_PRICE_POINTS = 120; // 120 instead of 240
   ```

3. **Use Production Build**: `npm run build && npm start` is faster than dev mode

4. **Enable Caching**: Add caching headers for static assets

## Browser Support

- Chrome/Chromium 90+
- Firefox 88+
- Safari 14+
- Edge 90+

Mobile browsers should have the same version requirements.

## License

Same as parent project (see repository root).

## Support

For issues, feature requests, or improvements, refer to the project's issue tracker or submit a pull request.
