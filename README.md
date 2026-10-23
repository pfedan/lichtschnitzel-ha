# Lichtschnitzel für Home Assistant

Bindet [Lichtschnitzel](https://github.com/pfedan/lichtschnitzel)-LED-Controller (ESP32-S3) in Home Assistant ein. Die Integration spricht die eigene HTTP- und WebSocket-API der Lampe (`firmware-esp32/API.md`): eine WebSocket-Verbindung pro Lampe hält alles live, ohne Polling, ohne MQTT.

## Installation

Über [HACS](https://hacs.xyz/) als benutzerdefiniertes Repository (Typ Integration) hinzufügen, oder `custom_components/lichtschnitzel` in den `config`-Ordner von Home Assistant kopieren. Nach einem Neustart findet Home Assistant Lampen im Netz von selbst (mDNS `_lichtschnitzel._tcp`); sonst unter *Einstellungen → Geräte & Dienste → Integration hinzufügen → Lichtschnitzel* die Adresse eingeben. Ein Passwort braucht es nur, wenn die Lampe eins für die Steuerung (`control`) hat.

## Was in Home Assistant ankommt

Ein Gerät pro Lampe, darin:

| Entity | Typ | Was sie tut |
| --- | --- | --- |
| Lampe | `light` | Hauptschalter und Master-Helligkeit (`FramebufferManager/On`, `Brightness`). Aus pausiert alle Effekte. |
| Screen *n* (oder sein Name) | `light` | An/Aus, Helligkeit, Effekt aus der Effektliste. Aus gibt die Effekt-Instanz frei, die Parameter bleiben gemerkt. |
| … nächstes Preset | `button` | `next-preset` des Screens |
| … gespiegelt, … default values | `switch`, `button` | Konfiguration je Screen |
| Effektparameter | `number`, Farben als `light` | je Screen, Effekt und Parameter; standardmäßig deaktiviert, in der Entity-Liste einschalten |
| Zustand speichern, Neustart | `button` | `POST /api/save`, `/api/reboot` (Neustart standardmäßig aus) |
| LED-Strom, Strombudget, USB-PD, WLAN, … | `sensor`, `binary_sensor` | aus dem Status der Lampe, gerundet, damit die Datenbank nicht jede Sekunde wächst |

Screens sind feste Plätze (0 bis 15). Ein Screen bekommt Entities, sobald er LEDs hat; verliert er sie, werden seine Entities `unavailable`, aber nicht gelöscht, so bleiben Namen, Räume und Automationen erhalten. Benannt werden sie nach dem Namen des Screens (`Label`, in der Web-UI unter Einstellungen → Screens), sonst „Screen *n*“.

Effektparameter-Entities hängen an Screen und Effekt (`number.<lampe>_flur_aurora_speed`). Zeigt der Screen gerade einen anderen Effekt, ist die Entity `unavailable`.

Braucht die Firmware mit `On`, `Brightness`, `Label` und `revision` (lichtschnitzel ab dem PR „Home Assistant: An/Aus, Helligkeit, Namen“). Ältere Firmware geht auch, dann ohne Helligkeit; Aus setzt dort den Effekt auf „keiner“.

## Ohne Lampe testen: das Effektlabor als Lampe

Das Effektlabor (der digitale Zwilling im Browser) lässt sich über eine kleine Brücke als Lampe ins Netz stellen:

```sh
python3 firmware-esp32/tools/lab_bridge.py      # im lichtschnitzel-Repo, Port 8081
```

Dann im Labor (Panel „Web-UI der Lampe“ → „Im Netz“) mit `ws://127.0.0.1:8081/lab` verbinden, oder das Labor mit `?bridge=ws://127.0.0.1:8081/lab` öffnen. In Home Assistant die Adresse dieses Rechners mit Port 8081 eintragen; mit `pip install zeroconf` wird die Brücke auch automatisch gefunden. Die Brücke muss auf dem Rechner laufen, auf dem der Browser läuft.

## Entwicklung

```sh
pip install -r requirements_test.txt
pytest                                          # gegen eine Fake-Lampe in den Tests
LICHTSCHNITZEL_LIVE=127.0.0.1:8081 pytest       # zusätzlich gegen Labor, Mock oder echte Lampe
```

`custom_components/lichtschnitzel/api.py` ist der Client für die Lampen-API und hängt nur an aiohttp, `hub.py` hält den Zustand einer Lampe, die Plattformen (`light.py`, `number.py`, …) bilden ihn auf Entities ab.
