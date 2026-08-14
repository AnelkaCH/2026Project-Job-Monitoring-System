# Maps the "ats" field in config.json to the right fetch function.
#
# Each ATS has its own module in this package. This registry is the only
# thing job_monitor.py needs to import - it never has to know which module
# an adapter lives in, just which key to look up.

from adapters.greenhouse import fetch_greenhouse
from adapters.lever import fetch_lever
from adapters.ashby import fetch_ashby
from adapters.smartrecruiters import fetch_smartrecruiters
from adapters.recruitee import fetch_recruitee
from adapters.workable import fetch_workable
from adapters.personio import fetch_personio
from adapters.workday import fetch_workday
from adapters.sap import fetch_sap

CONNECTORS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "workable": fetch_workable,
    "personio": fetch_personio,
    "workday": fetch_workday,
    "sap": fetch_sap,
}