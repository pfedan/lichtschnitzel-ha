# Lichtschnitzel für Home Assistant

Bindet [Lichtschnitzel](https://github.com/pfedan/lichtschnitzel)-LED-Controller (ESP32-S3) in Home Assistant ein. Die Integration spricht die eigene HTTP- und WebSocket-API der Lampe (`firmware/API.md`, Version 2): eine WebSocket-Verbindung pro Lampe hält alles live, ohne Polling, ohne MQTT.

**Ab Version 0.6 braucht die Integration eine Lampe mit API-Version 2** (die neu geschriebene Firmware). Firmware, Web-UI der Lampe und Integration gehören zusammen: erst die Lampe aktualisieren, dann die Integration. Eine Lampe mit älterer Firmware wird nicht eingerichtet und sagt in der Integrationsliste, warum; für sie Version 0.5 behalten.

## Installation

Ab Home Assistant 2026.11 über den eingebauten [Marketplace](https://www.home-assistant.io/integrations/marketplace/) (ersetzt HACS): *Einstellungen → Marketplace*, oben rechts „Add from link“ (in der deutschen Oberfläche entsprechend übersetzt), `pfedan/lichtschnitzel-ha` eintragen (Typ Integration), dann auf der Seite des Repositorys installieren. Auf älteren Versionen über HACS als benutzerdefiniertes Repository (Typ Integration). Oder `custom_components/lichtschnitzel` in den `config`-Ordner von Home Assistant kopieren. Nach einem Neustart findet Home Assistant Lampen im Netz von selbst (mDNS `_lichtschnitzel._tcp`); sonst unter *Einstellungen → Geräte & Dienste → Integration hinzufügen → Lichtschnitzel* die Adresse eingeben. Ein Passwort braucht es nur, wenn die Lampe eins für die Steuerung (`control`) hat.

## Was in Home Assistant ankommt

Ein Gerät pro Lampe, darin:

| Entity | Typ | Was sie tut |
| --- | --- | --- |
| Lampe | `light` | Hauptschalter und Master-Helligkeit der Lampe. Aus pausiert alle Effekte. |
| Screen *n* (oder sein Name) | `light` | An/Aus, Helligkeit, Effekt aus der Effektliste. Aus gibt die Effekt-Instanz frei, die Parameter bleiben gemerkt. |
| Szene | `select` | Die Szenen der Lampe (benannte Sätze aller Screens) nach Name; Auswählen schaltet um. Hat sich seit dem Umschalten ein Screen geändert, zeigt sie keine Auswahl (Attribut `modified`), so lässt sich die Szene erneut wählen. |
| … nächstes Preset | `button` | `next-preset` des Screens; `unavailable`, wenn der Effekt keine Presets hat |
| … gespiegelt, … default values | `switch`, `button` | Konfiguration je Screen |
| Effektparameter | `number`, Farben als `light` | je Screen, Effekt und Parameter; standardmäßig deaktiviert, in der Entity-Liste einschalten |
| Zustand speichern, Neustart | `button` | `POST /api/save`, `/api/reboot` (Neustart standardmäßig aus) |
| LED-Strom, Strombudget, USB-PD, WLAN, … | `sensor`, `binary_sensor` | aus dem Status der Lampe, gerundet, damit die Datenbank nicht jede Sekunde wächst |

Screens sind feste Plätze (0 bis 15). Ein Screen bekommt Entities, sobald er LEDs hat; verliert er sie, werden seine Entities `unavailable`, aber nicht gelöscht, so bleiben Namen, Räume und Automationen erhalten. Benannt werden sie nach dem Namen des Screens (`label`, in der Web-UI unter Einstellungen → Screens), sonst „Screen *n*“.

Effektparameter-Entities hängen an Screen und Effekt (`number.<lampe>_flur_aurora_speed`). Zeigt der Screen gerade einen anderen Effekt, ist die Entity `unavailable`; kommt der Effekt zurück, hat er die Werte, die der Screen sich gemerkt hat.

Beim Umstieg von Version 0.5 bleiben alle Entities, ihre Namen und ihre Historie erhalten. Die Parameter-Entities bekommen dabei intern neue Kennungen (Effekt und Parameter heißen jetzt nach ihren IDs, etwa `aurora` und `speed`), die Entity-IDs ändern sich nicht. Eine Entity zu einem Parameter, den es in der neuen Firmware nicht mehr gibt, bleibt als „nicht mehr bereitgestellt“ stehen und kann von Hand gelöscht werden.

## Die Lichtschnitzel-Karte

Die Integration bringt eine Dashboard-Karte mit, die Home Assistant von selbst lädt (keine Ressource von Hand eintragen): Sie trägt sich als Dashboard-Ressource ein (`/lichtschnitzel/lichtschnitzel-card.js?v=…`, nur wenn die Ressourcen über die Oberfläche verwaltet werden), so lädt sie auch die Companion-App. Sie zeigt je Screen An/Aus, Helligkeit, den Effekt und darunter die Parameter des Effekts, der gerade läuft: Regler für Zahlen, Farbwähler für Farben. Wechselt der Effekt, wechseln die Regler mit. Die Parameter-Entities müssen dafür nicht aktiviert sein.

```yaml
type: custom:lichtschnitzel-card
title: Wohnzimmer          # optional
entities:                  # oder entity: light.lampe_screen_0
  - light.lampe_screen_0
  - light.lampe_screen_1
```

Nach dem Update der Integration einmal die Seite im Browser neu laden, damit die neue Karte ankommt.

Die Karte nutzt das Attribut `parameters` der Screen-Leuchte (je Parameter `key`, `name`, `group`, `type`, `value`, `min`, `max`; nicht in der Datenbank gespeichert) und den Dienst `lichtschnitzel.set_parameter`, der auch in Automationen geht:

```yaml
action: lichtschnitzel.set_parameter
target:
  entity_id: light.lampe_screen_0
data:
  parameter: speed           # die ID des Parameters, wie sie im Attribut parameters als key steht
  value: 1.5                 # Farben als [r, g, b] oder "#rrggbb"
```

Der Wert wird auf die Grenzen des Parameters begrenzt. Zeigt der Screen den Effekt eines anderen Screens, setzt der Dienst den Parameter dort. Die Namen aus der Zeit vor API-Version 2 (`Speed`, `Flying Emitters/Period Length`) versteht der Dienst weiter, bestehende Automationen laufen also unverändert.

## Szenen

Szenen legt man in der Web-UI der Lampe an (Steuerung → Szenen). In Home Assistant schaltet die Entity `select.<lampe>_szene` um, in Automationen geht auch der Dienst `lichtschnitzel.activate_scene`, nach Index (0 bis 15) oder Name:

```yaml
action: lichtschnitzel.activate_scene
target:
  entity_id: select.lampe_szene
data:
  name: Abend                # oder index: 2
```

Ändert sich danach ein Screen, zeigt die Entity die Szene weiter an, als „<Name> (geändert)“; die Szene mit ihrem Namen noch einmal wählen lädt sie neu. Der Knopf `button.<lampe>_szene_uberschreiben` speichert den momentanen Zustand in die zuletzt gewählte Szene (braucht das Passwort für `settings`, wenn eins gesetzt ist).

Hauptschalter und Helligkeit der Lampe bleiben beim Umschalten, wie sie sind. Wie jede Änderung gilt eine Szene nach einem Neustart der Lampe erst, wenn sie gespeichert wurde („Zustand speichern“).

## Benachrichtigungen

`lichtschnitzel.notify` lässt Screens kurz signalisieren, etwa wenn es klingelt, die Waschmaschine fertig ist oder der Fernseher angeht. Danach zeigt jeder Screen wieder genau das, was er vorher zeigte; am Zustand der Lampe ändert sich nichts. Ziel sind Screen-Leuchten (dieser Screen) oder die Leuchte der Lampe (alle Screens). Mehrere Screens einer Aktion starten gleichzeitig.

```yaml
automation:
  - triggers:
      - trigger: state
        entity_id: binary_sensor.tuerklingel
        to: "on"
    actions:
      - action: lichtschnitzel.notify
        target:
          entity_id: [light.lampe_flur, light.lampe_wohnzimmer]
        data:
          pattern: pulse         # flash, pulse, chase, bounce, solid
          rgb_color: [255, 160, 0]
          brightness_pct: 80
          duration: 5            # Sekunden, 0 beendet die Benachrichtigung
          start: -6              # optional: nur die letzten sechs LEDs des Screens
```

`period` (Sekunden) setzt die Geschwindigkeit von Blinken, Pulsieren, Lauflicht und Pendel (hin und her), `length` die Breite des Abschnitts. Die Benachrichtigung erscheint auch, wenn der Screen oder die Lampe aus ist, mit ihrer eigenen Helligkeit.

## Stehende Anzeigen

`lichtschnitzel.set_indicator` zeigt ein Signal, bis `lichtschnitzel.clear_indicator` es wieder entfernt, etwa solange die Waschmaschine läuft. Jede Anzeige hat eine ID; dieselbe ID noch einmal ändert sie. Ein Screen kann mehrere Anzeigen gleichzeitig zeigen: Ohne `start` reihen sie sich vom Ende des Screens her auf (je `length` LEDs, Standard 3, mit einer dunklen LED dazwischen), höhere `priority` näher am Ende. Passt eine nicht mehr hin, wartet sie, bis Platz ist. Mit `start` sitzt sie an einem festen Abschnitt. `progress` (0 bis 100) macht daraus einen Balken.

```yaml
automation:
  - triggers:
      - trigger: state
        entity_id: sensor.waschmaschine_restzeit
    actions:
      - action: lichtschnitzel.set_indicator
        target:
          entity_id: light.lampe_bad
        data:
          id: waschmaschine
          rgb_color: [0, 120, 255]
          pattern: pulse
          length: 10
          progress: "{{ 100 - states('sensor.waschmaschine_restzeit') | float(0) / 90 * 100 }}"
  - triggers:
      - trigger: state
        entity_id: binary_sensor.waschmaschine_tuer
        to: "on"
    actions:
      - action: lichtschnitzel.clear_indicator
        target:
          entity_id: light.lampe_bad
        data:
          id: waschmaschine
```

### Blueprint: Anzeige folgt einer Entity

Für den häufigsten Fall (Anzeige an, solange ein Fenster offen ist oder ein Schalter an ist) gibt es einen Blueprint. Importieren unter Einstellungen → Automationen & Szenen → Blueprints → Blueprint importieren, mit dieser Adresse:

```
https://github.com/pfedan/lichtschnitzel-ha/blob/main/blueprints/automation/lichtschnitzel/indicator_from_state.yaml
```

Dann pro Kopplung eine Automation daraus anlegen: Entity, aktiver Zustand (Standard `on`), Screens, eine eigene ID, Farbe, Muster, Helligkeit, Breite und Priorität. Die Automation setzt die Anzeige auch nach einem Neustart der Lampe oder von Home Assistant wieder.

Alle Anzeigen auf einmal löscht der Knopf `button.<lampe>_anzeigen_loschen` (oder `lichtschnitzel.clear_indicator` auf die Leuchte der Lampe ohne ID), der Sensor `sensor.<lampe>_anzeigen` zählt die gesetzten Anzeigen. In der Web-UI der Lampe erscheint dafür ein Feld „Anzeigen“, solange welche gesetzt sind.

Benachrichtigungen liegen kurz über den Anzeigen. Anzeigen werden nicht gespeichert: Nach einem Neustart der Lampe sind sie weg, bis die Automation sie wieder setzt.

## Ohne Lampe testen: das Effektlabor als Lampe

Das Effektlabor (der digitale Zwilling im Browser) lässt sich über eine kleine Brücke als Lampe ins Netz stellen:

```sh
python3 firmware/tools/lab_bridge.py      # im lichtschnitzel-Repo, Port 8081
```

Dann im Labor (Panel „Web-UI der Lampe“ → „Im Netz“) mit `ws://127.0.0.1:8081/lab` verbinden, oder das Labor mit `?bridge=ws://127.0.0.1:8081/lab` öffnen. In Home Assistant die Adresse dieses Rechners mit Port 8081 eintragen; mit `pip install zeroconf` wird die Brücke auch automatisch gefunden. Die Brücke muss auf dem Rechner laufen, auf dem der Browser läuft.

Die 3D-Szenen (`scenes.html`) verbinden sich genauso; die Adresse merkt sich der Browser, so übernimmt die Szenen-Seite die Verbindung vom Labor und umgekehrt. Wechselst du die Szene, startet die Lampe neu: Home Assistant verbindet sich neu und übernimmt die Screens, Namen und Effekte der neuen Szene.

## Entwicklung

```sh
pip install -r requirements_test.txt
pytest                                          # gegen eine Fake-Lampe in den Tests
LICHTSCHNITZEL_LIVE=127.0.0.1:8080 pytest       # zusätzlich gegen Simulator, Labor oder echte Lampe
```

Der Simulator ist die Firmware selbst als Programm für den Rechner (`lz_sim` im lichtschnitzel-Repo, siehe dort `firmware/README.md`); mit `LICHTSCHNITZEL_PASSWORD` geht der Test auch gegen eine Lampe mit Passwort.

`custom_components/lichtschnitzel/api.py` ist der Client für die Lampen-API und hängt nur an aiohttp, `hub.py` hält den Zustand einer Lampe, die Plattformen (`light.py`, `number.py`, …) bilden ihn auf Entities ab.
