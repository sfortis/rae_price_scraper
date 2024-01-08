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

DOMAIN = 'rae_price_scraper'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required('provider_filter'): cv.string,
    vol.Required('plan_filter'): cv.string,
    vol.Required('url'): cv.url,
    vol.Optional('discounted_price', default="Y"): cv.string,
})

def setup_platform(hass, config, add_entities, discovery_info=None):
    provider = config['provider_filter']
    plan = config['plan_filter']
    url = config['url']
    discounted_price = config.get('discounted_price', "Y")
    add_entities([RAEPriceSensor(provider, plan, url, discounted_price)], True)

class RAEPriceSensor(Entity):
    def __init__(self, provider, plan, url, discounted_price):
        self._provider = provider
        self._plan = plan
        self._url = url
        self._discounted_price = discounted_price 
        self._state = None  # Initial state is None, which might represent no data available yet
        self._initialized = False  # Flag to check if the sensor ever got a valid update

    @property
    def name(self):
        return 'rae_price_per_kwh'

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return "EUR/kWh"

    @property
    def should_poll(self):
        return True

    def update(self):
        month_filter = str(datetime.datetime.now().month)
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        final_price = None  # Temporary variable for the new price

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
                            if self._discounted_price == "Y":
                                price_element = "Τελική Τιμή Προμήθειας με Έκπτωση με προϋπόθεση (€/MWh)"
                            else:  # default or if "N"
                                price_element = "Τελική Τιμή Προμήθειας (€/MWh)"
                            final_price = float(item.get(price_element)) / 1000
                            break

            # Only update the state and initialized flag if a new price is successfully retrieved
            if final_price is not None:
                self._state = f"{final_price:.3f}"
                self._initialized = True
                _LOGGER.info("rae_price_scraper: Updated RAE price per kWh: EUR %.3f", final_price)

        except requests.ConnectionError as e:
            _LOGGER.error("rae_price_scraper: Error connecting to RAE: %s", e)
        except Exception as e:
            _LOGGER.error("rae_price_scraper: Error fetching data from RAE: %s", e)

        # If the sensor has never been initialized successfully and no price was retrieved, it remains 'Unavailable'
        if not self._initialized and self._state is None:
            self._state = 'Unavailable'
