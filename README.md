# Crypto Trading Bot - Order Flow + Liquidation Sweeps

## 🎯 Estrategia

### Setup
- **Detección**: Liquidation sweeps en tiempo real
- **Confirmación**: CVD divergencia + Volume Delta spike
- **Entrada**: Cuando precio toca/rompe liquidation cluster y CVD confirma
- **SL**: Justo abajo del liquidation wall
- **TP**: Resistencia o siguiente liquidation cluster
- **RR objetivo**: 1:5 a 1:10

### Timeframe
- **Macro**: Daily/4H (contexto, verificar 1-2 veces/día)
- **Micro**: 5-15 min (ejecución)
- **Duration**: 30 seg a 2 min por operación (scalping)

### Filters Críticos
- ✓ Funding rate: Si > ±0.15% → SKIP
- ✓ Volumen día: Si < 50% promedio → SKIP
- ✓ Market extremo: Si pánico/euphoria → SKIP

## 🏗️ Arquitectura

### Backend (FastAPI)
- **WebSocket**: Conecta Bybit/Binance trades en vivo
- **Indicadores**: CVD, Volume Delta (tiempo real)
- **Liquidations**: Fetch API cada 10 seg
- **Strategy Engine**: Detecta setups + calcula entrada/SL/TP
- **Alerts**: Genera señal cuando hay confluencia

### Frontend (Next.js)
- **Dashboard**: Charts CVD, Volume Delta, Liquidations
- **Real-time**: WebSocket updates
- **Alerts**: Notificación cuando hay setup
- **Signals**: Mostra entrada/SL/TP recomendado
- **Manual Execution**: Botón para ir al exchange (tú ejecutas)

## 📊 Indicadores Implementados

- [ ] CVD (Cumulative Volume Delta)
- [ ] Volume Delta (buy vs sell)
- [ ] Liquidation clusters por precio
- [ ] Sweep detector
- [ ] Entrada/SL/TP calculator
- [ ] Macro filters (funding, volume)

## 🚀 Roadmap

### Phase 1: Foundation (T2-T3)
- T2: WebSocket connector Bybit
- T3: API /trades endpoint

### Phase 2: Indicadores (T4-T5)
- T4: CVD calculator
- T5: Volume Delta

### Phase 3: Data (T6-T7)
- T6: Liquidation tracker
- T7: Macro filters

### Phase 4: Strategy (T8)
- T8: Sweep detector + engine

### Phase 5: Frontend (T9-T11)
- T9: Dashboard
- T10: Real-time charts
- T11: Alerts + signals

## 🔄 Stack

- **Backend**: FastAPI, Python 3.11+
- **Frontend**: Next.js, TypeScript, React
- **Data**: WebSocket (Bybit), REST APIs
- **DB**: PostgreSQL (si necesario)

## 📝 Próximo paso
[Se actualiza después de cada merge]