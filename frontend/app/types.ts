export type HealthResult = {
  status: string;
  message?: string;
};

export type StreamHealth = {
  connected: boolean;
  last_ts: string | null;
};

export type WsHealth = {
  trades?: StreamHealth;
  depth?: StreamHealth;
};

type NullableNumber = number | null;
type NullableString = string | null;

export type OpeningRange = {
  hi: NullableNumber;
  lo: NullableNumber;
  startTs: NullableString;
  endTs: NullableString;
};

export type ContextLevels = {
  OR: OpeningRange;
  VWAP: NullableNumber;
  PDH: NullableNumber;
  PDL: NullableNumber;
  VAHprev: NullableNumber;
  VALprev: NullableNumber;
  POCd: NullableNumber;
  POCprev: NullableNumber;
};

export type ContextStats = {
  rangeToday: NullableNumber;
  cd_pre: NullableNumber;
};

export type SessionState = {
  state: "off" | "london" | "overlap";
  nowUtc: string;
};

export type PricePayload = {
  price: NullableNumber;
  ts: NullableString;
  symbol?: string;
};

export type ContextResponse = {
  session: SessionState;
  levels: ContextLevels;
  stats: ContextStats;
  price?: PricePayload;
};

export type FootprintEntry = {
  price: number;
  volume: number;
  buy_vol: number;
  sell_vol: number;
  rank: number;
};

export type MetricsPayload = {
  vwap: NullableNumber;
  poc: NullableNumber;
  delta: NullableNumber;
  buy_volume: NullableNumber;
  sell_volume: NullableNumber;
  footprint: FootprintEntry[];
  trade_count: number;
};

export type MetricsResponse = {
  metrics: MetricsPayload | null;
  metadata: {
    last_update: NullableString;
    trade_count: number;
    buffer_size: number;
  };
};

export type StreamHealthDetail = {
  connected: boolean;
  last_ts: NullableString;
  last_update_id?: number;
  events_received?: number;
  reconnect_attempts?: number;
};

export type WsHealthExtended = {
  trades?: StreamHealthDetail;
  depth?: StreamHealthDetail;
  connector?: StreamHealthDetail;
};

export type StrategyStatus = {
  engine_state: Record<string, unknown>;
  context_analysis: Record<string, unknown> | null;
  scheduler_state: {
    current_session: string;
    is_active: boolean;
    hours_remaining?: number;
    next_session?: string;
  };
};

export type BackfillStatus = {
  cache_status: "HIT" | "MISS" | "UNKNOWN";
  trades_loaded: number;
  completion_time_ms: number;
  historical_range: {
    start: NullableString;
    end: NullableString;
  };
};
