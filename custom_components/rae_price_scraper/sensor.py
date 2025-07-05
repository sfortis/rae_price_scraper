import logging
import requests
from bs4 import BeautifulSoup
import datetime
import urllib3
from homeassistant.helpers.entity import Entity
from homeassistant.components.sensor import PLATFORM_SCHEMA
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
import time
import random
import pickle
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
        # Cookie file path in HA config directory
        self._cookie_file = os.path.join(os.path.dirname(__file__), 'rae_cookies.pkl')

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
            'discounted_price': self._discounted_price,
            'last_update': datetime.datetime.now().isoformat()
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
                    _LOGGER.debug("Using discounted price element")
                else:
                    price_element = "Τελική Τιμή Προμήθειας (€/MWh)"
                    _LOGGER.debug("Using standard price element")
                
                try:
                    price = float(item.get(price_element))
                    _LOGGER.info("Found price %.5f EUR/kWh for month %s", price, month_filter)
                    return price, int(month_filter)
                except (ValueError, TypeError) as e:
                    _LOGGER.error("Error converting price value: %s. Raw value: %s", 
                                e, item.get(price_element))
        
        _LOGGER.debug("No price found for month %s", month_filter)
        return None, None

    def _fetch_with_session_persistence(self):
        """Fetch using session persistence method that bypasses Incapsula"""
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        # Create session with retry strategy
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        
        # Load saved cookies if they exist
        if os.path.exists(self._cookie_file):
            try:
                with open(self._cookie_file, 'rb') as f:
                    cookies = pickle.load(f)
                    session.cookies.update(cookies)
                    _LOGGER.debug("Loaded saved cookies")
            except Exception as e:
                _LOGGER.warning("Could not load saved cookies: %s", e)
        
        # Set browser-like headers
        session.headers = {
            'Host': 'energycost.gr',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'el-GR,el;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        base_url = "https://energycost.gr"
        
        try:
            # Step 1: Visit homepage first (like a real user)
            _LOGGER.info("Visiting homepage first...")
            session.headers['Sec-Fetch-Site'] = 'none'
            session.headers['Sec-Fetch-Mode'] = 'navigate'
            session.headers['Sec-Fetch-Dest'] = 'document'
            session.headers['Sec-Fetch-User'] = '?1'
            
            resp1 = session.get(base_url, timeout=30, verify=False, allow_redirects=True)
            _LOGGER.debug("Homepage visit status: %d", resp1.status_code)
            
            # Save cookies for future use
            try:
                with open(self._cookie_file, 'wb') as f:
                    pickle.dump(session.cookies, f)
                    _LOGGER.debug("Saved cookies for future use")
            except Exception as e:
                _LOGGER.warning("Could not save cookies: %s", e)
            
            # Wait like a human (random delay between 2-4 seconds)
            wait_time = random.uniform(2, 4)
            _LOGGER.debug("Waiting %.1f seconds before navigating to pricing page...", wait_time)
            time.sleep(wait_time)
            
            # Step 2: Navigate to target page
            _LOGGER.info("Navigating to pricing page...")
            session.headers['Referer'] = base_url
            session.headers['Sec-Fetch-Site'] = 'same-origin'
            
            resp2 = session.get(self._url, timeout=30, verify=False, allow_redirects=True)
            _LOGGER.debug("Pricing page status: %d, length: %d", resp2.status_code, len(resp2.text))
            
            if resp2.status_code == 200:
                if 'data-invoice-id' in resp2.text:
                    _LOGGER.info("Successfully retrieved data with session persistence method")
                    return resp2
                else:
                    # If we got a challenge, wait and retry once
                    if 'incapsula' in resp2.text.lower() and '_Incapsula_Resource' in resp2.text:
                        _LOGGER.info("Incapsula challenge detected, retrying after delay...")
                        time.sleep(5)
                        
                        resp3 = session.get(self._url, timeout=30, verify=False)
                        if 'data-invoice-id' in resp3.text:
                            _LOGGER.info("Successfully retrieved data on retry")
                            return resp3
                    
                    _LOGGER.error("No invoice data found in response")
                    return None
            else:
                _LOGGER.error("Failed to fetch data. Status code: %d", resp2.status_code)
                return None
                
        except Exception as e:
            _LOGGER.error("Error during fetch: %s", e)
            return None

    def update(self):
        """Update sensor data"""
        _LOGGER.info("Starting update for RAE Price sensor")
        final_price = None
        found_month = None

        try:
            # Fetch data using session persistence method
            response = self._fetch_with_session_persistence()
            
            if response and response.status_code == 200:
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

                if len(data) == 0:
                    _LOGGER.error("No data rows found. The website might have changed.")
                    return

                # Search for price in recent months
                current_date = datetime.datetime.now()
                search_date = current_date
                max_attempts = 12
                attempts = 0

                while attempts < max_attempts and final_price is None:
                    month_filter = str(search_date.month)
                    _LOGGER.debug("Searching for price in month %s (attempt %d/%d)",
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
                _LOGGER.error("Failed to fetch data from RAE website")

        except Exception as e:
            _LOGGER.error("Unexpected error while fetching RAE data: %s", e, exc_info=True)

        if not self._initialized and self._state is None:
            self._state = 'Unavailable'
            _LOGGER.warning("Sensor remains uninitialized, setting state to 'Unavailable'")

        _LOGGER.info("Update cycle completed. Final state: %s", self._state)