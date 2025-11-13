# QUICK REFERENCE - Estado del Bot Botcrypto4

## 🎯 En Una Oración
Bot de trading cryptocurrency con análisis de régimen de mercado (Range vs Trend), basado en Next.js + FastAPI, con ingestion de múltiples fuentes de datos (Binance, Bybit, HFT Connector) y dashboard en vivo.

## 📊 Estado Actual
- **General**: 🟡 Funcional con algunos test issues
- **Tests**: ✅ 230/249 pasando (92.4%)
- **Frontend**: ✅ 100% operativo (0 errors)
- **Backend**: 🟡 ~95% operativo (19 tests fallando)

## 🏗️ Arquitectura Simple
```
Frontend (Next.js 14)
    ↓ HTTP/JSON
Backend (FastAPI)
    ├── Context Service (métricas vivas: VWAP, POC, volumes)
    ├── Strategy Engine (candle aggregation + regime detection)
    └── WS Module (ingesta datos: Binance/Bybit/HFT)
```

## 🚀 Startup (Non-Blocking!)
1. App inicia en ~0ms (antes: 2+ minutos)
2. Backfill corre en background
3. API/Frontend responden inmediatamente
4. Puedes monitorear: `GET /backfill/status`

## 📁 Archivos Importantes
| Path | Propósito | Líneas |
|------|-----------|--------|
| `backend/app/context/service.py` | Core metrics | 988 |
| `backend/app/strategy/engine.py` | Candles + regime | 310 |
| `frontend/app/dashboard-client.tsx` | UI | 700+ |
| `backend/app/ws/routes.py` | Data routing | 150 |

## ⚙️ Configuración Clave
```env
# Modo de datos
DATA_SOURCE=binance_ws              # binance_ws | bybit | hft_connector | bybit_connector

# Backfill
CONTEXT_BACKFILL_ENABLED=true       # Disable para tests
BACKFILL_CACHE_ENABLED=true         # Persistent cache (Parquet)

# Debug
LOG_LEVEL=INFO                      # DEBUG/INFO/WARNING/ERROR
```

## 🔧 Comandos Rápidos
```bash
# Setup
cd backend && source .venv/bin/activate && pip install -r requirements.txt

# Ejecutar backend
uvicorn app.main:app --reload

# Ejecutar frontend
cd frontend && npm run dev

# Tests
pytest app/tests/ -v

# Docker
docker compose up --build
```

## 📈 Métricas Principales Calculadas
| Métrica | Descripción |
|---------|------------|
| VWAP | Volume Weighted Average Price |
| POC | Point of Control (precio + volumen) |
| Delta | Diferencia compra/venta acumulada |
| VAH/VAL | Highest/Lowest Activity Value |
| PDH/PDL | Previous Day High/Low |

## 📅 Sesiones de Trading
| Sesión | Horario UTC | Características |
|--------|------------|-----------------|
| Londres | 08:00-12:00 | Range-bound típicamente |
| Overlap | 13:00-17:00 | Mayor volatilidad |
| Off | 12:00-13:00, 17:00-08:00 | No trading |

## 🌐 Endpoints principales
| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Status + session |
| `GET /context` | Metrics (VWAP, POC, etc) |
| `GET /strategy/status` | Regime + candles |
| `GET /ws/health` | Connector health |
| `GET /backfill/status` | Backfill progress |
| `GET /metrics` | System metrics |

## 🐛 Issues Conocidos (19 tests fallando)
1. **Async cleanup**: Tasks no awaiting correctamente
2. **ClientSession**: No cerrando en todos los casos
3. **Price quantization**: Algunos edge cases

**Fix**: Usar `AsyncExitStack` para proper cleanup (priority 1)

## ✅ Lo que Funciona Bien
- ✅ Ingesta de trades en vivo (Binance WebSocket)
- ✅ Cálculo de VWAP, POC, Volumen
- ✅ Session management (Londres/Overlap)
- ✅ Candle aggregation (1m, 5m)
- ✅ Backfill no-bloqueante (~18-20s)
- ✅ Cache persistente (Parquet)
- ✅ Dashboard responsive
- ✅ Type-safe TypeScript
- ✅ Circuit breaker + rate limiting
- ✅ Bybit connector (experimental)

## 🚧 Lo que Necesita Arreglar
1. **INMEDIATO** (1-2 días): Fix 19 tests fallando
2. **CORTO PLAZO** (1 semana): Performance tuning
3. **MEDIANO PLAZO** (2 semanas): Production readiness

## 📚 Documentación Completa
- `ANALISIS_ESTADO_ACTUAL.md` - Análisis exhaustivo (este archivo + más detalles)
- `backend/app/strategy/README.md` - Strategy framework
- `frontend/README.md` - Frontend setup
- `README.md` - Getting started

## 🔍 Debug Útil
```bash
# Ver logs en vivo
docker compose logs -f backend

# Check backfill status
curl http://localhost:8000/backfill/status

# Ver session actual
curl http://localhost:8000/health | jq .session

# Metrics en vivo
curl http://localhost:8000/context | jq .levels

# Tests específico
pytest app/tests/test_context.py::TestContextService -v
```

## 🎓 Próximos Pasos (Nueva Sesión)
1. Investigar test failures (focus: async cleanup)
2. Implementar fixes
3. Validar CI/CD pasa 100%
4. Performance tuning
5. Preparar para producción

---

**Última Actualización**: 13 Nov 2024  
**Rama**: `chore-analisis-estado-botcrypto4`  
**Estado**: 🟡 Funcional (tests fixing needed)
