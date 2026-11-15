# Bundled frontend

`pylontech-battery-card.js` is adapted from
[Pylontech Battery Card](https://github.com/jtubb/Pylontech-Battery-Card)
v3.0.8 by jtubb, MIT licence (`LICENSE-pylontech-battery-card`). The ISA-101
layout, styling and heat maps are his work.

Changes made for this integration:

- It finds its entities by itself from the integration's devices (platform,
  device and translation key), so there is no `entity_prefix` to configure and
  renamed entities keep working.
- Stack figures (state of charge, time to full / empty, balance status and
  score) come from the integration's own sensors.
- History opens Home Assistant's own more-info dialog, so `mini-graph-card` is
  not needed.
- The element is `pylontech-battery-card`, so it does not clash with the
  original card if that is installed too.

The integration loads the file on every Home Assistant page (`frontend.py`).
After changing it, bump `CARD_VERSION` in both the `.js` and `frontend.py` so
browsers fetch the new copy.
