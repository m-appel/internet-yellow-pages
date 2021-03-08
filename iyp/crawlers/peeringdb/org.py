import sys
import logging
import json
import iso3166
from iyp.wiki.wikihandy import Wikihandy
import requests

# URL to peeringdb API for organizations
URL_PDB_ORGS = 'https://peeringdb.com/api/org'

# Label used for the class/item representing the organization IDs
ORGID_LABEL = 'PeeringDB organization ID' 

class Crawler(object):
    def __init__(self):
        """Create an item representing the 'PeeringDB organization ID' class if 
        doesn't already exist. And fetch QIDs for organizations already in the
        wikibase."""
    
        sys.stderr.write('Initialization...\n')

        # Helper for wiki access
        self.wh = Wikihandy()

        # Get the QID for the item representing the organization IDs
        orgid_qid = self.wh.get_qid(ORGID_LABEL,
                create={                                     # Create it if it doesn't exist
                    'summary': 'add PeeringDB org IDs',      # Commit message
                    'description': 'Identifier for an organization in the PeeringDB database'
                    })

        # Load the QIDs for organizations already available in the wikibase
        self.orgid2qid = self.wh.extid2qid(qid=orgid_qid)

        # Added properties will have this reference information
        today = self.wh.today()
        self.reference = [
                (self.wh.get_pid('source'), self.wh.get_qid('PeeringDB')),
                (self.wh.get_pid('reference URL'), URL_PDB_ORGS),
                (self.wh.get_pid('point in time'), today)
                ]

    def run(self):
        """Fetch organizations information from PeeringDB and push to wikibase"""

        sys.stderr.write('Fetching PeeringDB data...\n')
        req = requests.get(URL_PDB_ORGS)
        if req.status_code != 200:
            sys.exit('Error while fetching AS names')
        organizations = json.loads(req.text)['data']

        self.wh.login() # Login once for all threads

        for i, res in enumerate(map(self.update_org, organizations)):
            sys.stderr.write(f'\rProcessing... {i+1}/{len(organizations)}')

    def update_org(self, organization):
        """Add the organization to wikibase if it's not there and update properties"""

        # set property name
        statements = [ 
                [self.wh.get_pid('instance of'), self.wh.get_qid('organization')],
                [self.wh.get_pid('name'), organization['name'].strip(), self.reference] ] 

        # set property website
        if organization['website']:
            statements.append([ self.wh.get_pid('website'), organization['website'], self.reference])

        # set property country
        if organization['country'] in iso3166.countries_by_alpha2:
            country_qid = self.wh.get_qid(iso3166.countries_by_alpha2[organization['country']].name)
            if country_qid is not None:
                statements.append([self.wh.get_pid('country'), country_qid, self.reference])

        # Update name, website, and country for this organization
        org_qid = self.org_qid(organization)
        self.wh.upsert_statements('update peeringDB organization', org_qid, statements )
        
        return org_qid

    def org_qid(self, organization):
        """Find the organization QID or add it to wikibase if it is not yet there.
        Return the organization QID."""

        # Check if the organization is in the wikibase
        if str(organization['id']) not in self.orgid2qid :
            # Set properties for this new organization
            org_qualifier = [
                    (self.wh.get_pid('instance of'), self.wh.get_qid(ORGID_LABEL)),
                    ]
            statements = [ [self.wh.get_pid('external ID'), str(organization['id']), [], org_qualifier] ]

            # Add this organization to the wikibase
            org_qid = self.wh.add_item('add new peeringDB organization', 
                    label=organization['name'],statements=statements)
            # keep track of this QID
            self.orgid2qid[str(organization['id'])] = org_qid

        return self.orgid2qid[str(organization['id'])]


# Main program
if __name__ == '__main__':

    scriptname = sys.argv[0].replace('/','_')[0:-3]
    FORMAT = '%(asctime)s %(processName)s %(message)s'
    logging.basicConfig(
            format=FORMAT, 
            filename='log/'+scriptname+'.log',
            level=logging.INFO, 
            datefmt='%Y-%m-%d %H:%M:%S'
            )
    logging.info("Started: %s" % sys.argv)

    pdbo = Crawler()
    pdbo.run()
