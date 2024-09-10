from __future__ import annotations

import json
import logging
from json.decoder import JSONDecodeError
from time import sleep
from urllib.error import URLError, HTTPError
from urllib.request import urlopen
from urllib.parse import urlencode, urljoin

from .enums import ItemType
from .exceptions import EndpointError, RequestError, UnsupportedRegion
from .database import Database
from .url_util import GachaUrl


class Client:
    API_BASE_URL = 'https://api-os-takumi.mihoyo.com/common/gacha_record/api'

    def __init__(self):
        self._database = Database()

    def _request(self, endpoint: str, params: dict[str, str | int]):
        logging.info('Requesting endpoint %s', endpoint)
        try:
            with urlopen('{}?{}'.format(endpoint, urlencode(params))) as request:
                result = request.read()
        except (URLError, HTTPError) as err:
            logging.error("Request error", err)
            raise EndpointError('Error making request.')

        try:
            result = json.loads(result)
        except JSONDecodeError as err:
            logging.error("Decode error", err)
            raise RequestError('Error parsing request result as JSON.')

        if 'retcode' not in result:
            logging.error('Response had no "retcode" field')
            raise EndpointError('Malformed response from endpoint.')

        if result['retcode'] != 0:
            pretty_message = result['message'][0].upper() + result['message'][1:] + '.'
            logging.error("Endpoint returned message: %s", pretty_message)
            raise EndpointError(pretty_message)

        return result['data']

    def _fetch_warp_history(self, url: GachaUrl, banner_type: int, end_id=None):
        if url.region != 'hkrpg_global':
            raise UnsupportedRegion('Unsupported region.')

        endpoint = urljoin(self.API_BASE_URL, 'getGachaLog')
        params = {
            'gacha_type': banner_type,
            'size': 20,
            'authkey': url.auth_token,
            'lang': 'en',
            'game_biz': 'hkrpg_global',
            'authkey_ver': 1,
        }

        if end_id is not None:
            params['end_id'] = end_id

        latest_warp_id = None
        while result := self._request(endpoint, params):
            if not result['list']:
                break
            end_id = None
            for warp in result['list']:
                # get the latest warp and store it;
                # this is the earliest point we can do this, because
                # only when we start fetching warp history from
                # mihoyo's API will we get the UID for our auth token
                if latest_warp_id is None:
                    latest_warp_id = self._database.get_latest_warp_id(warp['uid'], banner_type)
                    logging.debug('Latest warp id for banner type %d is %d', banner_type, latest_warp_id or 0)

                # return when we reach the latest warp we already have in our history
                logging.debug('Current warp id is %s. (%s - %s)', warp['id'], warp['time'], warp['name'])
                if latest_warp_id is not None and latest_warp_id == int(warp['id']):
                    logging.debug('Current id and last id match, returning')
                    return

                yield warp
                end_id = warp['id']

            params['end_id'] = end_id
            sleep(0.1)  # reasonable delay..?

    @staticmethod
    def get_banner_types() -> dict[int, str]:
        return {
            1: 'Stellar Warp',
            2: 'Departure Warp',
            11: 'Character Event Warp',
            12: 'Light Cone Event Warp'
        }

    def fetch_and_store_warp_history(self, url: GachaUrl):
        logging.info('Fetching warp history')
        new_warps_count = 0
        for banner_type in self.get_banner_types().keys():
            logging.info('Fetching warp history for banner type %s', banner_type)
            warps = []
            for warp in self._fetch_warp_history(url, banner_type):
                warps.append({
                    'id': int(warp['id']),  # convert to int for proper sorting
                    'uid': int(warp['uid']),
                    'banner_id': int(warp['gacha_id']),
                    'banner_type': banner_type,
                    'type': ItemType.CHARACTER if warp['item_type'] == 'Character' else ItemType.LIGHTCONE,
                    'rarity': int(warp['rank_type']),
                    'time': warp['time'],
                    'item_id': int(warp['item_id']),
                    'name': warp['name']
                })

            logging.info('Got %d warps', len(warps))  # TODO: log how many warps we actually _stored_ (after implementing fetching missing warps and de-duplication)
            new_warps_count += len(warps)
            warps.sort(key=lambda warp: warp['id'])
            self._database.store_warp_history(warps)

        return new_warps_count

    def get_uids(self):
        return self._database.get_uids()

    def get_warp_history(self, uid):
        return self._database.get_warp_history(uid)
