# Troubleshooting & Debug Guide - Botcrypto4

## 1. TEST FAILURES - Quick Diagnosis

### 1.1 Los 19 Tests Fallando

#### Categoría A: Async Cleanup Issues
**Tests Afectados**:
- `test_context.py::TestPriceBinning::test_quantize_price_errors`
- `test_strategy_engine.py` (multiple)
- `test_dynamic_backfill.py` (multiple)

**Síntomas**:
```
RuntimeWarning: Task was destroyed but it is pending!
Unclosed client session
coroutine 'BinanceTradeHistory._backfill_parallel...' was never awaited
```

**Root Cause**:
- ContextService crea tasks pero no las cancela correctamente en shutdown
- aiohttp ClientSession no se cierra en todos los paths

**Diagnosis**:
```bash
# Ver el error exacto
pytest app/tests/test_context.py -v -s

# Con asyncio debug mode
PYTHONASYNCDEBUG=1 pytest app/tests/test_context.py -v
```

**Fix Necesario**:
```python
# En ContextService.__init__
self._client_session: Optional[aiohttp.ClientSession] = None

# En startup()
self._client_session = aiohttp.ClientSession()

# En shutdown() - ADD THIS
if self._client_session:
    await self._client_session.close()

# En _backfill_background() - USE ASYNC CONTEXT
async with aiohttp.ClientSession() as session:
    # Use session
    pass  # Auto cleanup
```

---

#### Categoría B: Bybit Cache Issues
**Tests Afectados**:
- `test_backfill_cache.py::test_bybit_trade_tick_to_dict_conversion`
- `test_backfill_cache.py::test_bybit_cache_resume_functionality`
- `test_backfill_cache.py::test_bybit_cache_deduplication`
- `test_bybit_backfill.py` (4 tests)

**Síntomas**:
```
AssertionError: expected X but got Y
TypeError: dict conversion failed
```

**Root Cause**:
- BybitConnectorHistory no está serializando trades correctamente a Parquet
- Cache resume logic tiene bugs en deduplication

**Diagnosis**:
```bash
# Ver detalles del error
pytest app/tests/test_backfill_cache.py::TestBybitCacheIntegration::test_bybit_trade_tick_to_dict_conversion -vvs

# Checa the cache file
ls -la context_history_dir/backfill_cache/
```

**Common Issues**:
1. **TradeTick → dict conversion**
   - Falta convertir timestamp a milliseconds
   - Side enum no serializa a string

2. **Cache deduplication**
   - Trades duplicados en resume
   - Sort order diferente entre saves

**Fix Pattern**:
```python
def trade_to_dict(trade: TradeTick) -> dict:
    return {
        "timestamp": int(trade.timestamp.timestamp() * 1000),  # Milliseconds!
        "price": float(trade.price),
        "qty": float(trade.qty),
        "side": trade.side.value,  # Enum to string
        "id": trade.id,
        "is_buyer_maker": trade.is_buyer_maker,
    }
```

---

#### Categoría C: Dynamic Backfill Issues
**Tests Afectados**:
- `test_dynamic_backfill.py::test_dynamic_backfill_00_05_utc`
- `test_dynamic_backfill.py::test_dynamic_backfill_12_00_utc`
- `test_dynamic_backfill.py::test_dynamic_backfill_23_55_utc`
- `test_dynamic_backfill.py::test_backfill_with_cache_hit`
- `test_dynamic_backfill.py::test_backfill_with_cache_miss`

**Síntomas**:
```
AssertionError: expected X chunks but got Y
Timeout waiting for backfill
```

**Root Cause**:
- Cálculo dinámico de chunks no es correcto para todas las horas UTC
- Cache hit/miss logic tiene timing issues

**Diagnosis**:
```bash
# Debug específico para cada hora
pytest app/tests/test_dynamic_backfill.py::test_dynamic_backfill_00_05_utc -vvs

# Con logs
LOG_LEVEL=DEBUG pytest app/tests/test_dynamic_backfill.py -v
```

**Expected Behavior**:
```
Current Time → Calcula range desde session start → Chunks dinámicos
00:05 UTC → From 08:00 prev day → 8 hours = 48 chunks (10min each)
12:05 UTC → From 08:00 today → 4 hours = 24 chunks
23:55 UTC → From 13:00 today → 10.9 hours ≈ 65 chunks
```

