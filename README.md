# RAE Electricity Price for Home Assistant

A custom Home Assistant integration that exposes a Greek electricity tariff from
the RAE / RAAEY registry ([energycost.gr](https://energycost.gr)) as sensors in
EUR/kWh (single-zone, tiered, or day/night depending on the plan).

## How it works

The tariff data is read from a maintained endpoint on our own server, so the
integration works out of the box without any extra setup on your side.

## Installation

### HACS (recommended)

1. Add this repository as a custom repository in HACS (category: Integration).
2. Install **RAE Electricity Price**.
3. Restart Home Assistant.

### Manual

1. Copy the `custom_components/rae_price_scraper` folder into your Home
   Assistant `config/custom_components` directory.
2. Restart Home Assistant.

## Configuration

Configuration is done entirely from the UI (no YAML):

1. Go to **Settings > Devices & Services > Add Integration**.
2. Search for **RAE Electricity Price**.
3. Choose the **Provider** from the list. The list comes from the tariff feed,
   so Home Assistant needs internet access during setup.
4. Choose the **Plan** from the provider's plans, and whether to **Use
   discounted price** (the price with conditions instead of the standard one).
   - The registry publishes plans with a month-version, such as
     `My Home Online 06.26`. The list shows the name without it, and the
     integration matches that name, so the sensor keeps working when the
     provider bumps the version (e.g. to `07.26`).
   - Plans that are no longer sold are marked **withdrawn**, and plans missing
     from the newest registry month are marked **last listed MM/YYYY**. They
     stay selectable because an existing customer may still be on them.

Minimum Home Assistant version: 2025.11.0.

You can change any of these later with **Reconfigure** on the integration
entry. Versions before 0.12.0 also had a **Configure** options dialog; the
settings it saved are moved into the entry automatically on upgrade.

## The sensors

The entities created depend on the matched plan (all in `EUR/kWh`, polled every
12 hours):

- **Flat plan**: `sensor.rae_price_single` (single-zone price), plus
  `sensor.rae_price_dual` if the plan also carries a static time-of-use price.
- **Tiered plan** (different price per consumption band): `sensor.rae_price_single`
  holds the average across tiers, plus `sensor.rae_price_tier1`,
  `sensor.rae_price_tier2`, ... for each band.
- **Day/night plan**: `sensor.rae_price_single` holds the average, plus
  `sensor.rae_price_day` and `sensor.rae_price_night`.

Always created: `sensor.rae_price_period` (the registry month the price refers
to, as `MM/YYYY`) and `sensor.rae_last_updated` (feed refresh timestamp). Price
sensors expose `provider`, `plan`, `discounted_price`, `matched_period`,
`product_kind` and `available` as attributes.

If the current month's tariff is not found, the integration searches back up to
12 months so the sensors still report the most recent applicable price.

## Tests

Install the `requests` package. Run `python -m unittest discover -s tests`.

The feed reader rejects HTTP redirects, decoded responses larger than 10 MiB and
downloads that take longer than 60 seconds in total.
A custom feed URL must point directly to the JSON feed without redirects.
