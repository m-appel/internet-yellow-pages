import os
import sys
import json
import logging
import requests
from iyp import BaseCrawler

# Organization name and URL to data
ORG = 'Cloudflare'
URL = 'https://api.cloudflare.com/client/v4/radar/ranking/top?name=top&limit=100&format=json'

API_KEY = ''
if os.path.exists('config.json'): 
    API_KEY = json.load(open('config.json', 'r'))['cloudflare']['apikey']

class Crawler(BaseCrawler):
    # Base Crawler provides access to IYP via self.iyp
    # and setup a dictionary with the org/url/today's date in self.reference

    def run(self):
        """Fetch data and push to IYP. """

        self.cf_qid = self.iyp.get_node(
                'RANKING', {'name': f'Cloudflare top 100 domains'}, create=True)

        # Fetch data
        headers = {
                'Authorization': 'Bearer '+API_KEY,
                'Content-Type': 'application/json'
                }

        req = requests.get(self.reference['reference_url'], headers=headers)
        if req.status_code != 200:
            print(f'Cannot download data {req.status_code}: {req.text}')
            sys.exit('Error while fetching data file')

        # Process line one after the other
        for i, _ in enumerate(map(self.update, req.json()['result']['top'])):
            sys.stderr.write(f'\rProcessed {i} lines')
        sys.stderr.write('\n')
    
    def update(self, entry):
        """Add the entry to IYP if it's not already there and update its
        properties."""

        # set rank
        statements = [[ 'RANK', self.cf_qid, dict({'rank': entry['rank']}, **self.reference) ]]

        # Commit to IYP
        # Get the AS's node ID (create if it is not yet registered) and commit changes
        domain_qid = self.iyp.get_node('DOMAIN_NAME', {'name': entry['domain']}, create=True) 
        self.iyp.add_links( domain_qid, statements )
        
# Main program
if __name__ == '__main__':

    scriptname = sys.argv[0].replace('/','_')[0:-3]
    FORMAT = '%(asctime)s %(processName)s %(message)s'
    logging.basicConfig(
            format=FORMAT, 
            filename='log/'+scriptname+'.log',
            level=logging.WARNING, 
            datefmt='%Y-%m-%d %H:%M:%S'
            )
    logging.info("Started: %s" % sys.argv)

    crawler = Crawler(ORG, URL)
    crawler.run()
    crawler.close()