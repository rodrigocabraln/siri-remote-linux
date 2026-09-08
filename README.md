# siri-remote-linux

<p align="center">
  <font color="#7C3AED">
    <strong>PROYECTO PERSONAL, CODESARROLLADO CON IA</strong><br>
    Este proyecto se creó para un HTPC de uso personal. No es un producto
    oficial de Apple y no ofrece soporte comercial.
  </font>
</p>

`siri-remote-linux` permite usar un Apple Siri Remote A2540 como teclado virtual
en Linux. Convierte el aro, la superficie táctil y los botones del mando en
eventos de teclado para controlar la aplicación que tenga el foco.

Por ahora, el A2540 es el único modelo compatible. El perfil se desarrolló y
probó con un mando físico en Fedora. También se incluyen instrucciones de
instalación para Ubuntu, Debian y Arch, aunque el funcionamiento con hardware
real todavía no se ha validado en esas distribuciones.

## Instalación e inicio rápido

Ejecuta los comandos desde la carpeta del proyecto y utiliza la versión de
Python instalada por el sistema.

### 1. Instalar dependencias

Elige el comando correspondiente a tu distribución:

```bash
# Fedora
sudo dnf install bluez python3-dbus python3-gobject python3-evdev

# Ubuntu o Debian
sudo apt install bluez python3-dbus python3-gi python3-evdev

# Arch Linux
sudo pacman -S bluez bluez-utils python-dbus python-gobject python-evdev
```

### 2. Habilitar Bluetooth y uinput

```bash
sudo systemctl enable --now bluetooth.service
sudo modprobe uinput
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
sudo groupadd -f input
sudo usermod -aG input "$USER"
sudo install -m 0644 udev/99-siri-remote-uinput.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --name-match=uinput
```

Cierra la sesión y vuelve a iniciarla para aplicar el cambio de grupo. El
programa se ejecuta con tu usuario y utiliza `/dev/uinput` para crear el teclado
virtual.

### 3. Seleccionar o emparejar el mando

```bash
./siri-remote setup
```

El asistente muestra los mandos compatibles que ya están emparejados. Si hay
varios, podrás elegir cuál utilizar. Si no encuentra ninguno, iniciará una
búsqueda. Acerca el mando al equipo y mantén pulsados Atrás + Volumen arriba
durante unos cinco segundos.

Cuando se establezca la conexión, pulsa un botón cuando el asistente lo indique.
En cuanto reciba un evento, `setup` guardará la identidad del mando y la
configuración inicial en `config.env`. El programa controla un solo mando a la
vez.

### 4. Probar y ejecutar

Comienza con una prueba de 30 segundos:

```bash
./siri-remote run --listen-seconds 30
```

Pon en primer plano la aplicación que quieras controlar y prueba el aro, Centro,
Atrás y los gestos de deslizamiento. Para mantener el programa en ejecución
hasta pulsar Ctrl+C, utiliza:

```bash
./siri-remote run
```

Si el mando está en reposo, pulsa Atrás para activarlo. Para ver las acciones
sin enviarlas al escritorio:

```bash
./siri-remote run --dry-run
```

### 5. Iniciar automáticamente con la sesión

Después de comprobar que funciona manualmente, instala el servicio de usuario:

```bash
mkdir -p ~/.config/systemd/user
./siri-remote service-unit > ~/.config/systemd/user/siri-remote.service
systemctl --user daemon-reload
systemctl --user enable --now siri-remote.service
```

Detén cualquier ejecución manual antes de iniciar el servicio para evitar dos
instancias simultáneas. La unidad generada contiene las rutas absolutas del
proyecto. Si mueves la carpeta, genera de nuevo la unidad y recarga systemd.

```bash
systemctl --user status siri-remote.service
journalctl --user -u siri-remote.service -f
```

El servicio se inicia con la sesión del usuario y no requiere habilitar
`linger`. Los mensajes se envían a la consola o a journald; el programa no crea
un archivo de registro que crezca de forma indefinida.

### Comandos disponibles

```text
./siri-remote setup          empareja, verifica y guarda la identidad
./siri-remote run            mantiene el mando conectado
./siri-remote doctor         comprueba la instalación y la conexión
./siri-remote list           muestra adaptadores y mandos reconocidos
./siri-remote forget [ID]    elimina un vínculo después de confirmar
./siri-remote service-unit   genera la unidad systemd de usuario
```

Cada comando ofrece ayuda adicional, por ejemplo:

```bash
./siri-remote run --help
./siri-remote setup --help
```

### Diagnóstico de instalación y conexión

```bash
./siri-remote list
./siri-remote doctor
```

`doctor` comprueba las dependencias, `config.env`, `/dev/uinput`, BlueZ, el
adaptador y el emparejamiento. Si ya hay un mando configurado, también prueba la
conexión y el acceso GATT. Pulsa un botón durante la comprobación de eventos.

