"""Constants of the Lichtschnitzel integration."""

DOMAIN = "lichtschnitzel"
MDNS_TYPE = "_lichtschnitzel._tcp.local."

CONF_PASSWORD = "password"
DEFAULT_PORT = 80

NUM_SCREENS = 16  # the lamp always has 16 screens; one without LEDs is unused

PLATFORMS = ["light", "button", "number", "switch", "select", "sensor", "binary_sensor"]

CARD_URL = f"/{DOMAIN}/lichtschnitzel-card.js"

# Patterns of lichtschnitzel.notify, as POST /api/notify takes them.
NOTIFY_PATTERNS = ["flash", "pulse", "chase", "bounce", "solid"]
