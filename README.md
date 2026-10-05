# sportcam

Estadísticas deportivas con una cámara y Python. Son **funciones para meter en tu app**, no una app:

| Qué | Dónde | Estado |
|---|---|---|
| Leer un número mostrando los dedos (0–10, dos manos; 2 cifras para dorsales) | `sportcam/gestures.py` | lógica probada; **falta probar con tu cámara** |
| Identificar jugadores por la cara | `sportcam/faces.py` | probado con una foto real |
| Replay de los últimos 3 min al marcar un gol, exportado a mp4 | `sportcam/replay.py` | probado de punta a punta |
| Deportes (fútbol, básquet, pádel; agregás los que quieras) | `sportcam/sports.py` | probado |
| Estadísticas, partidos, torneos con reglas elegibles | `sportcam/store.py`, `tournaments.py` | probado |
| Gesto → evento → clip, todo junto | `sportcam/session.py` | probado con manos/caras simuladas |
| Códigos de partido, check-in, deshacer, plan B manual, resumen de 30 s, purga de clips | `store.py`, `session.py`, `maintenance.py` | probado |
| Calendario todos-contra-todos, llaves de eliminación, resumen y figura del partido | `fixtures.py`, `store.py` | probado |
| "Quiero jugar": equipos armados / incompletos, filtros por día y deporte, emparejar | `sportcam/lobby.py` | probado |
| Páginas web: jugadores y jugadas, torneos (tabla + calendario), partidos | `sportcam/viewer.py` | probado |

El marketplace / inscripciones es tu app: `Store` es una implementación de referencia en SQLite;
si ya tenés tu base, replicá esos métodos.

## Instalar

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Probado con Python 3.11. En Linux sin escritorio puede faltar `libegl1` y `libgles2`
(`sudo apt install libegl1 libgles2`). El modelo de manos (8 MB) se descarga solo la primera vez a `models/`.

## Probarlo con tu cámara (en este orden)

**1. ¿Reconoce el número?**
```bash
python demos/numero.py --debug
```
Mostrá de 0 a 10 dedos y sostené ~1 s. Con `--debug` ves qué dedo toma como levantado.
Los avisos largos que empiezan con `W0000`/`INFO` son internos de MediaPipe y son normales.

**2. ¿Reconoce las caras?**
```bash
python demos/caras.py registrar "Ana"     # pide consentimiento escrito
python demos/caras.py reconocer
```

**3. Partido completo**
```bash
python demos/partido.py --deporte futbol --jugador Ana:A:10 --jugador Beto:B:7
python -m sportcam.viewer                 # y abrí http://127.0.0.1:8000
```
Durante el partido, quien juega muestra el gesto:

- **1 dedo = gol** · 2 = asistencia · 3 = atajada (en básquet 1–7: libre, doble, triple, asistencia, rebote, robo, tapón; en pádel 1–4).
- Si la cámara reconoce su cara, queda anotado. Si no, bajá la mano y mostrá tu **número de camiseta** (`1` y después puño = `10`).
- En un gol se guarda el video de los **últimos 3 minutos** y aparece en la página del jugador.

## Quiero jugar (equipos armados y equipos incompletos)

Cada anuncio es un equipo con **día, hora y lugar**. El sistema lo clasifica solo según cuántos jugadores tenga:
**armado** (completo, busca rival) o **incompleto** (muestra cuántos faltan). Al sumarse el último jugador pasa a "armados".
Tamaño por defecto: fútbol 5, básquet 5, pádel 2 (se puede cambiar con `team_size`).

```python
from datetime import datetime
from sportcam.lobby import Lobby
lobby = Lobby(db)
a = lobby.create_post(ana, "padel", datetime(2026, 10, 8, 20, 0), members=[beto], place="Cancha 2")  # pareja armada
b = lobby.create_post(carla, "padel", datetime(2026, 10, 8, 20, 30))                                 # le falta 1
lobby.join_post(b, dario)                                  # se completa y pasa a "armados"
lobby.list_posts("padel")                                  # {"armados": [...], "incompletos": [...]}; filtro: day=date(...)
lobby.suggest_rivals(a)                                    # equipos armados del mismo deporte a horario parecido
m = lobby.match_posts(a, b)                                # crea el partido A vs B (y sale de la lista)
```
Reglas: el horario tiene que ser a futuro; un jugador no puede estar en dos anuncios del mismo deporte con
menos de 2 h de diferencia; solo el creador cancela (si se va, pasa el anfitrión al siguiente).
La página `python -m sportcam.viewer` → `/quiero-jugar` muestra las dos secciones con filtros por deporte y día.
Por consola: `python demos/quiero_jugar.py --help`. Las horas son la hora local del servidor.
La web es de solo lectura: publicar y sumarse se hace desde tu app con estas funciones (con tu login).

## Flujo recomendado en el lugar

1. **Una vez, presencial:** el jugador se registra con su cara y acepta el consentimiento (`demos/caras.py registrar`).
2. **Al alquilar la cancha:** el sistema crea el partido y un **código** de 6 caracteres que vence solo
   (`store.create_match_code(m)` o `python demos/codigos.py crear`).
3. **Cada jugador canjea el código** eligiendo equipo y número de camiseta
   (`store.join_with_code(codigo, jugador, "A", 10)`). No se pueden repetir números.
4. **Al arrancar, check-in:** cada uno pasa frente a la cámara (`session.check_in(frame)`); `store.missing_check_in(m)`
   dice quién no confirmó su identidad.
