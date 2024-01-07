import logging
import requests
from bs4 import BeautifulSoup
import datetime
import urllib3
from homeassistant.helpers.entity import Entity
from homeassistant.components.sensor import PLATFORM_SCHEMA
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

# Define the domain of your component
DOMAIN = 'rae_price_scraper'

# Define your configuration schema
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required('provider_filter'): cv.string,
    vol.Required('plan_filter'): cv.string,
    vol.Required('url'): cv.url,
})

def setup_platform(hass, config, add_entities, discovery_info=None):
    """Setup the sensor platform."""
    provider = config['provider_filter']
    plan = config['plan_filter']
    url = config['url']
    add_entities([RAEPriceSensor(provider, plan, url)], True)

class RAEPriceSensor(Entity):
    """Representation of the RAE Price Sensor."""

    def __init__(self, provider, plan, url):
        """Initialize the sensor."""
        self._provider = provider
        self._plan = plan
        self._url = url
        self._state = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return 'rae_price_per_kwh'

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "EUR/kWh"

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    def update(self):
        """Fetch new state data for the sensor."""
        month_filter = str(datetime.datetime.now().month)
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        final_price = None  # Initialize final_price as None

        try:
            response = requests.get(self._url, verify=False)
            response.encoding = 'utf-8'

            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                table = soup.find('table', id='billing_table')

                if table:
                    data = []
                    headers = [header.text for header in table.find_all('th')]

                    for row in table.find_all('tr'):
                        cells = row.find_all('td')

                        if cells:
                            row_data = {headers[i]: cell.text.strip() for i, cell in enumerate(cells)}
                            data.append(row_data)

                    for item in data:
                        if (item.get("Πάροχος") == self._provider and
                            item.get("Μήνας") == month_filter and
                            item.get("Ονομασία Τιμολογίου") == self._plan):
                            final_price = float(item.get("Τελική Τιμή Προμήθειας (€/MWh)")) / 1000
                            print (final_price)
                            break
        except requests.ConnectionError as e:
            _LOGGER.error("rae_price_scraper: Error connecting to RAE: %s", e)
        except Exception as e:
            _LOGGER.error("rae_price_scraper: Error fetching data from RAE: %s", e)

        self._state = final_price if final_price is not None else 'Unavailable'
        if final_price is not None:
            _LOGGER.info("rae_price_scraper: Updated RAE price per kWh: EUR %.3f", final_price)
