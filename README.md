# MLOps4RT-Edge

![Python 3.11](https://img.shields.io/badge/python-3.11-blue)
![GNU Make](https://img.shields.io/badge/build-GNU%20Make-informational)
![Docker](https://img.shields.io/badge/docker-F05%2FF06%2Fedge-orange)
![Traceability](https://img.shields.io/badge/traceability-schema%20driven-success)

Pipeline MLOps por fases para transformar series temporales en modelos cuantizados y validarlos en edge. El repositorio contiene codigo, automatizacion, schemas y plantillas; los datos, ejecuciones, modelos, logs, caches DVC y estado de MLflow son artefactos locales del proyecto.

La unidad de trabajo es la **variante**. Cada fase crea una carpeta trazable en:

```text
executions/<fase>/<variante>/
```

Ejemplo:

```text
executions/f05_modeling/v5_0001/
```

## Que Hace

El flujo completo tiene ocho fases encadenadas:

| Fase | Carpeta | Objetivo | Parent | Salida principal |
| --- | --- | --- | --- | --- |
| F01 | `f01_explore` | explorar, limpiar y perfilar datos brutos | ninguno | dataset limpio y columnas de medida |
| F02 | `f02_events` | convertir una senal en eventos | F01 | eventos y catalogo |
| F03 | `f03_windows` | construir ventanas temporales | F02 | dataset de ventanas |
| F04 | `f04_targets` | etiquetar ventanas con targets predictivos | F03 | dataset supervisado |
| F05 | `f05_modeling` | entrenar modelos | F04 | modelo, metricas y dataset etiquetado |
| F06 | `f06_quant` | cuantizar y empaquetar para edge | F05 | TFLite/EEDU y compatibilidad edge |
| F07 | `f07_modval` | validar un modelo en edge | F06 | metricas runtime de un modelo |
| F08 | `f08_sysval` | validar un sistema multimodelo | F07(s) | seleccion/configuracion y metricas globales |

El contrato formal de parametros, artefactos, exports y metricas vive en [`scripts/traceability_schema.yaml`](scripts/traceability_schema.yaml). El `Makefile` traduce comandos comodos (`RAW`, `PARENT`, `MODEL_FAMILY`, etc.) a los nombres schema-native que se congelan en `params.yaml`.

## Requisitos

Minimos:

- Python 3.11
- GNU Make
- Git

Recomendados:

- Docker para F05, F06 y flujos ESP32 reproducibles.
- DVC para artefactos pesados.
- MLflow si se activa tracking de entrenamiento.
- Toolchain, placa o entorno virtual ESP32 para F07/F08.

En Windows se recomienda ejecutar `make` desde Git Bash o desde un entorno que soporte las recetas POSIX del Makefile.

## Setup

Modo local:

```bash
make setup SETUP_CFG=setup/local.yaml
make check-setup
```

Modo remoto:

```bash
cp setup/remote.yaml .mlops4ofp.remote.yaml
# editar endpoints Git, DVC y MLflow
make setup SETUP_CFG=.mlops4ofp.remote.yaml
make check-setup
```

Ver ayuda:

```bash
make help
```

Limpiar setup local:

```bash
make clean-setup
```

## Patron De Uso

Cada fase sigue el mismo ciclo:

```bash
make variantN ...
make scriptN VARIANT=vN_XXXX
make checkN VARIANT=vN_XXXX
make registerN VARIANT=vN_XXXX
```

`variantN` crea la carpeta y escribe `params.yaml`. Si la variante ya existe, falla para evitar sobrescrituras accidentales. Para reejecutar una variante existente usa `scriptN`, no `variantN`.

Los IDs cortos se normalizan por fase. Por ejemplo, `VARIANT=v701` en F07 se normaliza a `v7_0701` segun las reglas de `params_manager.py`; el formato canonico del schema es `v<fase>_<4 digitos>`.

## Trazabilidad

Cada variante contiene, como minimo:

- `params.yaml`: snapshot de fase, variante, parent, parametros resueltos y hashes de parents.
- `metadata.yaml`: estado de ciclo de vida, verificacion y registro.
- `outputs.yaml`: artefactos, exports y metricas producidas por la ejecucion.

El schema define:

- parametros requeridos, heredados y por defecto;
- regex de parent por fase, por ejemplo F05 requiere parent `v4_XXXX`;
- artefactos esperados y sus extensiones;
- exports propagables aguas abajo;
- metricas obligatorias u opcionales.

El pipeline calcula `parent_hashes` sobre `outputs.yaml` del parent. Si un parent cambia despues de crear una variante hija, la auditoria puede detectarlo como ruptura de trazabilidad.

Comandos utiles:

```bash
make checkN VARIANT=vN_XXXX
make generate_lineage
```

El dashboard de linaje se genera en:

```text
executions/pipeline_lineage.html
```

## Ejecucion Completa Por Fases

### F01: Exploracion y limpieza

Parametros schema principales: `raw_path`, `cleaning`, `nan_values`, `error_values`, `first_line`, `max_lines`.

```bash
make variant1 VARIANT=v1_0000 RAW=data/raw.csv CLEANING=basic NAN_VALUES='[-999999]'
make script1 VARIANT=v1_0000
make check1 VARIANT=v1_0000
make register1 VARIANT=v1_0000
```

Limpieza profunda con valores erroneos por columna:

```bash
make variant1 VARIANT=v1_0001 RAW=data/raw.csv CLEANING=basic NAN_VALUES='[-999999]' ERROR_VALUES='{"MG-LV-MSB_AC_Voltage":[0.0],"Receiving_Point_AC_Voltage":[0.0],"Island_mode_MCCB_AC_Voltage":[0.0],"Island_mode_MCCB_Frequency":[-327.679993,0.0],"MG-LV-MSB_Frequency":[-327.679993,0.0],"Outlet_Temperature":[-52.5],"Inlet_Temperature_of_Chilled_Water":[-52.5,-52.400002,-52.299999]}'
make script1 VARIANT=v1_0001
make check1 VARIANT=v1_0001
make register1 VARIANT=v1_0001
```

F01 exporta `Tu`, `n_rows`, `n_columns` y `measure_cols`. F02 valida `MEASURE` contra `exports.measure_cols`.

### F02: Eventos

Parametros schema principales: `parent_variant`, `Tu`, `measure_name`, `strategy`, `bands`, `nan_mode`.

```bash
make variant2 VARIANT=v2_0001 PARENT=v1_0000 MEASURE=Battery_Active_Power STRATEGY=transitions BANDS='[10,20,30,40,50,60,70,80,90]' NAN_MODE=discard
make script2 VARIANT=v2_0001
make check2 VARIANT=v2_0001
make register2 VARIANT=v2_0001
```

F02 es univariante: cada variante selecciona una sola medida con `MEASURE`. Si quieres comparar varias senales, crea una variante F02 por medida.

Exports relevantes: `measure_name`, `event_strategy`, `bands`, `event_types`, `n_event_types`, `n_events`, `n_types`.

### F03: Ventanas

Parametros schema principales: `parent_variant`, `Tu`, `measure_name`, `OW`, `LT`, `PW`, `window_strategy`, `nan_mode`.

```bash
make variant3 VARIANT=v3_0001 PARENT=v2_0001 OW=600 LT=100 PW=100 STRATEGY=synchro NAN_MODE=discard
make script3 VARIANT=v3_0001
make check3 VARIANT=v3_0001
make register3 VARIANT=v3_0001
```

Estrategias permitidas: `synchro`, `asynOW`, `withinPW`, `asynPW`.

Exports relevantes: `Tu`, `OW`, `LT`, `PW`, `event_type_count`, `window_strategy`, `nan_mode`, `measure_name`, `n_windows`.

### F04: Targets

Parametros schema principales: `parent_variant`, `prediction_name`, `target_operator`, `target_event_types`.

```bash
make variant4 VARIANT=v4_0001 PARENT=v3_0001 NAME=battery_active_power_high_90 OPERATOR=OR EVENTS='["Battery_Active_Power_80_90-to-90_100"]'
make script4 VARIANT=v4_0001
make check4 VARIANT=v4_0001
make register4 VARIANT=v4_0001
```

`NAME` debe cumplir el regex del schema: minusculas, numeros y guion bajo (`^[a-z0-9_]+$`). El operador soportado en el schema actual es `OR`.

Exports relevantes: geometria heredada (`Tu`, `OW`, `LT`, `PW`), `prediction_name`, `target_operator`, `target_event_types`, `n_windows_pos`, `n_windows_neg`.

### F05: Modelado

Parametros schema principales: `parent_variant`, `model_family`, `automl`, `search_space`, `training`, `deduplication_mode`, `seed`, `evaluation`, `imbalance_strategy`, `imbalance_max_majority_samples`.

```bash
make variant5 VARIANT=v5_0001 PARENT=v4_0001 MODEL_FAMILY=cnn1d IMBALANCE_STRATEGY=rare_events IMBALANCE_MAX_MAJ=20000 SEED=42
make script5 VARIANT=v5_0001
make check5 VARIANT=v5_0001
make register5 VARIANT=v5_0001
```

Familias permitidas: `dense_bow`, `sequence_embedding`, `cnn1d`.

F05 corre en Docker. Para GPU:

```bash
make script5 VARIANT=v5_0001 F56_GPU=true
```

Overrides utiles:

```bash
make variant5 VARIANT=v5_0002 PARENT=v4_0001 MODEL_FAMILY=cnn1d TRAINING='{"epochs":20,"max_samples":50000}' AUTOML='{"enabled":true,"max_trials":8,"seed":42}' EVALUATION='{"split":{"train":0.7,"val":0.15,"test":0.15}}'
```

Exports relevantes: `models`, `prediction_name`, `model_family`, `decision_threshold`, `best_val_recall`, `test_precision`, `test_recall`, `test_f1`.

### F06: Cuantizacion y empaquetado edge

Parametros schema principales: `parent_variant`, geometria heredada, `deployment`, `quantization`, `thresholding`, `eedu`.

```bash
make variant6 VARIANT=v6_0001 PARENT=v5_0001 DEPLOY_TARGET=esp32 REQUIRE_INT8=true MEMORY_LIMIT=327680
make script6 VARIANT=v6_0001
make check6 VARIANT=v6_0001
make register6 VARIANT=v6_0001
```

F06 tambien corre en Docker. Para GPU:

```bash
make script6 VARIANT=v6_0001 F56_GPU=true
```

Ejemplo con dicts schema-native:

```bash
make variant6 VARIANT=v6_0002 PARENT=v5_0001 QUANTIZATION='{"calibration_samples":512,"symmetric_int8":true,"per_channel":true}' THRESHOLDING='{"strategy":"recalibrate_on_quantized","maximize_metric":"recall","grid_points":101}' EEDU='{"version":"1.0","layout":"default"}'
```

Exports relevantes: `edge_capable`, `incompatibility_reason`, `operators`, `model_size_bytes`, `arena_bytes`, `decision_threshold`, metricas float vs quantized.

### F07: Validacion de un modelo en edge

Parametros schema principales: `parent_variant`, `time_scale_factor`, geometria heredada, `MTI_MS`, `ITmax`, `max_rows`, `serial_max_lines`, `esp_flash_size_mb`, `platform`, `virtual`.

ESP32 virtual:

```bash
make variant7 VARIANT=v7_0001 PARENT=v6_0001 PLATFORM=esp32 MTI_MS=100 TIME_SCALE=0.01 VIRTUAL=true MAX_ROWS=10000 ESP_FLASH_MB=4
make script7 VARIANT=v7_0001
make check7 VARIANT=v7_0001
make register7 VARIANT=v7_0001
```

Placa fisica:

```bash
make variant7 VARIANT=v7_0002 PARENT=v6_0001 PLATFORM=esp32 MTI_MS=100 TIME_SCALE=0.01 VIRTUAL=false MAX_ROWS=10000 ESP_FLASH_MB=4
make script7 VARIANT=v7_0002 PORT=/dev/ttyUSB0 BAUD=115200
make check7 VARIANT=v7_0002
make register7 VARIANT=v7_0002
```

Ejecucion paso a paso:

```bash
make script7-prepare-build VARIANT=v7_0001
make script7-build-only VARIANT=v7_0001
make script7-flash-run VARIANT=v7_0001
make script7-post VARIANT=v7_0001
```

Opciones utiles:

- `MTI_MS`: presupuesto temporal de inferencia en milisegundos.
- `TIME_SCALE`: escala temporal usada para reproducir ventanas en edge.
- `MAX_ROWS`: limita filas incluidas en el dataset generado.
- `MAX_LINES`: limita lineas enviadas por serial sin recortar el dataset generado.
- `ESP_FLASH_MB`: tamano de flash declarado para ESP-IDF/QEMU.
- `F07_FORCE_REBUILD=true`: fuerza recompilacion aunque exista build previo.

Exports relevantes: `model_id`, `platform`, `runtime_model_name`, `operators`, `arena_bytes`, `model_memory_bytes`, `edge_capable`, `quality_score`, `ok_rate`, `offload_rate`, `watchdog_rate`, `edge_run_completed`.

### F08: Validacion multimodelo

Parametros schema principales: `parent_variant` en modo lista, `selection_mode`, `objective`, `solver_time_limit_sec`, `time_scale_factor`, `MTI_MS`, `max_rows`, `platform`, `memory_budget_bytes`, `max_models`, `min_quality_score`, `min_precision`, `min_recall`.

Seleccion manual:

```bash
make variant8 VARIANT=v8_0001 PARENTS=v7_0001,v7_0002 PLATFORM=esp32 MTI_MS=100 SELECTION_MODE=manual
make script8 VARIANT=v8_0001 PORT=/dev/ttyUSB0 BAUD=115200
make check8 VARIANT=v8_0001
make register8 VARIANT=v8_0001
```

Seleccion automatica por ILP:

```bash
make variant8 VARIANT=v8_0002 PARENTS=v7_0001,v7_0002 PLATFORM=esp32 MTI_MS=100 SELECTION_MODE=auto_ilp OBJECTIVE=max_global_recall MEMORY_BUDGET_BYTES=327680 MAX_MODELS=2 MIN_QUALITY_SCORE=0.7
make script8 VARIANT=v8_0002
make check8 VARIANT=v8_0002
make register8 VARIANT=v8_0002
```

Ejecucion paso a paso:

```bash
make script8-select-config VARIANT=v8_0001
make script8-prepare-build VARIANT=v8_0001
make script8-build-only VARIANT=v8_0001
make script8-flash-run VARIANT=v8_0001 PORT=/dev/ttyUSB0
make script8-post VARIANT=v8_0001
```

Exports relevantes: `selected_variants`, `model_ids`, `operators_union`, `compatible_input_signature`, `configuration_edge_capable`, `selection_global_precision`, `selection_global_recall`, `system_viable`.

## ESP32 Virtual

F07 soporta `VIRTUAL=true` como parametro trazable. En ese modo, `make script7` usa el entorno virtual basado en Docker, QEMU y `socat`.

Comandos utiles:

```bash
make esp32-virt-verify
make script7-virtualESP32 VARIANT=v7_0001
make esp32-virt-stop
```

El host solo necesita Docker. ESP-IDF, QEMU, `socat` y dependencias Python viven dentro del contenedor.

## ESP32 Fisica

Para placa real:

1. Crear variante F07 con `VIRTUAL=false`.
2. Conectar la placa.
3. Ejecutar `make script7 VARIANT=... PORT=...`.
4. Revisar logs y metricas dentro de la carpeta de variante.

Ejemplo:

```bash
make script7 VARIANT=v7_0002 PORT=/dev/ttyUSB0 BAUD=115200
```

En Windows el puerto suele tener forma `COM3`, `COM4`, etc.

## Artefactos

El pipeline genera:

- `params.yaml`: parametros resueltos y linaje.
- `metadata.yaml`: estado de ciclo de vida.
- `outputs.yaml`: contrato de artefactos, exports y metricas.
- datasets intermedios `.parquet` o `.csv`.
- reportes `.html`.
- modelos `.h5` y `.tflite`.
- logs de build, flash y monitor en fases edge.
- perfiles `07_model_profile.yaml`, `08_system_profile.yaml` y configuraciones efectivas.

Estos archivos viven bajo `executions/`. No son codigo fuente.

## DVC, MLflow Y Git

Responsabilidades:

- Git: codigo, documentacion, schemas, Makefile y plantillas.
- DVC: artefactos pesados generados por fases.
- MLflow: tracking de entrenamientos F05 si esta habilitado.
- `executions/`: estado local de variantes y resultados.

Traer artefactos registrados:

```bash
make dvc-pull VARIANT=v5_0001
make dvc-pull VARIANT=v2_0001,v5_0001,v7_0001
make dvc-pull VARIANT=v7
```

Limpiar artefactos descargados:

```bash
make dvc-clean VARIANT=v5_0001
```

## Limpieza

Eliminar una variante, solo si no tiene hijos dependientes:

```bash
make remove5 VARIANT=v5_0001
```

Eliminar todas las variantes de una fase en modo seguro:

```bash
make remove5-all
```

Resetear setup local:

```bash
make clean-setup
```

## Troubleshooting

### `La carpeta ya existe`

`make variantN` crea variantes nuevas. Si ya existe `executions/<fase>/<variante>`, usa otra variante o ejecuta la existente:

```bash
make script7 VARIANT=v7_0000
```

### Una fase no encuentra el parent

Comprueba que el parent existe y pertenece a la fase correcta:

```text
F02 -> parent F01: v1_XXXX
F03 -> parent F02: v2_XXXX
F04 -> parent F03: v3_XXXX
F05 -> parent F04: v4_XXXX
F06 -> parent F05: v5_XXXX
F07 -> parent F06: v6_XXXX
F08 -> parents F07: v7_XXXX
```

### `Parent outputs.yaml modified`

La variante hija guarda hashes del `outputs.yaml` del parent. Si el parent se regenero despues de crear la hija, crea una nueva variante hija o restaura los artefactos coherentes.

### F02 rechaza `MEASURE`

F02 valida `MEASURE` contra `executions/f01_explore/<PARENT>/outputs.yaml` en `exports.measure_cols`. Ejecuta y registra/comprueba F01 antes de crear F02.

### F05/F06 falla en Docker

Comprueba:

- Docker arrancado.
- imagen construible.
- espacio en disco.
- `F56_GPU=true` solo si tienes GPU y runtime NVIDIA configurado.

### F07/F08 no flashea

Comprueba:

- `PORT` y `BAUD`.
- permisos del puerto serie.
- placa en modo correcto.
- logs `07_esp_flash_log.txt`, `07_esp_monitor_log.txt`, `08_esp_flash_log.txt` o `08_esp_monitor_log.txt`.

### Firmware ESP32 demasiado grande

Declara una flash mayor si tu placa o QEMU la soporta:

```bash
make variant7 VARIANT=v7_0003 PARENT=v6_0001 PLATFORM=esp32 MTI_MS=100 ESP_FLASH_MB=4
```

Si la placa solo tiene 2 MB, reduce modelo, operadores o trazas, o cambia de hardware.

### Inferencias edge incompletas

Revisa:

- `metrics_models.csv`
- `metrics_inference_records.csv`, si existe.
- logs de monitor.
- `phase_status_reason` en `outputs.yaml`.

Indicadores comunes:

- `wd_late`: inferencia fuera de deadline.
- `urgent`: fallback por deadline.
- `inference_incomplete`: inicio de inferencia sin fin registrado.
- `no_successful_inferences`: no hubo inferencias validas.

## Estructura Del Repositorio

```text
scripts/core/              logica comun, parametros, trazabilidad y artefactos
scripts/phases/            implementacion de fases F01-F08
scripts/runtime_analysis/  parser y metricas runtime edge
scripts/esp32_virtual/     soporte Docker/QEMU/socat para ESP32 virtual
scripts/docker/            imagenes reproducibles F05/F06
edge/                      plantillas y runners edge
setup/                     configuracion local/remota
test/                      auditorias, comparativas y experimentos
executions/                salidas locales generadas
```

## Para Desarrolladores

Lee [`DEVELOPERS.md`](DEVELOPERS.md).

Antes de publicar cambios:

- no subir datos privados;
- no subir `.env`;
- no subir caches DVC/MLflow;
- revisar que `executions/` no contenga artefactos pesados no deseados;
- actualizar este README si cambian comandos, parametros o contratos del schema.
