__author__ = 'Eric Price'

from urllib2 import urlopen
from lxml.html import fromstring
import json

"""
Yes, it's a screen scraper script for assembling the instance type to arch map.
The thought is that updates to the web page will be minor ... maybe

You will need to have the following two dependencies to your environment: lxml, cssselect
"""

SELECT_TABLES = "div.informaltable"
SELECT_TABLE_DISCRIMINATOR = "table > thead > tr > th:nth-child(2)"

INSTANCE_TYPE_DELIMITER = ' | '

def get_page(url):
    html = urlopen(url).read()
    dom = fromstring(html)
    dom.make_links_absolute(url)
    return dom


def find_table(tbls, contains_text):
    return next(tbl for tbl in tbls if contains_text in tbl.cssselect(SELECT_TABLE_DISCRIMINATOR)[0].text)


def scrape_table_cells(tbl):
    tbl_cells = []
    for row in tbl.cssselect("tbody > tr"):
        content = row.cssselect('td:nth-child(2)')[0].text_content()
        tbl_cells.append(content)

    return tbl_cells


def get_instance_types(tbl):
    tbl_cells = scrape_table_cells(tbl)
    all_rows = INSTANCE_TYPE_DELIMITER.join(tbl_cells)
    instance_types_list = all_rows.split(INSTANCE_TYPE_DELIMITER)
    return instance_types_list


def build_type_to_arch_map(tbl, arch_type, map):
    for it in get_instance_types(tbl):
        map[it] = {'Arch': arch_type}

if __name__ == '__main__':
    dom = get_page("http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instance-types.html")

    tables = dom.cssselect(SELECT_TABLES)

    hvm_tbl = find_table(tables, "Current Generation")
    pv_tbl = find_table(tables, "Previous Generation")

    instance_type_to_arch_map = {}

    build_type_to_arch_map(hvm_tbl, 'HVM64', instance_type_to_arch_map)
    build_type_to_arch_map(pv_tbl, 'PV64', instance_type_to_arch_map)

    # Special case for 2x GPU instance with nvidia drivers
    instance_type_to_arch_map['g2.2xlarge']['Arch'] = 'HVMG2'

    json_str = json.dumps(instance_type_to_arch_map, indent=4, separators=(',', ': '), sort_keys=True)

    # Someone please explain to me why I have to do this repeatedly for all matches to be replaced!!
    import re
    for _ in range(7):
        json_str = re.sub(r": \{\n\s*([^\n]+)\n\s*\}", r": { \1 }", json_str, re.MULTILINE)
    print json_str