**Fix Pattern**:
```python
def calculate_backfill_chunks(now: datetime) -> int:
    session_start = calculate_session_start(now)
    duration = (now - session_start).total_seconds() / 60  # minutes
    chunks = duration / 10  # 10 min per chunk
    return max(1, int(chunks))
```

---

#### Categoría D: Backfill Core Pagination Issues
**Tests Afectados**:
- `test_backfill.py::TestBinanceTradeHistory::test_pagination_logic`
- `test_backfill.py::TestBinanceTradeHistory::test_reduced_concurrency_and_throttling`
- `test_backfill.py::TestCircuitBreakerWithRateLimitingScenarios::test_progressive_recovery_of_throttle_multiplier`

**Síntomas**:
```
AssertionError: pagination order incorrect
Timeout in fetch
```

**Root Cause**:
- Lógica de paginación en BinanceTradeHistory tiene off-by-one error
- Throttling multiplier no se recupera correctamente

**Diagnosis**:
```bash
pytest app/tests/test_backfill.py::TestBinanceTradeHistory::test_pagination_logic -vvs

# Ver logs de throttle
pytest app/tests/test_backfill.py::TestCircuitBreakerWithRateLimitingScenarios -vvs
```

---

### 1.2 Cómo Debuggear Tests

#### Setup de Debug Básico
```python
# En el test
import logging
logging.basicConfig(level=logging.DEBUG)

@pytest.mark.asyncio
async def test_something():
    service = ContextService(...)
    try:
        await service.startup()
        # Your assertions
    finally:
        await service.shutdown()  # IMPORTANT!
```

#### Fixture Mejorada
```python
@pytest.fixture
async def context_service():
    """Context service with proper cleanup."""
    service = ContextService(context_backfill_enabled=False)
    await service.startup()
    yield service
    # Cleanup en orden
    await service.shutdown()  # Cancela tasks
    # Optional: await asyncio.sleep(0.1)  # Let cleanup finish
```

#### Debugging de Tasks Pendientes
```bash
# Script para debuggear
cat > debug_tasks.py << 'EOF'
import asyncio

async def show_tasks():
    tasks = asyncio.all_tasks()
    for task in tasks:
        print(f"Task: {task.get_name()}")
        print(f"  Coro: {task.get_coro()}")
        print(f"  Done: {task.done()}")

asyncio.run(show_tasks())
EOF

python debug_tasks.py
```

---

## 2. RUNTIME ERRORS - Solutions

### 2.1 "Bot takes 2+ minutes to start"
**Problema**: Antes de non-blocking backfill implementation

**Solución**: Verificar que `NON_BLOCKING_BACKFILL.md` está aplicado
```bash
# Check if background task implemented
grep "_backfill_task" backend/app/context/service.py

# Check if asyncio.create_task used
grep "create_task" backend/app/context/service.py
```

**Verificar**:
```bash
# Startup debe ser instantáneo
curl -w "\nTime: %{time_total}\n" http://localhost:8000/health
# Response should be < 100ms
```

---

### 2.2 "VWAP/POC showing as None"
**Problema**: Backfill no completed o cache issue

**Diagnóstico**:
```bash
# 1. Check backfill status
curl http://localhost:8000/backfill/status

# 2. Check context metrics
curl http://localhost:8000/context | jq .levels

# 3. Check logs
docker compose logs backend | grep -i "backfill"

# 4. Check cache files
ls -la context_history_dir/backfill_cache/
```

**Soluciones Posibles**:
1. Backfill still running: Wait o check `/backfill/status`
2. Backfill failed: Check logs para error
3. Cache issue: Delete cache y retry
   ```bash
   rm -rf context_history_dir/backfill_cache/*
   # Restart service
   ```

---

### 2.3 "Rate limit errors (418/429)"
**Problema**: Binance rate limiting

**Diagnóstico**:
```bash
# Ver logs
docker compose logs backend | grep -i "418\|429\|rate"

# Check circuit breaker status
curl http://localhost:8000/context | jq .backfill_status
```

