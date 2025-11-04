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
