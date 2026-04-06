## Scope Of This Repository

This repository is the codebase of the pipeline and should contain code only.

- Do version in Git: source code, configuration templates, documentation, tests, and project setup files.
- Do not version in Git: local DVC state, DVC cache, MLflow local state, execution outputs, generated artifacts, or per-project DVC references produced under executions/.

## Separation Between Pipeline Repo And User Projects

For projects that use the pipeline:

- The Git repository configured in the project setup is the place where that project stores its own code and, if desired, its DVC references.
- Binary artifacts must go to the DVC backend configured by that project setup, which may be local or remote.

For this repository mlops4rtedge:

- Keep only the reusable pipeline code.
- Do not publish .dvc, .dvc_storage, mlruns, or executions/ contents here.

powershell.exe -NoProfile -Command "[System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object"


bash build_image.sh (construir imagen en docker)


añadidos: ensure_docker_image_exists  en mlops4rtedge\scripts\phases\f072_flashrun.py
Ver si tiene que detectar sistema operativo para arm.


auto_detect_port actualizado para que funcione en windows y linux + describe_serial_ports (para prints)