Si `/dev/uinput` no existe o el acceso está denegado, repite el paso 2 y
comprueba el grupo actual con `id`. Si Bluetooth está desactivado, actívalo
desde el escritorio.

Si la conexión llega a GATT pero aparece `NotAuthorized` o `NotPermitted`,
BlueZ puede estar restringiendo el servicio HID que administra. En las
versiones que admitan esta opción, añade o modifica la siguiente sección en
`/etc/bluetooth/main.conf`:

```ini
[GATT]
ExportClaimedServices=read-write
```

Reinicia BlueZ para aplicar el cambio:

```bash
sudo systemctl restart bluetooth.service
```

El reinicio interrumpe temporalmente las conexiones Bluetooth del equipo. Si el
vínculo queda desincronizado, puede ser necesario volver a emparejar:

```bash
./siri-remote forget
./siri-remote setup
```

`forget` pide confirmación y elimina únicamente el vínculo seleccionado. También
admite una identidad explícita cuando hay varios mandos. Un mapa GATT no
reconocido indica un dispositivo o firmware incompatible con el perfil actual.

## Configuración

`setup` crea `config.env` en la carpeta del proyecto. Para cambiar la
configuración, edita ese archivo y reinicia el proceso o el servicio. La
plantilla completa está disponible en
[config.env.example](config.env.example).

El archivo se interpreta como datos `NOMBRE=VALOR`; admite comentarios con `#`
y no ejecuta comandos de shell. Las opciones desconocidas, duplicadas o fuera
de rango producen un error antes de crear el teclado virtual.

`REMOTE_IDENTITY` contiene la identidad seleccionada durante `setup` y
`ADAPTER` elige el adaptador, inicialmente `hci0`. La ruta D-Bus del dispositivo
se resuelve en cada sesión y no se guarda. `config.env` está excluido de Git.

### Teclas y remapeos

| Opción | Entrada | Valor inicial |
|---|---|---|
| `KEY_UP` / `KEY_DOWN` | Arriba / abajo | `KEY_UP` / `KEY_DOWN` |
| `KEY_LEFT` / `KEY_RIGHT` | Izquierda / derecha | `KEY_LEFT` / `KEY_RIGHT` |
| `KEY_CENTER` | Centro corto | `KEY_ENTER` |
| `KEY_CENTER_LONG` | Centro mantenido | `KEY_ENTER` |
| `KEY_BACK` | Atrás | `KEY_ESC` |
| `KEY_TV` | TV corto | `KEY_HOME` |
| `KEY_TV_LONG` | TV largo | `KEY_LEFTMETA+KEY_D` |
| `KEY_SIRI` | Siri | `KEY_F12` |
| `KEY_PLAYPAUSE` | Play/Pausa | `KEY_PLAYPAUSE` |
| `KEY_VOLUMEUP` / `KEY_VOLUMEDOWN` | Volumen | `KEY_VOLUMEUP` / `KEY_VOLUMEDOWN` |
| `KEY_MUTE` | Silencio | `KEY_MUTE` |

Las asignaciones usan nombres evdev `KEY_*`. Las direcciones se comparten entre
el aro, su repetición y la superficie táctil. `KEY_TV_LONG` y `KEY_SIRI`
también admiten combinaciones separadas por `+` o `NONE` para desactivar la
acción:

```ini
KEY_BACK=KEY_BACKSPACE
KEY_PLAYPAUSE=KEY_SPACE
KEY_TV_LONG=KEY_LEFTMETA+KEY_D
KEY_SIRI=NONE
```

Si `KEY_CENTER` y `KEY_CENTER_LONG` coinciden, la tecla permanece pulsada desde
que se presiona Centro hasta que se suelta. La aplicación decide cómo
interpretar la duración. Si las asignaciones son diferentes, una pulsación
corta envía la primera tecla al soltar el botón antes del umbral; una pulsación
larga mantiene la segunda desde que se alcanza `LONG_PRESS_MS` hasta que se
suelta. La acción larga no envía también la acción corta.

TV corto actúa al soltar. TV largo envía una sola combinación al alcanzar el
umbral. Siri actúa una vez al presionar. Win+D, Home, F12 y las teclas
multimedia tienen el efecto que les asigne el escritorio o la aplicación. El
botón Siri no activa reconocimiento de voz.

### Pulsaciones y repetición

Todos los tiempos están expresados en milisegundos.

