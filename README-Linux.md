# Linux - ARCH 
## PermissionError: [Errno 13] Permission denied: '/dev/ttyUSB0`

Este error ocurre cuando el usuario no tiene permisos para acceder al puerto serie USB. En sistemas Linux, los puertos seriales están protegidos y solo usuarios en ciertos grupos pueden accederlos. En Arch Linux, los grupos relevantes son `uucp` (Unix-to-Unix CoPy) para acceso general a puertos serie y `lock` para el control de bloqueos.

### Solución de permisos
Arch
```bash 
sudo usermod -aG uucp,lock $USER
```
Verificar
```bash
id -nG $USER
```
output
```bash 
tehe lock wheel uucp docker microk8s
```



### Error 

```bash 
PROJECT_ROOT=/home/tehe/Work/tfm/mlops4rtedge
PHASES=f07..f07
[TIMING] /home/tehe/Work/tfm/mlops4rtedge/test/timing/run_1774467799.csv
Cleaning variants from f07 down to f07...
- remove7-all
[INFO] Using local Python interpreter: python3.11
[INFO] Using Python interpreter in venv: .venv/bin/python3
make remove-phase-all PHASE=f07_modval VARIANTS_DIR=executions/f07_modval
make[1]: Entering directory '/home/tehe/Work/tfm/mlops4rtedge'
[INFO] Using local Python interpreter: python3.11
[INFO] Using Python interpreter in venv: .venv/bin/python3
==> Removing ALL variants of phase f07_modval (SAFE mode: only if no children dependencies)
[INFO] executions/f07_modval does not exist. Nothing to delete.
ls: cannot access 'executions/f07_modval': No such file or directory
[OK] Phase f07_modval completely removed (SAFE mode: only if no children dependencies)
make[1]: Leaving directory '/home/tehe/Work/tfm/mlops4rtedge'
Skipping setup because start phase is f07 (not full run from f01).
[TEST 7.0]
[INFO] Using local Python interpreter: python3.11
[INFO] Using Python interpreter in venv: .venv/bin/python3
[INFO] MTI_MS=100 ms
[INFO] PLATFORM=esp32
[INFO] TIME_SCALE=0.01
[INFO] ITMAX not provided -> preparebuild will use ITmax=MTI_MS
[INFO] MAX_ROWS not provided -> preparebuild will use full dataset
make[1]: Entering directory '/home/tehe/Work/tfm/mlops4rtedge'
[INFO] Using local Python interpreter: python3.11
[INFO] Using Python interpreter in venv: .venv/bin/python3
==> Creating variant f07_modval:v701
[OK] Variante creada: f07_modval:v701
==> Variant created: f07_modval:v701
make[1]: Leaving directory '/home/tehe/Work/tfm/mlops4rtedge'
[INFO] Using local Python interpreter: python3.11
[INFO] Using Python interpreter in venv: .venv/bin/python3
[F07] preparebuild OK — v701
[F07] Platform: esp32
[F07] Model size: 80560 bytes
[F07] Arena estimated: 120840 bytes
[F07] Operators: 9
[F07] Models configured: 1
[F07] Input bytes: 4
[F07] Output bytes: 1
[F07] post_wait_s = 5.00s
[F07] Puerto autodetectado: /dev/ttyUSB0

=== BUILD ===
[F07] sdkconfig regenerado desde sdkconfig.defaults para build Docker
[F07] Docker memory limit por defecto: 16596598784

=== FLASH ===
[F07] Flash vía Docker

=== RUN ===
[F07-serial] Puerto: /dev/ttyUSB0
[F07-serial] Baud: 115200
[F07-serial] Periodo envío: 0.100s
[F07-serial] Líneas a enviar: 20146
[F07-serial] Drenado final: 5.00s
[F07-serial] Progreso: '*' cada 100 líneas enviadas (10 '*' por línea)
Traceback (most recent call last):
  File "/home/tehe/Work/tfm/mlops4rtedge/.venv/lib/python3.11/site-packages/serial/serialposix.py", line 322, in open
    self.fd = os.open(self.portstr, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 13] Permission denied: '/dev/ttyUSB0
```


## Error de bus en flash (Antes)
> Pasaba antes, pero ya no (no se por que :D)

Este error ocurre cuando la velocidad de comunicación (baudrate) es incompatible entre el host y la ESP32, causando pérdida de datos o desincronización en la transmisión serie. Al establecer una velocidad fija de 460800 bps tanto en Docker como en el host, se garantiza que ambos extremos de la comunicación operan sincronizados. Esta limitación es especialmente crítica después del flasheo, cuando el sistema intenta enviar datos inmediatamente sin esperar a la estabilización del puerto.

### Solución
scripts/phases/f072_flashrun.py line 259
```bash 
  if docker_ok:
      print("[F07] Flash vía Docker")
      run_idf_and_log(
          ["-p", port, "-b", "460800", "flash"],
          flash_log,
          esp_project_dir=esp_project_dir,
          port=port,
          docker_memory_limit=docker_memory_limit,
          docker_memory_swap=docker_memory_swap,
          docker_cpus=docker_cpus,
      )
      return
``` 