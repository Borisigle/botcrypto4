# Solución: HFT Connector Bybit - Desconexiones y Reconexión Automática

## Problema Resuelto ✅

El HFT Connector con Bybit se desconectaba y no volvía a conectar automáticamente:
- **Status:** "Disconnected" / "Down"
- **Last update:** Congelado (ejemplo: 21:54:21 UTC)
- **Backfill:** Completaba OK (132/132 chunks)
- **Live data:** No fluía después de la conexión inicial

## Causas Identificadas 🔍

1. **Falta de reconexión automática**: Cuando el subproceso moría, solo se detectaba pero no se intentaba reconectar
2. **Conexión "estancada" no detectada**: Si la conexión parecía viva pero dejaba de recibir eventos, no se detectaba
3. **Logs de error perdidos**: No se monitoreaba stderr del subproceso, perdiendo mensajes importantes de error
4. **Limpieza incompleta**: Al reconectar, el proceso viejo no se limpiaba correctamente

## Solución Implementada 🛠️

### 1. Monitoreo Mejorado del Subproceso

**Nuevo monitoreo de stderr:**
- Captura todos los errores del subproceso hftbacktest
- Logs estructurados: `bybit_connector_subprocess_stderr`
- Ayuda a diagnosticar problemas de conexión de la librería

**Detección de salida del proceso:**
- Registra códigos de salida cuando el subproceso termina
- Logs: `bybit_connector_subprocess_exited` y `bybit_connector_subprocess_terminated`

### 2. Detección de Conexión Estancada

**Nuevo sistema de detección:**
```python
_stale_connection_seconds = 60  # Considera estancada si no hay eventos por 60s
```

**Health check loop mejorado:**
- Verifica cada 5 segundos si el proceso está vivo
- Detecta conexiones "estancadas" (parece conectada pero no recibe datos)
- Espera 30 segundos después de conectar para evitar falsos positivos
- Registra estado de salud cada 60 segundos

**Cuando detecta conexión estancada:**
1. Log: `bybit_connector_stale_connection_detected`
2. Marca `_connected = False`
3. `is_connected()` devuelve False
4. `HFTConnectorStream` detecta desconexión
5. Se inicia reconexión automática

### 3. Reconexión Automática Mejorada

**Método `connect()` mejorado:**
- Limpia runner antiguo antes de crear uno nuevo
- Reinicia estado de suscripciones (trades/depth)
- Cancela task de health check antiguo
- Mejor manejo de errores

**Secuencia de reconexión:**
1. Detiene runner antiguo si existe
2. Crea nuevo runner
3. Inicia subproceso nuevo
4. Reinicia flags de suscripción
5. Re-suscribe a canales (manejado por HFTConnectorStream)
6. Inicia nuevo loop de health check

### 4. Script de Subproceso Mejorado

**Logging comprehensivo a stderr:**
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stderr
)
```

**Logs incluyen:**
- Progreso de inicialización de conexión
- Confirmación de suscripciones
- Estadísticas de procesamiento cada 60s
- Todos los errores con contexto completo

**Mejor manejo de errores:**
- Continúa funcionando en errores no fatales
- No sale del loop por error en un solo evento
- Cleanup graceful en finally block

## Logs Nuevos para Monitoreo 📊

### Logs estructurados nuevos:

1. **bybit_connector_subprocess_connected** - Subproceso conectado OK
2. **bybit_connector_subprocess_stderr** - Errores del subproceso
3. **bybit_connector_process_died** - Proceso murió (detectado por health check)
4. **bybit_connector_stale_connection_detected** - Sin eventos por >60s
5. **bybit_connector_health_check** - Estado periódico cada 60s
   - `process_alive`: bool
   - `queue_size`: int
   - `error_count`: int
   - `seconds_since_last_event`: float

### Ejemplo de logs esperados:

```json
{"event": "bybit_connector_subprocess_connected"}
{"event": "bybit_connector_connected", "symbol": "BTCUSDT"}

