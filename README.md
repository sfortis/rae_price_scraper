# RAE Electricity Price for Home Assistant

A custom Home Assistant integration that exposes a Greek electricity tariff from
the RAE / RAAEY registry ([energycost.gr](https://energycost.gr)) as a sensor
(`sensor.rae_price_per_kwh`, in EUR/kWh).

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
3. Fill in:
   - **Provider**: exactly as listed on the registry, e.g. `ΔΕΗ`.
   - **Plan name**: exactly as listed, **without** the trailing month-version.
     The registry shows plans like `My Home Online 06.26`; enter just
     `My Home Online`. The integration matches the plan exactly while ignoring
     that `MM.YY` version, so your sensor keeps working when the provider bumps
     it (e.g. to `07.26`).
   - **Use discounted price**: use the discounted (with-conditions) price
     instead of the standard one.

You can change any of these later with **Reconfigure** (or the **Configure**
options dialog) on the integration entry.

### Migrating from the old YAML setup

Older versions used a `sensor:` platform in `configuration.yaml`. On upgrade,
an existing YAML entry is **imported automatically** into a config entry on the
next restart. After confirming the entity still works, remove the old
`rae_price_scraper` block from `configuration.yaml`.

## The sensor

- **Entity**: `sensor.rae_price_per_kwh`
- **Unit**: `EUR/kWh`
- **Polling**: every 12 hours
- **Attributes**: `provider`, `plan`, `discounted_price`, `last_found_month`,
  `last_update`

If the current month's tariff is not found, the integration searches back up to
12 months so the sensor still reports the most recent applicable price.

## Tests

Install the `requests` package. Run `python -m unittest discover -s tests`.

The feed reader rejects HTTP redirects and decoded responses larger than 10 MiB.
A custom feed URL must point directly to the JSON feed without redirects.