| Opción | Inicial | Efecto |
|---|---|---|
| `BUTTON_DEBOUNCE_MS` | `50` | Ignora otra pulsación del mismo botón durante este plazo después de soltarlo; `0` desactiva |
| `LONG_PRESS_MS` | `300` | Umbral de TV largo y de Centro largo cuando sus teclas difieren |
| `REPEAT_ENABLED` | `true` | Repite las direcciones del aro al mantenerlas |
| `REPEAT_DELAY_MS` | `450` | Espera hasta la primera repetición |
| `REPEAT_INTERVAL_MS` | `120` | Separación entre repeticiones |

La primera flecha del aro se envía al presionar. Centro, Atrás, TV, Siri y las
teclas multimedia no se repiten. Si el proceso se retrasa, no recupera los
pasos atrasados en una ráfaga.

### Superficie táctil

| Opción | Inicial | Efecto |
|---|---|---|
| `TOUCH_MODE` | `continuous` | Envía flechas durante el movimiento; `release` envía como máximo una al levantar |
| `TOUCH_STEP` | `18` | Recorrido mínimo por paso; un valor menor aumenta la sensibilidad |
| `TOUCH_INTERVAL_MS` | `90` | Separación mínima entre flechas |
| `TOUCH_SETTLE_MS` | `50` | Tiempo inicial para estabilizar y reanclar el contacto |
| `TOUCH_AXIS_LOCK` | `true` | Conserva el eje horizontal o vertical durante el contacto continuo |
| `TOUCH_REVERSE_MARGIN` | `4` | Margen para invertir el sentido desde el extremo alcanzado |
| `TOUCH_GAIN_X` / `TOUCH_GAIN_Y` | `1.0` | Ganancia del desplazamiento por eje |
| `TOUCH_INVERT_Y` | `false` | Invierte la dirección vertical |

Con el bloqueo de eje, un movimiento diagonal no genera una dirección hasta que
un eje predomina un 35 % y supera el umbral. Para cambiar de eje, levanta el
dedo y vuelve a apoyarlo. La inversión exige al menos
`max(TOUCH_STEP, TOUCH_REVERSE_MARGIN)` desde el extremo alcanzado; esos valores
no se suman.

Un clic cancela el resto del gesto táctil para evitar una acción duplicada. El
dedo quieto no genera pasos pendientes y no hay inercia al levantarlo.

### Opciones de ejecución

```bash
./siri-remote run --config ./otro.env
./siri-remote run --touch-step 20
./siri-remote run --no-touch
./siri-remote run --dry-run --raw-touch
./siri-remote doctor --listen-seconds 15
```

`--touch-step` sustituye temporalmente el valor del archivo. `--no-touch` deja
solo los botones. `--raw-touch` muestra los bytes de la característica táctil y
`--dry-run` registra las teclas sin inyectarlas.

## Funcionamiento interno

Cada pulsación sigue este recorrido:

```text
Siri Remote A2540 → Bluetooth LE → BlueZ / D-Bus → perfil A2540
                  → botones y contactos → navegación → uinput → aplicación
```

BlueZ administra el enlace Bluetooth y expone los servicios GATT por D-Bus. El
programa se suscribe a las notificaciones del mando, decodifica sus informes y
crea un teclado virtual mediante evdev/uinput. La aplicación recibe eventos de
teclado del sistema, por lo que no necesita una integración específica.

### Identificación y vínculo

La detección combina los datos de fabricante Apple (`0x004c`), el servicio HID
(`0x1812`), la apariencia `0x03c0` y la ausencia de la propiedad `Class`. Esta
combinación selecciona candidatos compatibles; no constituye por sí sola una
identificación infalible del modelo.

Durante el emparejamiento se comprueba `Paired` y, cuando BlueZ publica esa
propiedad, también `Bonded`. Después, el mando se marca como confiable. Para
acceder a los informes, la conexión debe estar activa y `ServicesResolved` debe
tener un valor verdadero. El emparejamiento por sí solo no confirma que el
programa esté recibiendo los botones.

### Inspección GATT y activación

El perfil se construyó a partir de la tabla GATT que BlueZ expuso para un A2540
real. Para cada característica se registraron el UUID, el handle, los permisos
y el descriptor Report Reference. El mapa observado estaba desplazado una
posición respecto del mapa de referencia conocido, así que el perfil reconoce
ambos:

| Función | Handle de referencia | Handle observado | Report ID observado | Tipo observado |
|---|---|---|---|---|
| Botones | `0x0039` | `0x003a` | `0xfb` | Input (`1`) |
| Superficie táctil | `0x003d` | `0x003e` | `0xfc` | Input (`1`) |
| Activación | `0x004d` | `0x004e` | `0xf0` | Feature (`3`) |

BlueZ publica el handle de declaración de cada característica. El perfil suma
uno para obtener el handle del valor. Además, el reconocimiento comprueba los
UUID de batería y de varios informes para evitar la activación de un mapa
incompleto que coincida por casualidad.

