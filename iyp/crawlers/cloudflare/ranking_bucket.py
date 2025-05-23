import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter, Retry

from iyp import BaseCrawler, JSONDecodeError

# Organization name and URL to data
ORG = 'Cloudflare'
URL_DATASETS = 'https://api.cloudflare.com/client/v4/radar/datasets?limit=10&offset=0&datasetType=RANKING_BUCKET&format=json'  # noqa: E501
URL = 'https://api.cloudflare.com/client/v4/radar/datasets'
URL_DL = 'https://api.cloudflare.com/client/v4/radar/datasets/download'
NAME = 'cloudflare.ranking_bucket'

API_KEY = ''
if os.path.exists('config.json'):
    API_KEY = json.load(open('config.json', 'r'))['cloudflare']['apikey']


class Crawler(BaseCrawler):
    # Base Crawler provides access to IYP via self.iyp and setup a dictionary with the
    # org/url/today's date in self.reference
    #
    # Cloudflare ranks second and third level domain names (not host names).
    # See https://blog.cloudflare.com/radar-domain-rankings/
    def __init__(self, organization, url, name):
        super().__init__(organization, url, name)
        self.reference['reference_url_info'] = 'https://developers.cloudflare.com/radar/investigate/domain-ranking-datasets/'  # noqa: E501

    def run(self):
        """Fetch data and push to IYP."""

        # setup HTTPS session with credentials and retry
        req_session = requests.Session()
        req_session.headers['Authorization'] = 'Bearer ' + API_KEY
        req_session.headers['Content-Type'] = 'application/json'

        retries = Retry(total=5,
                        backoff_factor=0.1,
                        status_forcelist=[500, 502, 503, 504])

        req_session.mount('http://', HTTPAdapter(max_retries=retries))
        req_session.mount('https://', HTTPAdapter(max_retries=retries))

        # Fetch rankings descriptions
        logging.info('Fetching datasets and dataset data.')
        req = req_session.get(URL_DATASETS)
        req.raise_for_status()

        datasets_json = req.json()
        if 'success' not in datasets_json or not datasets_json['success']:
            logging.error(f'HTTP request succeeded but API returned: {req.text}')
            raise JSONDecodeError('Error while fetching data file')

        # Fetch all datasets first before starting to process them. This way we can
        # get/create all DomainName nodes in one go and then just add the RANK
        # relationships per dataset.
        datasets = list()
        all_domains = set()
        for dataset in datasets_json['result']['datasets']:
            if not self.reference['reference_time_modification']:
                # Get modification time from first dataset. Should be the same for all
                # datasets.
                try:
                    date_str = dataset['meta']['targetDateEnd']
                    date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                    self.reference['reference_time_modification'] = date
                except (KeyError, ValueError, TypeError) as e:
                    logging.warning(f'Failed to get modification time: {e}')

            # Get the dataset URL
            req = req_session.post(URL_DL, json={'datasetId': dataset['id']})
            if req.status_code != 200:
                logging.error(f'Cannot get url for dataset {dataset["id"]} {req.status_code}: {req.text}')
                continue

            logging.info(req.json())

            dataset['url'] = req.json()['result']['dataset']['url']
            req = requests.get(dataset['url'])
            if req.status_code != 200:
                logging.error(f'Cannot download dataset {dataset["id"]} {req.status_code}: {req.text}')
                continue

            # Read top list and skip header
            dataset_domains = set(req.text.splitlines()[1:])
            all_domains.update(dataset_domains)
            datasets.append((dataset, dataset_domains))

        # Get or create nodes for domains and retrieve their IDs.
        # Note: Since we do not specify all=False in batch_get_nodes we will get the IDs
        # of _all_ DomainName nodes, so we must not create relationships for all
        # domain_ids, but iterate over the domains set instead.
        domain_ids = self.iyp.batch_get_nodes_by_single_prop('DomainName', 'name', all_domains)

        for dataset, domains in datasets:
            dataset_title = f'Cloudflare {dataset["title"]}'
            logging.info(f'Processing dataset: {dataset_title}')
            ranking_id = self.iyp.get_node('Ranking',
                                           {
                                               'name': dataset_title,
                                               'description': dataset['description'],
                                               'top': dataset['meta']['top']
                                           },
                                           id_properties={'name'})

            # Create RANK relationships
            domain_links = [{'src_id': domain_ids[domain], 'dst_id': ranking_id, 'props': [self.reference]}
                            for domain in domains]
            if domain_links:
                # Push RANK relationships to IYP
                self.iyp.batch_add_links('RANK', domain_links)

    def unit_test(self):
        return super().unit_test(['RANK'])


# Main program
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--unit-test', action='store_true')
    args = parser.parse_args()

    FORMAT = '%(asctime)s %(processName)s %(message)s'
    logging.basicConfig(
        format=FORMAT,
        filename=f'log/{NAME}.log',
        level=logging.INFO,
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logging.info(f'Started: {sys.argv}')

    crawler = Crawler(ORG, URL, NAME)
    if args.unit_test:
        crawler.unit_test()
    else:
        crawler.run()
        crawler.close()
    logging.info(f'Finished: {sys.argv}')
