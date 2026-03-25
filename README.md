powershell.exe -NoProfile -Command "[System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object"


bash build_image.sh (construir imagen en docker)


añadidos: ensure_docker_image_exists  en mlops4rtedge\scripts\phases\f072_flashrun.py
Ver si tiene que detectar sistema operativo para arm.


auto_detect_port actualizado para que funcione en windows y linux + describe_serial_ports (para prints)