5. **Durante el partido:** gesto de evento + número, o la cara si se ve. Un error se corrige con **dos manos
   abiertas (10 dedos)** = deshacer, o con `session.undo_last()` / `store.undo_event(id)`.
   Si el gesto no anda, `session.record_manual("gol", dorsal=10)` desde un botón del celular.
6. **Clips:** cada gol guarda el video de 3 min y un resumen de 30 s. `purge_old_clips(store, "clips", 30)` borra los viejos.

## Usarlo desde tu app

```python
from sportcam.store import Store
from sportcam.session import MatchSession
from sportcam.gestures import HandTracker
from sportcam.replay import ReplayBuffer

db = Store("mi.db")
ana = db.add_player("Ana", face_consent=True)
beto = db.add_player("Beto")

# Torneo entre amigos: se elige cómo se decide el ganador (en orden de prioridad)
t = db.create_tournament("Liga del barrio", "futbol", owner_id=ana, rank_by=("wins", "gol"))
db.join_tournament(t, beto)             # "wins" = partidos ganados; si empatan, más goles

m = db.create_match("futbol", tournament_id=t)
db.add_match_player(m, ana, "A", dorsal=10)
db.add_match_player(m, beto, "B", dorsal=7)

session = MatchSession(db, m, hands=HandTracker(), replay=ReplayBuffer(seconds=180))
for frame, t_seg in tu_fuente_de_video():           # frames BGR de OpenCV
    for aviso in session.process(frame, t_seg):      # eventos / mensajes para mostrar
        print(aviso.message)

db.finish_match(m)
db.leaderboard(t)               # tabla ordenada según la regla del torneo
db.player_stats(ana)            # goles, asistencias, partidos ganados, torneos jugados/ganados...
db.list_clips(player_id=ana)    # jugadas guardadas, para tu página
db.close_tournament(t)          # fija al campeón
```

Calendario y resumen:

```python
db.schedule_tournament(t)                      # todos contra todos (double=True: ida y vuelta)
cruce = db.tournament_fixtures(t)[0]           # estado: pendiente / en juego / jugado
m = db.start_fixture(cruce["id"], dorsal_a=10, dorsal_b=7)   # crea el partido del cruce
db.match_summary(m)                            # marcador, acciones por jugador y figura (MVP)
from sportcam.fixtures import knockout_first_round
knockout_first_round([1, 2, 3, 4, 5])          # llaves con byes para los mejores sembrados
```
`python -m sportcam.viewer` ahora también muestra `/torneo/ID` y `/partido/ID`.
Torneos por equipos (equipo contra equipo):

```python
rojos = db.create_team(t, "Rojos", [ana, beto])      # un jugador, un solo equipo por torneo
azules = db.create_team(t, "Azules", [cami, dani])
db.schedule_teams(t)                                  # calendario entre equipos
m = db.start_team_fixture(db.team_fixtures(t)[0]["id"], dorsals={ana: 10, beto: 7, cami: 9, dani: 4})
db.team_standings(t)                                  # 3/1/0 puntos, desempate por diferencia y goles a favor
```
Los jugadores siguen acumulando sus propias estadísticas y `leaderboard(t)` sigue funcionando.
`schedule_tournament` (sin equipos) arma cruces jugador contra jugador.

Criterios de torneo (`rank_by`): `wins`, `losses`, `draws`, `matches`, `win_rate`, `points`,
`league_points` (3/1/0) y cualquier evento del deporte (`gol`, `asistencia`, `triple`...).
Estadísticas filtrables por deporte y por torneo: `player_stats(id, sport="futbol", tournament_id=t)`.

Deporte propio: `register_sport(Sport("tenis", "Tenis", (EventType("ace", "Ace", code=1, points=1),)))`.

## Lo que NO está o no pude verificar

- **Con una cámara real no lo probé** (no tengo una acá). La detección de la mano usa MediaPipe, cargado
  y ejecutado de verdad, pero nunca vio una mano. El conteo de dedos se probó con manos sintéticas
  (rotadas, inclinadas, con ruido): la lógica es correcta, pero los umbrales (`FingerConfig`) pueden
  necesitar un ajuste con tu mano y tu luz. Para eso está `--debug`.
- **Dibujar el número en el aire (trazar un "7" con el dedo) no está.** Lo intenté y un clasificador con
  mis datos de prueba acertaba ~60 % (casi nunca 6, 8 y 9). Mostrar los dedos es mucho más confiable. Para
  hacerlo bien hace falta un conjunto de trayectorias reales (ej. Pendigits de UCI) que no pude bajar
  desde mi entorno.
- **Caras:** probado con una sola persona real (distancia 0,08–0,12 de la misma cara con otra escala y
  luz; límite 0,5). No pude medir cuánto confunde a dos personas parecidas. La cara debe verse grande:
  en una cancha entera con la cámara lejos no va a reconocer a nadie; para eso está el dorsal.
- **Pádel:** cuenta puntos ganados y golpes; el resultado del partido se indica con
  `finish_match(m, winner_side="A")` (games/sets los lleva tu app).
- **Memoria:** el buffer de 3 minutos usa unos 200–300 MB a 960 px / 25 fps.

## Privacidad

El reconocimiento facial es dato biométrico. `Store.save_face` **se niega** a guardar sin
`face_consent=True`, guarda solo el vector (no la foto) y `Store.forget_face(id)` lo borra y revoca el
consentimiento. Revisá lo que exija la ley de tu país antes de usarlo con público.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
