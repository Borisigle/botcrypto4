# Implementación de Cálculo Incremental VWAP y POCd

## Resumen

Se implementó el flujo completo de cálculo incremental para VWAP (Volume-Weighted Average Price) y POCd (Point of Control del día) con soporte para backfill histórico y actualizaciones en tiempo real.

## Arquitectura de la Solución

### 1. Fase de Backfill (00:00 UTC → Inicio del Bot)

**ContextService** (`app/context/service.py`):
- Descarga todos los trades desde 00:00 UTC hasta el momento de startup
- Calcula VWAP inicial acumulando: `sum_price_qty_base` y `sum_qty_base`
- Calcula POCd inicial acumulando volumen por precio en: `volume_by_price`
- Mantiene estado acumulativo (no resetea con cada trade)

**OrderFlowAnalyzer** (`app/strategy/analyzers/orderflow.py`):
- Después del backfill, se inicializa con el estado acumulado del ContextService
- Método `initialize_from_state()` copia los valores:
  - `sum_price_qty`: Suma acumulada de precio × cantidad
  - `sum_qty`: Suma acumulada de cantidad
  - `volume_by_price`: Dict[precio_binned, volumen]
  - `trade_count`: Número total de trades procesados

### 2. Fase Live (Desde que el Bot Arranca)

**Trade Ingestion Pipeline**:
```
WebSocket/Connector → TradeStream/HFTConnectorStream → 
  ├─> ContextService.ingest_trade()
  ├─> StrategyEngine.ingest_trade()
  └─> OrderFlowAnalyzer.ingest_trade()  ← NUEVO
```

**OrderFlowAnalyzer.ingest_trade()**:
- Actualiza estado acumulativo incrementalmente:
  ```python
  self._sum_price_qty += price * qty
  self._sum_qty += qty
  price_bin = self._bin_price(price)
  self._volume_by_price[price_bin] += qty
  ```
- Cada N trades (default 50), recalcula las métricas desde el estado acumulado
- **VWAP = sum_price_qty / sum_qty** (cálculo instantáneo)
- **POC = max(volume_by_price, key=volume)** (precio con mayor volumen)

### 3. Previous Day Levels (VALprev, VAHprev, POCprev)

**ContextService** ya implementaba esto correctamente:
- Si es 14-Nov: carga trades de 13-Nov (00:00 a 23:59) del cache
- Calcula VAH, VAL, POC del día 13 usando `_profile_from_volume()`
- Guarda como "prev" levels que NO se actualizan durante el 14
- Almacenado en: `prev_day_levels` dict

## Cambios Implementados

### 1. `OrderFlowAnalyzer` - Cálculo Incremental

**Antes**:
```python
# Mantenía buffer de TODOS los trades
self._trades_buffer: list[Dict[str, Any]] = []
# Recalculaba desde cero cada N trades
metrics = self.metrics_calculator.calculate(self._trades_buffer)
```

**Después**:
```python
# Estado acumulativo
self._sum_price_qty: float = 0.0
self._sum_qty: float = 0.0
self._volume_by_price: defaultdict[float, float] = defaultdict(float)
self._buy_volume: float = 0.0
self._sell_volume: float = 0.0

# Actualización incremental
def ingest_trade(self, trade: TradeTick) -> None:
    price = float(trade.price)
    qty = float(trade.qty)
    
    self._sum_price_qty += price * qty
    self._sum_qty += qty
    
    price_bin = self._bin_price(price)
    self._volume_by_price[price_bin] += qty
    
    if is_buyer_maker:
        self._sell_volume += qty
    else:
        self._buy_volume += qty
    
    self._trade_count += 1
    
    if self._trade_count % self.calculation_interval == 0:
        self._update_metrics()
```

**Nuevos métodos**:
- `initialize_from_backfill(trades: List[TradeTick])`: Inicializa desde lista de trades
- `initialize_from_state(...)`: Inicializa desde estado pre-calculado (usado con ContextService)
- `reset_state()`: Resetea estado al cambio de día
- `_bin_price(price)`: Agrupa precios por tick_size
- `_calculate_footprint()`: Top 20 bins de precio por volumen

### 2. `ContextService` - Integración con OrderFlowAnalyzer

**Nuevo método**:
```python
def _initialize_orderflow_analyzer(self) -> None:
    """Initialize OrderFlowAnalyzer with backfilled state."""
    analyzer = get_orderflow_analyzer()
    
    # Sincroniza tick_size
    if self.tick_size is not None:
        analyzer.tick_size = self.tick_size
        analyzer.metrics_calculator.tick_size = self.tick_size
    
    # Inicializa con estado acumulado
    analyzer.initialize_from_state(
        sum_price_qty=self.sum_price_qty_base,
        sum_qty=self.sum_qty_base,
        volume_by_price=dict(self.volume_by_price),
        buy_volume=0.0,  # Se trackea desde live trades
        sell_volume=0.0,
        trade_count=self.trade_count,
    )
```

**Llamado en**:
- `startup()`: Cuando backfill está deshabilitado o se usa HFT connector
- `_run_backfill_background()`: Después de completar el backfill
- `_roll_day()`: Resetea OrderFlowAnalyzer al cambiar de día

### 3. `TradeStream` y `HFTConnectorStream` - Forwarding a OrderFlowAnalyzer

**Agregado en ambos**:
```python
# Forward to orderflow analyzer
from app.strategy.analyzers.orderflow import get_orderflow_analyzer
try:
    analyzer = get_orderflow_analyzer()
    analyzer.ingest_trade(tick)
except Exception:
    pass  # Don't fail trade ingestion if analyzer has issues
```

### 4. `/strategy/metrics` Endpoint - Ya Existente

