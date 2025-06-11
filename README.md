# RAE Price Scraper for Home Assistant

This custom Home Assistant sensor component scrapes electricity prices from the RAE (Regulatory Authority for Energy) website (https://energycost.gr) and provides the price as a sensor in Home Assistant.

## Features

- Fetches the latest electricity price per kWh in EUR from RAE.
- Configurable provider and plan filters.
- Configurable scraping URL.

## Installation

1. **Clone or Download**: Clone this repository into your Home Assistant's `custom_components` directory or download the zip file and extract the `rae_price_scraper` folder into the `custom_components` directory.
2. **Restart Home Assistant**
3. **Configuration**: Add the sensor configuration to your `configuration.yaml` file with the necessary parameters.
4. **Restart Home Assistant**: Restart your Home Assistant instance to pick up the new sensor.

## Configuration

Add the following to your `configuration.yaml` file:

```yaml
sensor:
  - platform: rae_price_scraper
    provider_filter: "ΔΕΗ"
    plan_filter: "My Home Online"
    url: "https://energycost.gr/%ce%ba%ce%b1%cf%84%ce%b1%cf%87%cf%89%cf%81%ce%b7%ce%bc%ce%ad%ce%bd%ce%b1-%cf%84%ce%b9%ce%bc%ce%bf%ce%bb%cf%8c%ce%b3%ce%b9%ce%b1-%cf%80%cf%81%ce%bf%ce%bc%ce%ae%ce%b8%ce%b5%ce%b9%ce%b1%cf%82-%ce%b7-3/"
    discounted_price: "Y"
    scan_interval: 86400
```

## Usage

Once configured and Home Assistant has been restarted, the sensor will appear in your Home Assistant instance as sensor.rae_price_per_kwh. It will update once every day by default, but you can modify the scan_interval to change the update frequency.