**Soluciones**:
1. **Esperar**: Circuit breaker espera 60 segundos
2. **Reduce concurrency**: Set `BACKFILL_MAX_CONCURRENT_CHUNKS=2`
3. **Increase delay**: Set `BACKFILL_PUBLIC_DELAY_MS=200`
4. **Use API keys**: Set BINANCE_API_KEY + BINANCE_API_SECRET

---

### 2.4 "WebSocket disconnects"
**Problema**: Binance WS dropping connection

**Diagnóstico**:
```bash
# Check WS health
curl http://localhost:8000/ws/health | jq

# Ver logs
docker compose logs backend | grep -i "websocket\|disconnect"
```

**Soluciones**:
1. Check network connectivity
2. Check Binance API status
3. Increase reconnect backoff (code change)
4. Switch to bybit_connector (if testing)

---

### 2.5 "Memory usage increasing"
**Problema**: Memory leak

**Diagnóstico**:
```bash
# Monitor memory
docker stats botcrypto4-backend-1

# Check for unclosed resources
docker compose logs backend | grep -i "unclosed\|not await"

# Python memory profiler
pip install memory-profiler
python -m memory_profiler app.main
```

**Common Causes**:
1. ClientSession not closed → Fix: use async context manager
2. Tasks not awaited → Fix: use `wait_for_backfill()`
3. Large cache not rotating → Fix: implement TTL

---

## 3. DOCKER ISSUES

### 3.1 Container won't start
```bash
# Check logs
docker compose logs backend

# Rebuild from scratch
docker compose down -v
docker compose build --no-cache
docker compose up

# Check if ports in use
lsof -i :8000
lsof -i :3000
```

### 3.2 Network issues between containers
```bash
# Check connectivity
docker compose exec backend ping frontend
docker compose exec frontend ping backend

# Check DNS
docker compose exec backend cat /etc/resolv.conf

# Restart networking
docker network prune
docker compose up
```

### 3.3 Volume mount issues (Windows)
```bash
# Use WSL2 or Docker Desktop native
# Or explicitly use WSL backend
# Settings → Resources → WSL Integration
```

---

## 4. PERFORMANCE ISSUES

### 4.1 Slow Backfill
**Esperado**: ~18-20 segundos para ~12K trades

**Si toma más**:
1. **Reduce chunks**: Set `BACKFILL_MAX_RETRIES=3`
2. **Parallel fetch**: Increase `BACKFILL_MAX_CONCURRENT_CHUNKS`
3. **Check network**: `ping api.binance.com`
4. **Check rate limiting**: See circuit breaker status

---

### 4.2 High CPU Usage
**Diagnóstico**:
```bash
# Inside container
top
ps aux | grep python

# Check strategy engine workload
curl http://localhost:8000/strategy/status | jq
```

**Optimization**:
1. Reduce candle timeframes (1m only)
2. Reduce analysis frequency
3. Profile with cProfile

---

### 4.3 High Memory Usage
**Diagnóstico**:
```bash
# Inside container
free -h
ps aux | grep python

# Get heap dump
python -c "import app.main; import tracemalloc; tracemalloc.start()"
```

**Optimization**:
1. Limit price bin history
2. Rotate old candles
3. Use incremental cache writes

---

## 5. CONFIGURATION ISSUES

### 5.1 "DATA_SOURCE not recognized"
**Solución**:
```bash
# Valid values:
# - binance_ws (default)
# - bybit
# - hft_connector
# - bybit_connector

# Check env
echo $DATA_SOURCE
# or
cat backend/.env | grep DATA_SOURCE

# Restart service
docker compose restart backend
```

### 5.2 "CORS errors from frontend"
```bash
# Check allowed origins
cat backend/.env | grep CORS

# Should be: http://localhost:3000 (or your frontend URL)

# Set correct origin
export CORS_ALLOW_ORIGINS="http://localhost:3000,http://frontend:3000"

# Restart
docker compose restart backend
```

### 5.3 "API URL not found"
**Frontend**:
```bash
# Check NEXT_PUBLIC_API_URL
cat frontend/.env.local | grep NEXT_PUBLIC_API_URL

# Should be:
# NEXT_PUBLIC_API_URL=http://localhost:8000  (local)
# NEXT_PUBLIC_API_URL=http://backend:8000    (docker)
```

---

## 6. DATABASE / CACHE ISSUES

