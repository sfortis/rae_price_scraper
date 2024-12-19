# RAE Price Scraper for Home Assistant

This custom Home Assistant sensor component scrapes electricity prices from the RAE (Regulatory Authority for Energy) website (https://invoices.rae.gr/oikiako/) and provides the price as a sensor in Home Assistant.

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
    url: "https://invoices.rae.gr/%CE%BA%CE%B1%CF%84%CE%B1%CF%87%CF%89%CF%81%CE%B7%CE%BC%CE%AD%CE%BD%CE%B1-%CF%84%CE%B9%CE%BC%CE%BF%CE%BB%CF%8C%CE%B3%CE%B9%CE%B1-%CF%80%CF%81%CE%BF%CE%BC%CE%AE%CE%B8%CE%B5%CE%B9%CE%B1%CF%82-%CE%B7-3/"
    discounted_price: "Y"
    scan_interval: 86400
```

## Usage

Once configured and Home Assistant has been restarted, the sensor will appear in your Home Assistant instance as sensor.rae_price_per_kwh. It will update once every day by default, but you can modify the scan_interval to change the update frequency.
