"""Constants of the Lichtschnitzel integration."""

DOMAIN = "lichtschnitzel"
MDNS_TYPE = "_lichtschnitzel._tcp.local."

CONF_PASSWORD = "password"
DEFAULT_PORT = 80

FBM = "FramebufferManager"
NUM_SCREENS = 16  # FramebufferManager::kNumScreens
NO_EFFECT = 4294967295  # EffectIndex of a screen without effect (UINT_MAX)

PLATFORMS = ["light", "button", "number", "switch", "sensor", "binary_sensor"]
