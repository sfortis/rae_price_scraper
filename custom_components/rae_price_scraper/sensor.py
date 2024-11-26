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
    """Set up the RAE Price sensor platform."""
    provider = config['provider_filter']
    plan = config['plan_filter']
    url = config['url']
    discounted_price = config.get('discounted_price', "Y")
    _LOGGER.info("Setting up RAE Price sensor with: provider=%s, plan=%s", provider, plan)
    add_entities([RAEPriceSensor(provider, plan, url, discounted_price)], True)

class RAEPriceSensor(Entity):
    """Implementation of a RAE Price sensor."""

    def __init__(self, provider, plan, url, discounted_price):
        """Initialize the sensor."""
        self._provider = provider
        self._plan = plan
        self._url = url
        self._discounted_price = discounted_price 
        self._state = None
        self._initialized = False
        self._last_found_month = None
        self._attributes = {}

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
        """Polling is needed."""
        return True

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            'last_found_month': self._last_found_month,
            'provider': self._provider,
            'plan': self._plan,
            'discounted_price': self._discounted_price
        }

    def _get_previous_month(self, current_date):
        """Get the first day of the previous month."""
        if current_date.month == 1:
            return current_date.replace(year=current_date.year - 1, month=12, day=1)
        return current_date.replace(month=current_date.month - 1, day=1)

    def _search_price_in_data(self, data, month_filter):
        """Search for price in the parsed data for a specific month."""
        for item in data:
            _LOGGER.info("Checking price for month %s", month_filter)
            if (item.get("Πάροχος") == self._provider and
                item.get("Μήνας") == month_filter and
                item.get("Ονομασία Τιμολογίου") == self._plan):
                
                if self._discounted_price == "Y":
                    price_element = "Τελική Τιμή Προμήθειας με Έκπτωση με προϋπόθεση (€/MWh)"
                    _LOGGER.info("Using discounted price element")
                else:
                    price_element = "Τελική Τιμή Προμήθειας (€/MWh)"
                    _LOGGER.info("Using standard price element")
                
                try:
                    price = float(item.get(price_element)) / 1000
                    _LOGGER.info("Found price %.3f EUR/kWh for month %s", price, month_filter)
                    return price, int(month_filter)
                except (ValueError, TypeError) as e:
                    _LOGGER.error("Error converting price value: %s. Raw value: %s", 
                                e, item.get(price_element))
        
        _LOGGER.info("No price found for month %s", month_filter)
        return None, None

    def update(self):
        """Fetch new state data for the sensor."""
        _LOGGER.info("Starting update for RAE Price sensor")
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        final_price = None
        found_month = None

        try:
            _LOGGER.info("Fetching data from RAE website: %s", self._url)
            response = requests.get(self._url, verify=False)
            response.encoding = 'utf-8'

            if response.status_code == 200:
                _LOGGER.info("Successfully retrieved data from RAE website")
                soup = BeautifulSoup(response.content, 'html.parser')
                table = soup.find('table', id='billing_table')

                if table:
                    _LOGGER.info("Found billing table in HTML response")
                    data = []
                    headers = [header.text for header in table.find_all('th')]

                    for row in table.find_all('tr'):
                        cells = row.find_all('td')
                        if cells:
                            row_data = {headers[i]: cell.text.strip() for i, cell in enumerate(cells)}
                            data.append(row_data)

                    _LOGGER.info("Successfully parsed %d rows from table", len(data))

                    # Start with current month and go backwards until price is found
                    current_date = datetime.datetime.now()
                    search_date = current_date
                    max_attempts = 12  # Limit search to 12 months back
                    attempts = 0

                    while attempts < max_attempts and final_price is None:
                        month_filter = str(search_date.month)
                        _LOGGER.info("Searching for price in month %s (attempt %d/%d)", 
                                   month_filter, attempts + 1, max_attempts)
                        
                        final_price, found_month = self._search_price_in_data(data, month_filter)
                        
                        if final_price is None:
                            search_date = self._get_previous_month(search_date)
                            attempts += 1
                        else:
                            break

                    if final_price is not None:
                        self._state = f"{final_price:.3f}"
                        self._initialized = True
                        self._last_found_month = found_month
                        _LOGGER.info("Successfully updated RAE price: %.3f EUR/kWh (Month: %d)", 
                                   final_price, found_month)
                    else:
                        _LOGGER.warning("No price found in the last %d months", max_attempts)
                else:
                    _LOGGER.error("Billing table not found in HTML response")
            else:
                _LOGGER.error("Failed to fetch data. Status code: %d", response.status_code)

        except requests.ConnectionError as e:
            _LOGGER.error("Connection error while fetching RAE data: %s", e)
        except requests.RequestException as e:
            _LOGGER.error("Request error while fetching RAE data: %s", e)
        except Exception as e:
            _LOGGER.error("Unexpected error while fetching RAE data: %s", e, exc_info=True)

        # If the sensor has never been initialized successfully and no price was retrieved
        if not self._initialized and self._state is None:
            self._state = 'Unavailable'
            _LOGGER.warning("Sensor remains uninitialized, setting state to 'Unavailable'")

        _LOGGER.info("Update cycle completed. Final state: %s", self._state)
