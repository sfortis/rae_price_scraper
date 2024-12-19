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
    _LOGGER.info("Setting up RAE Price sensor with: provider=%s, plan=%s", provider, plan)
    add_entities([RAEPriceSensor(provider, plan, url, discounted_price)], True)

class RAEPriceSensor(Entity):
    def __init__(self, provider, plan, url, discounted_price):
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

    @property
    def extra_state_attributes(self):
        return {
            'last_found_month': self._last_found_month,
            'provider': self._provider,
            'plan': self._plan,
            'discounted_price': self._discounted_price
        }

    def _get_previous_month(self, current_date):
        if current_date.month == 1:
            return current_date.replace(year=current_date.year - 1, month=12, day=1)
        return current_date.replace(month=current_date.month - 1, day=1)

    def _search_price_in_data(self, data, month_filter):
        for item in data:
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
                    price = float(item.get(price_element))
                    _LOGGER.info("Found price %.5f EUR/kWh for month %s", price, month_filter)
                    return price, int(month_filter)
                except (ValueError, TypeError) as e:
                    _LOGGER.error("Error converting price value: %s. Raw value: %s", 
                                e, item.get(price_element))
        
        _LOGGER.info("No price found for month %s", month_filter)
        return None, None

    def update(self):
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
                rows = soup.find_all('tr', attrs={'data-invoice-id': True})
                data = []

                for row in rows:
                    tds = row.find_all('td')
                    if len(tds) < 4:
                        continue

                    provider = tds[0].get_text(strip=True)
                    year = tds[1].get_text(strip=True)
                    month = tds[2].get_text(strip=True)
                    plan = tds[3].get_text(strip=True)

                    off_price_td = row.find('td', class_='checkbox_ekptosi_off teliki_timi_promithias')
                    on_price_td = row.find('td', class_='checkbox_ekptosi_on teliki_timi_promithias_meta_apo_ekptoseis_promithias')

                    off_price = off_price_td.get_text(strip=True) if off_price_td else None
                    on_price = on_price_td.get_text(strip=True) if on_price_td else None

                    item = {
                        "Πάροχος": provider,
                        "Έτος": year,
                        "Μήνας": month,
                        "Ονομασία Τιμολογίου": plan,
                        "Τελική Τιμή Προμήθειας (€/MWh)": off_price,
                        "Τελική Τιμή Προμήθειας με Έκπτωση με προϋπόθεση (€/MWh)": on_price
                    }
                    data.append(item)

                _LOGGER.info("Successfully parsed %d rows", len(data))

                current_date = datetime.datetime.now()
                search_date = current_date
                max_attempts = 12
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
                    self._state = f"{final_price:.5f}"
                    self._initialized = True
                    self._last_found_month = found_month
                    _LOGGER.info("Successfully updated RAE price: %.5f EUR/kWh (Month: %d)", 
                                 final_price, found_month)
                else:
                    _LOGGER.warning("No price found in the last %d months", max_attempts)
            else:
                _LOGGER.error("Failed to fetch data. Status code: %d", response.status_code)

        except requests.ConnectionError as e:
            _LOGGER.error("Connection error while fetching RAE data: %s", e)
        except requests.RequestException as e:
            _LOGGER.error("Request error while fetching RAE data: %s", e)
        except Exception as e:
            _LOGGER.error("Unexpected error while fetching RAE data: %s", e, exc_info=True)

        if not self._initialized and self._state is None:
            self._state = 'Unavailable'
            _LOGGER.warning("Sensor remains uninitialized, setting state to 'Unavailable'")

        _LOGGER.info("Update cycle completed. Final state: %s", self._state)