### 6.1 "Cache is stale"
```bash
# Clear cache
rm -rf context_history_dir/backfill_cache/*
rm -rf data/history/*

# Restart
docker compose restart backend
```

### 6.2 "Parquet file corrupted"
```bash
# Remove corrupt files
find context_history_dir -name "*.parquet" -mtime +7 -delete

# Or nuke entire cache
rm -rf context_history_dir/
```

### 6.3 "Disk space issues"
```bash
# Check usage
du -sh context_history_dir/

# Limit cache size
# Edit BACKFILL_CACHE_DIR setting or:
rm -rf context_history_dir/backfill_cache/*
```

---

## 7. DEVELOPMENT WORKFLOW

### 7.1 Quick Iteration
```bash
# Terminal 1: Run backend with reload
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2: Run frontend
cd frontend
npm run dev

# Terminal 3: Run tests
cd backend
source .venv/bin/activate
pytest app/tests/test_context.py -v --tb=short

# Edit code and tests will auto-reload
```

### 7.2 Debugging Production Issues
```bash
# Enable debug logging
export LOG_LEVEL=DEBUG

# Restart with debug
docker compose restart backend
docker compose logs -f backend

# Look for specific errors
docker compose logs backend | grep -i "error\|exception"
```

### 7.3 Profiling Code
```bash
# CPU profiling
python -m cProfile -s cumulative -o app.prof app/main.py
python -m pstats app.prof

# Memory profiling
python -m memory_profiler app/main.py

# Async profiling
pip install pyflame
pyflame app.main
```

---

## 8. QUICK FIX CHECKLIST

When a test fails, go through this checklist:

- [ ] Run test in isolation: `pytest test_name.py::test_func -vvs`
- [ ] Check async cleanup: Look for `Task was destroyed` warnings
- [ ] Check resource cleanup: Look for `Unclosed client session`
- [ ] Check timeout: Increase `wait_for_backfill(timeout=10)` in test
- [ ] Check mock data: Verify test fixtures are correct
- [ ] Check timestamps: Timezone issues often in date/time logic
- [ ] Check serialization: Dict/JSON conversions failing
- [ ] Run with PYTHONASYNCDEBUG=1
- [ ] Clean cache: `rm -rf context_history_dir/`
- [ ] Restart containers: `docker compose restart`
- [ ] Check logs: `docker compose logs -f`

---

## 9. USEFUL DEBUGGING SNIPPETS

### 9.1 Print All Active Tasks
```python
import asyncio

def print_tasks():
    for task in asyncio.all_tasks():
        print(f"Task: {task.get_name()}: {task.get_coro()}")

# In test or service
print_tasks()
```

### 9.2 Monitor Backfill Progress
```bash
# Script to monitor
while true; do
  curl -s http://localhost:8000/backfill/status | jq .
  curl -s http://localhost:8000/context | jq '.levels | {VWAP, POC}'
  sleep 1
done
```

### 9.3 Test Single Component
```bash
# Just context service
pytest app/tests/test_context.py -v

# Just strategy
pytest app/tests/test_strategy_engine.py -v

# Just backfill
pytest app/tests/test_backfill.py -v
```

### 9.4 Environment Inspector
```bash
# See all env vars in container
docker compose exec backend env | sort

# Check specific setting
docker compose exec backend python -c "from app.ws.models import get_settings; s = get_settings(); print(f'DATA_SOURCE={s.data_source}')"
```

---

## 10. GETTING HELP

### When stuck, collect this info:
1. **Exact error message**: `docker compose logs backend > error.log`
2. **Environment**: `cat backend/.env` (redact API keys)
3. **Test output**: `pytest test_name -vvs > test_output.log`
4. **System info**: `docker --version && docker compose --version`
5. **Git status**: `git log -1 && git status`

### Then:
1. Check `ANALISIS_ESTADO_ACTUAL.md` for context
2. Check `STATUS_QUICK_REFERENCE.md` for quick answers
3. Check relevant `.md` files in root
4. Check strategy framework README
5. Search in code comments

---

**Last Updated**: 13 Nov 2024  
**For Issues With**: Tests, runtime, performance, configuration  
**Related Docs**: ANALISIS_ESTADO_ACTUAL.md, STATUS_QUICK_REFERENCE.md