// Cada 60 segundos:
{
  "event": "bybit_connector_health_check",
  "process_alive": true,
  "queue_size": 0,
  "error_count": 0,
  "seconds_since_last_event": 2.3
}

// Si hay desconexión:
{
  "event": "bybit_connector_stale_connection_detected",
  "seconds_since_last_event": 62.4,
  "stale_threshold": 60
}

// Reconexión automática:
{"event": "connector_disconnected", "connector": "hft"}
{"event": "connector_connection_error", "attempt": 1, "retry_delay": 0.5}
{"event": "bybit_connector_connected", "symbol": "BTCUSDT"}
```

## Comportamiento Esperado 🎯

### Operación Normal:
- Conector inicia y conecta correctamente
- Recibe trades y depth en tiempo real
- Logs de health check cada 60s muestran todo OK
- `seconds_since_last_event` < 5s durante trading activo

### Muerte del Proceso:
1. Detectado en <5 segundos por health check
2. Log: `bybit_connector_process_died`
3. `is_connected()` devuelve False
4. HFTConnectorStream inicia reconexión automática
5. Nuevo subproceso arranca
6. Re-suscribe a canales
7. Datos fluyen de nuevo

### Conexión Estancada:
1. Pasan 60 segundos sin eventos
2. Log: `bybit_connector_stale_connection_detected`
3. Marca conexión como desconectada
4. Reconexión automática se inicia
5. Nueva conexión establecida

### Errores de Librería:
- Capturados desde stderr
- Logeados como: `bybit_connector_subprocess_stderr`
- Visibles en logs de aplicación
- Ayudan a diagnosticar problemas

## Configuración ⚙️

No se necesitan cambios de configuración. Usa valores sensatos por defecto:

- **Threshold de conexión estancada:** 60 segundos
- **Intervalo de health check:** 5 segundos  
- **Intervalo de log de health:** 60 segundos
- **Periodo de gracia al inicio:** 30 segundos (antes de chequear estancamiento)

## Monitoreo Recomendado 📈

### Métricas clave en logs:

1. **seconds_since_last_event**
   - Debe ser <5s durante trading activo
   - Si >60s, se activa reconexión

2. **error_count**
   - Debe ser 0 o muy bajo
   - Si aumenta, revisar logs de stderr

3. **queue_size**
   - Debe mantenerse razonable (<100)
   - Si crece, puede indicar problema de procesamiento

4. **Intentos de reconexión**
   - Deberían ser raros con red estable
   - Si son frecuentes, investigar causa raíz

## Pruebas Realizadas ✅

- ✅ 27 tests de test_bybit_connector.py pasando
- ✅ 26 tests de test_hft_connector.py pasando
- ✅ Sintaxis Python validada
- ✅ Instanciación de clases verificada
- ✅ Generación de script verificada

## Archivos Modificados 📝

- `backend/app/data_sources/bybit_connector.py`
  - Clase `BybitConnectorRunner` mejorada
  - Clase `BybitConnector` mejorada
  - Script de subproceso mejorado
  - Detección de conexión estancada
  - Logging mejorado en todas partes

## Próximos Pasos 🚀

1. **Deploy:** Los cambios están listos para producción
2. **Monitoreo:** Observar logs durante las primeras horas
3. **Verificación:** Confirmar que `Last update` se actualiza continuamente
4. **Alertas:** Configurar alertas si `error_count` > 10 o reconexiones frecuentes

## Notas Adicionales 📌

- La reconexión es **automática** - no requiere intervención manual
- Los logs de stderr del subproceso ayudarán a diagnosticar problemas de la librería hftbacktest
- El sistema es robusto ante desconexiones temporales de red
- La detección de 60s evita falsos positivos en períodos de baja actividad

---

**¡El conector ahora mantendrá conexión estable y se reconectará automáticamente ante cualquier problema!** 🎉