El endpoint ya devuelve las métricas del OrderFlowAnalyzer:
- VWAP, POC, Delta, Buy/Sell Volume, Footprint
- `backfill_complete`: Indica si el backfill terminó
- `metrics_precision`: "PRECISE" o "IMPRECISE (backfill X%)"

Frontend ya lo consume via polling.

## Flujo de Datos Completo

### Startup Sequence

1. **Bot arranca** (ej: 10:00 UTC)
2. **ContextService.startup()**:
   - Inicia backfill en background: 00:00 → 10:00 UTC
   - Acumula estado: `sum_price_qty_base`, `sum_qty_base`, `volume_by_price`
3. **Backfill completa**:
   - Llama `_initialize_orderflow_analyzer()`
   - Copia estado acumulado a OrderFlowAnalyzer
   - Marca `backfill_complete = True`
4. **Live trading**:
   - Cada trade llega via WebSocket/Connector
   - Se ingiere en ContextService Y OrderFlowAnalyzer simultáneamente
   - Ambos mantienen estado sincronizado

### Live Trade Flow

```
Trade arrives →
  ├─> ContextService.ingest_trade()
  │     ├─> Actualiza sum_price_qty_base, sum_qty_base
  │     └─> Actualiza volume_by_price
  │
  ├─> OrderFlowAnalyzer.ingest_trade()
  │     ├─> Actualiza _sum_price_qty, _sum_qty
  │     ├─> Actualiza _volume_by_price
  │     └─> Cada 50 trades: recalcula métricas
  │
  └─> StrategyEngine.ingest_trade()
        └─> Agrega a candles
```

### Frontend Polling

```
/strategy/metrics (cada 1s) →
  ├─> OrderFlowAnalyzer.get_metrics_with_metadata()
  │     └─> { vwap, poc, delta, buy_volume, sell_volume, footprint, trade_count }
  │
  └─> ContextService.get_backfill_status()
        └─> { backfill_complete, status, percentage, metrics_precision }
```

## Ventajas de la Implementación

### Performance
- **Cálculo O(1) en lugar de O(n)**: No recalcula desde cero, solo actualiza sumas
- **Sin buffer infinito**: No almacena lista completa de trades, solo estado agregado
- **Memoria constante**: Estado acumulativo tiene tamaño fijo

### Precisión
- **Backfill incluido**: VWAP y POC empiezan con datos históricos del día
- **Incremental perfecto**: VWAP(t+1) = (VWAP(t) * Vol(t) + Price(t+1) * Vol(t+1)) / (Vol(t) + Vol(t+1))
- **Consistencia**: ContextService y OrderFlowAnalyzer calculan los mismos valores

### Robustez
- **Manejo de errores**: Forwarding a OrderFlowAnalyzer no bloquea ingesta de trades
- **Reseteo automático**: Al cambiar de día, se resetea el estado
- **Tick size dinámico**: Se sincroniza desde exchange info

## Testing

### Test Manual
```bash
cd /home/engine/project/backend
source ../.venv/bin/activate
PYTHONPATH=/home/engine/project/backend python /tmp/test_incremental2.py
```

**Resultado esperado**:
```
=== Simulating Backfill ===
After backfill: VWAP=50495.00, POC=50000.00, Trades=100
Delta=0.00, Buy Vol=50.00, Sell Vol=50.00

=== Simulating Live Trades ===
After live trades: VWAP=50520.12, POC=50000.00, Trades=110
Delta=5.00, Buy Vol=55.00, Sell Vol=50.00

✅ Incremental calculation test passed!
```

### Verificación en Producción

1. **Check backfill initialization**:
   ```bash
   curl http://localhost:8000/strategy/metrics
   ```
   Debe mostrar `trade_count > 0` después del backfill.

2. **Check live updates**:
   Poll el endpoint cada segundo, verificar que `trade_count` aumenta con cada trade.

3. **Check precision status**:
   ```bash
   curl http://localhost:8000/ready
   ```
   Debe mostrar `backfill_complete: true` y `metrics_precision: "PRECISE"`.

## Archivos Modificados

1. **`backend/app/strategy/analyzers/orderflow.py`**:
   - Reemplazó buffer de trades con estado acumulativo
   - Agregó métodos de inicialización y reset
   - Implementó cálculo incremental de VWAP y POC

2. **`backend/app/context/service.py`**:
   - Agregó `_initialize_orderflow_analyzer()`
   - Llama inicialización después del backfill
   - Resetea analyzer en `_roll_day()`

3. **`backend/app/ws/trades.py`**:
   - Agregó forwarding a OrderFlowAnalyzer en `handle_payload()`

4. **`backend/app/data_sources/hft_connector.py`**:
   - Agregó forwarding a OrderFlowAnalyzer en `_handle_trade_event()`

## Próximos Pasos (Opcional)

1. **Buy/Sell Volume por Precio**: Trackear buy_vol y sell_vol por cada price bin en footprint
2. **Métricas adicionales**: CVD (Cumulative Volume Delta) por precio
3. **Websocket Push**: Enviar actualizaciones push en lugar de polling (Socket.IO)
4. **Histórico intraday**: Guardar snapshots cada N minutos para replay

## Conclusión

El sistema ahora calcula VWAP y POCd de forma incremental y eficiente:
- ✅ Backfill inicial con todos los trades del día
- ✅ Actualización incremental con cada nuevo trade live
- ✅ Estado acumulativo sincronizado entre ContextService y OrderFlowAnalyzer
- ✅ Frontend recibe actualizaciones en tiempo real via `/strategy/metrics`
- ✅ Previous day levels (VAHprev, VALprev, POCprev) cargados correctamente
