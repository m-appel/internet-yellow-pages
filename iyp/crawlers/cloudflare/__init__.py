import json
import logging
import os
from concurrent.futures import as_completed
from datetime import datetime, timezone

from requests.adapters import HTTPAdapter, Retry
from requests_futures.sessions import FuturesSession

from iyp import BaseCrawler

BATCH_SIZE = 10
RANK_THRESHOLD = 10000
TOP_LIMIT = 100
PARALLEL_DOWNLOADS = 4

API_KEY = str()
if os.path.exists('config.json'):
    config = json.load(open('config.json', 'r'))
    API_KEY = config['cloudflare']['apikey']

# Cloudflare radar's top location and ASes is available for both domain names
# and host names. Results are likely accounting for all NS, A, AAAA queries made to
# Cloudflare's resolver. Since NS queries for host names make no sense it seems
# more intuitive to link these results to DomainName nodes.


class DnsTopCrawler(BaseCrawler):
    def __init__(self, organization, url, name):
        super().__init__(organization, url, name)

        # Fetch domain names registered in IYP
        existing_dn = self.iyp.tx.run(
            """MATCH (dn:DomainName)-[r:RANK]-(:Ranking)
                WHERE r.rank <= $rank_threshold
                RETURN elementId(dn) AS _id, dn.name AS dname""",
            rank_threshold=RANK_THRESHOLD)

        self.domain_names_id = {node['dname']: node['_id'] for node in existing_dn}

        # TODO Fetching data for HostName nodes does not scale at the moment.
        self.host_names_id = dict()
        # existing_hn = self.iyp.tx.run(
        #     """MATCH (hn:HostName)-[r:RANK]-(:Ranking)
        #         WHERE r.rank <= $rank_threshold
        #         RETURN elementId(hn) AS _id, hn.name AS hname""",
        #     rank_threshold=RANK_THRESHOLD)
        # self.host_names_id = {node['hname']: node['_id'] for node in existing_hn}

        # There might be overlap between these two, but we don't want to fetch the same
        # data twice.
        self.names = list(sorted(set(self.domain_names_id.keys()).union(self.host_names_id.keys())))
        # Contains unique values that connect to the names, depending on the crawler.
        # ASNs for top_ases, country codes for top_locations.
        self.to_nodes = set()
        self.links = list()

    def fetch(self):
        """Download data for top RANK_THRESHOLD domain names registered in IYP and save
        it on disk."""

        req_session = FuturesSession(max_workers=PARALLEL_DOWNLOADS)
        # Set API authentication headers.
        req_session.headers['Authorization'] = 'Bearer ' + API_KEY
        req_session.headers['Content-Type'] = 'application/json'

        retries = Retry(total=10,
                        backoff_factor=0.1,
                        status_forcelist=[500, 502, 503, 504])

        req_session.mount('https://', HTTPAdapter(max_retries=retries))

        # Clear the cache.
        tmp_dir = self.create_tmp_dir()

        queries = list()

        # Query Cloudflare API in batches.
        i = 0
        batch = list()
        fpaths = dict()
        while i < len(self.names):
            name = self.names[i]
            i += 1
            # Do not override existing files.
            fname = f'data_{name}.json'
            fpath = os.path.join(tmp_dir, fname)

            if os.path.exists(fpath):
                continue

            fpaths[name] = fpath

            batch.append(name)
            if len(batch) < BATCH_SIZE and i < len(self.names):
                # Batch not yet full and not in last iteration.
                continue

            if not batch:
                # If the number of batches perfectly lines up, we do not want to send a
                # broken request without names.
                break

            get_params = f'?limit={TOP_LIMIT}'
            for domain in batch:
                get_params += f'&dateRange=7d&domain={domain}'

            url = self.url + get_params
            future = req_session.get(url)
            future.domains = batch
            future.fpaths = fpaths
            queries.append(future)
            batch = list()
            fpaths = dict()

        for query in as_completed(queries):
            try:
                res = query.result()
                res.raise_for_status()
                # Confirm JSON integrity.
                data = res.json()
                if not data['success']:
                    raise ValueError('Response contains success=False')

                data = data['result']

                if not self.reference['reference_time_modification']:
                    # Get the reference time from the first file.
                    try:
                        date_str = data['meta']['dateRange'][0]['endTime']
                        date = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                        self.reference['reference_time_modification'] = date
                    except (KeyError, ValueError, TypeError) as e:
                        logging.warning(f'Failed to get modification time: {e}')

                for idx, (placeholder, result) in enumerate(data.items()):
                    if placeholder == 'meta':
                        continue
                    domain = query.domains[idx]
                    with open(query.fpaths[domain], 'w') as fp:
                        json.dump({domain: result}, fp)

            except Exception as e:
                logging.error(f'Failed to fetch data for domains: {query.domains}: {e}')
                continue

    def run(self):
        self.fetch()

        tmp_dir = self.get_tmp_dir()

        for entry in os.scandir(tmp_dir):
            if not entry.is_file() or not entry.name.endswith('.json'):
                continue
            file = os.path.join(tmp_dir, entry.name)

            with open(file, 'rb') as fp:
                results = json.load(fp)
                if not results:
                    continue

                for domain_top in results.items():
                    self.compute_link(domain_top)

        self.map_links()

        self.iyp.batch_add_links('QUERIED_FROM', self.links)

    def compute_link(self, param):
        """Create link entries for result."""
        raise NotImplementedError()

    def map_links(self):
        """Fetch/create destination nodes of links and replace link destination with
        QID."""
        raise NotImplementedError()

    def unit_test(self):
        return super().unit_test(['QUERIED_FROM'])