Antes de escribir, el programa verifica que las características pertenezcan al
servicio HID y lee sus descriptores Report Reference (`0x2908`). Los botones y
la superficie táctil deben ser informes Input con notificaciones. La
característica de activación debe ser Output o Feature y permitir escritura.
Los Report ID de la tabla son valores observados; la validación exige el tipo
correcto, no un ID fijo.

Para activar el mando, el programa se suscribe a los informes de botones y de
la superficie táctil y luego escribe `f0 00` en la característica de
activación. Tras esa escritura se comprobó la recepción de informes. La
secuencia funciona con el dispositivo probado, pero no constituye una
especificación oficial del protocolo.

### Decodificación de botones y superficie táctil

Los botones llegan en dos bytes que forman una máscara de 16 bits en orden
little-endian. La comparación de cada máscara con la anterior permite detectar
pulsaciones y liberaciones independientes, incluidas las combinaciones. Por
ejemplo, `40 00` representa Atrás y `00 00` representa su liberación cuando no
hay otro botón pulsado. La tabla completa está en
[remotes/a2540.py](siri_remote_linux/remotes/a2540.py).

La superficie táctil utiliza informes de 11 bytes: cuatro de cabecera y siete
de contacto. El decodificador convierte sus campos en coordenadas aproximadas y
utiliza la presión para determinar si existe contacto. Una presión de cero
termina el contacto en la última posición conocida. Estas coordenadas son
unidades internas del decodificador, no píxeles ni distancias físicas
calibradas.

Durante las pruebas también aparecieron informes táctiles de 18 bytes. Ese
formato todavía no está decodificado. Cuando aparece, solo se descarta la
continuidad del contacto táctil; el siguiente informe válido comienza un
contacto nuevo. Así se evita interpretar el salto entre ambos contactos como un
movimiento y se conservan los botones que continúen pulsados.

### Ajuste del comportamiento

El ajuste se realizó con pruebas manuales: se pulsaron, mantuvieron y soltaron
los botones, y se hicieron gestos sobre la superficie táctil mientras se
comparaban los bytes recibidos, los eventos decodificados y las teclas
generadas. Estas capturas respaldan el mapa observado, pero no describen el
protocolo completo del fabricante.

A partir de esos eventos se calibraron la estabilización inicial, el paso
mínimo, el intervalo entre flechas, el bloqueo de eje y la inversión de sentido.
Los valores actuales —18 unidades, 90 ms y 50 ms— produjeron el comportamiento
utilizado como referencia. Las pruebas automatizadas cubren rebotes, diagonales,
cambios de presión, clics durante un gesto, Centro mantenido, combinaciones y
errores al liberar teclas.

Los detalles BLE del A2540 quedan aislados en su perfil. La navegación trabaja
con eventos comunes y la salida solo conoce teclas. La escucha de D-Bus y los
temporizadores comparten un contexto GLib, con un ciclo de navegación de 10 ms.

### Reconexión y límites actuales

`run` crea un único teclado virtual. Si el mando entra en reposo, BlueZ se
reinicia o el adaptador desaparece, el programa libera las teclas, descarta las
suscripciones y reconstruye la sesión. Los reintentos esperan 1, 2, 5 y 10
segundos. Después mantienen un máximo de 10 segundos y vuelven a comenzar en 1
segundo tras una activación correcta.

Solo está implementado el A2540. No hay audio, reconocimiento de voz, cursor ni
multitouch. El botón de encendido se decodifica, pero no tiene una tecla de
salida asignada. La experiencia completa debe comprobarse con el mando físico,
incluidos el reposo y la reactivación, los reinicios de Bluetooth y el arranque
con la sesión.

### Pruebas y perfiles adicionales

Las pruebas usan simulaciones de Bluetooth y uinput, por lo que no necesitan un
mando conectado:

```bash
python3 -m unittest discover -v
```

Para añadir otro modelo, implementa el contrato de
[RemoteProfile](siri_remote_linux/remotes/base.py) y regístralo en
[remotes/__init__.py](siri_remote_linux/remotes/__init__.py). El perfil debe
incluir la detección, la identidad, el mapa GATT, la activación, la
decodificación y el restablecimiento, junto con capturas y pruebas del nuevo
formato.

## Referencias y agradecimientos

Este proyecto agradece a los autores y colaboradores de los siguientes
repositorios, consultados como referencias durante su desarrollo:

- [azais-corentin/siri-remote](https://github.com/azais-corentin/siri-remote)
- [Yanndroid/SiriRemote-Linux](https://github.com/Yanndroid/SiriRemote-Linux)
- [Jack-R1/SiriRemoteVoiceControl](https://github.com/Jack-R1/SiriRemoteVoiceControl)
- [retsyx/SiriRemote](https://github.com/retsyx/SiriRemote)

Licencia [MIT](LICENSE).
